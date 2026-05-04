"""Attachment storage service — TaskTrack ↔ MinIO.

Polymorphic on (table_name, record_id) the same way comments and
activity_log are. Object keys are bucket-relative and include the
sha256 so re-uploading the same bytes for the same record is a no-op:

    <table>/<record_id>/<sha256>-<safe-filename>

Service layer concerns:
  - 50 MB hard cap (overridable via ATTACHMENT_MAX_BYTES env)
  - MIME / extension whitelist (PDF, DWG, DXF, PNG, JPG, XLSX, DOCX)
  - sha256 dedupe — same bytes twice on the same record returns the
    existing Attachment row
  - 5-minute presigned URLs for downloads — Flask doesn't proxy bytes
  - audit_log row on every upload / delete

Routing concerns (table validation, auth, JSON shape) live in
routes/attachments.py.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import re
from dataclasses import dataclass
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError
from flask import current_app, session as flask_session
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.datastructures import FileStorage

from ..models import Attachment
from .audit import log_activity

LOG = logging.getLogger("tasktrack.attachments")

DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB

# (extension, mimetype) — both must match. mimetypes.guess_type returns
# None for DWG/DXF on most stdlib builds, so we accept octet-stream for
# those two when the extension is right.
ALLOWED_EXTENSIONS = {
    ".pdf":  {"application/pdf"},
    ".png":  {"image/png"},
    ".jpg":  {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".dwg":  {"image/vnd.dwg", "application/acad", "application/x-acad",
              "application/autocad_dwg", "application/dwg",
              "application/x-dwg", "application/octet-stream"},
    ".dxf":  {"image/vnd.dxf", "application/dxf", "application/x-dxf",
              "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "application/vnd.ms-excel", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/msword", "application/octet-stream"},
}

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class AttachmentError(Exception):
    """Raised for client-visible upload failures (bad MIME, oversized, etc.)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class _MinioConfig:
    endpoint: str
    access_key: str
    secret_key: str
    bucket: str
    region: str


def _config() -> _MinioConfig:
    cfg = _MinioConfig(
        endpoint=os.environ.get("MINIO_ENDPOINT", "http://127.0.0.1:9000"),
        access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
        secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
        bucket=os.environ.get("MINIO_BUCKET", "tasktrack-attachments"),
        region=os.environ.get("MINIO_REGION", "us-east-1"),
    )
    if not cfg.access_key or not cfg.secret_key:
        raise AttachmentError(
            "Attachment storage is not configured (MINIO_ACCESS_KEY / "
            "MINIO_SECRET_KEY missing).",
            status_code=503,
        )
    return cfg


def _client():
    cfg = _config()
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.access_key,
        aws_secret_access_key=cfg.secret_key,
        region_name=cfg.region,
        config=BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def _max_bytes() -> int:
    raw = os.environ.get("ATTACHMENT_MAX_BYTES")
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_BYTES


def _safe_filename(name: str) -> str:
    name = os.path.basename(name or "file")
    name = _SAFE_FILENAME_RE.sub("_", name).strip("._-")
    return name or "file"


def _validate_filetype(filename: str, declared_mime: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    allowed = ALLOWED_EXTENSIONS.get(ext)
    if not allowed:
        raise AttachmentError(
            f"File type '{ext or '(none)'}' is not allowed. "
            "Allowed: PDF, DWG, DXF, PNG, JPG/JPEG, XLSX, DOCX."
        )
    declared = (declared_mime or "").lower().split(";")[0].strip()
    guessed = (mimetypes.guess_type(filename)[0] or "").lower()
    candidate = declared or guessed or "application/octet-stream"
    if candidate not in allowed:
        raise AttachmentError(
            f"Declared content type '{candidate}' does not match extension '{ext}'."
        )
    return candidate


def _hash_and_size(stream: BinaryIO, max_bytes: int) -> tuple[str, int, bytes]:
    """Read the whole stream into memory while computing sha256 + size.

    50 MB cap is small enough that holding the bytes is fine and lets us
    stream-upload to MinIO without re-reading from disk. Raises
    AttachmentError if size exceeds max_bytes.
    """
    h = hashlib.sha256()
    chunks = []
    total = 0
    while True:
        chunk = stream.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise AttachmentError(
                f"File exceeds the {max_bytes // (1024 * 1024)} MB limit."
            )
        h.update(chunk)
        chunks.append(chunk)
    return h.hexdigest(), total, b"".join(chunks)


def list_for(sess: Session, table: str, record_id: int) -> list[Attachment]:
    return list(
        sess.scalars(
            select(Attachment)
            .where(Attachment.table_name == table, Attachment.record_id == record_id)
            .order_by(Attachment.uploaded_at.asc())
        )
    )


def upload(sess: Session, file_storage: FileStorage, table: str,
           record_id: int) -> Attachment:
    if file_storage is None or not getattr(file_storage, "filename", None):
        raise AttachmentError("No file uploaded.")
    safe_name = _safe_filename(file_storage.filename)
    content_type = _validate_filetype(safe_name, file_storage.mimetype or "")

    sha, size, blob = _hash_and_size(file_storage.stream, _max_bytes())

    existing = sess.scalar(
        select(Attachment).where(
            Attachment.table_name == table,
            Attachment.record_id == record_id,
            Attachment.sha256 == sha,
        )
    )
    if existing is not None:
        return existing

    cfg = _config()
    object_key = f"{table}/{record_id}/{sha}-{safe_name}"

    s3 = _client()
    try:
        s3.put_object(
            Bucket=cfg.bucket,
            Key=object_key,
            Body=blob,
            ContentType=content_type,
            ContentLength=size,
            Metadata={
                "tasktrack-table": table,
                "tasktrack-record-id": str(record_id),
                "tasktrack-uploader": flask_session.get("user_name", "")[:128],
            },
        )
    except ClientError as e:
        LOG.exception("MinIO put_object failed key=%s err=%s", object_key, e)
        raise AttachmentError("Storage backend rejected the upload.", status_code=502)

    att = Attachment(
        table_name=table,
        record_id=record_id,
        object_key=object_key,
        filename=safe_name,
        content_type=content_type,
        size_bytes=size,
        sha256=sha,
        uploaded_by_user_id=flask_session.get("user_id"),
        uploaded_by_name=flask_session.get("user_name", ""),
    )
    sess.add(att)
    sess.flush()
    log_activity(sess, table, record_id, "attachment_added",
                 new=f"{safe_name} ({size} bytes)")
    return att


def delete_attachment(sess: Session, attachment_id: int) -> Attachment:
    att = sess.get(Attachment, attachment_id)
    if att is None:
        raise AttachmentError("Attachment not found.", status_code=404)

    cfg = _config()
    s3 = _client()
    try:
        s3.delete_object(Bucket=cfg.bucket, Key=att.object_key)
    except ClientError as e:
        LOG.warning("MinIO delete_object failed key=%s err=%s — proceeding "
                    "with DB row removal", att.object_key, e)

    log_activity(sess, att.table_name, att.record_id, "attachment_removed",
                 new=att.filename)
    sess.delete(att)
    sess.flush()
    return att


def presigned_download_url(att: Attachment, ttl_seconds: int = 300) -> str:
    cfg = _config()
    s3 = _client()
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": cfg.bucket,
                "Key": att.object_key,
                "ResponseContentDisposition": f'attachment; filename="{att.filename}"',
            },
            ExpiresIn=ttl_seconds,
        )
    except ClientError as e:
        LOG.exception("MinIO presign failed key=%s err=%s", att.object_key, e)
        raise AttachmentError("Could not generate download URL.", status_code=502)
