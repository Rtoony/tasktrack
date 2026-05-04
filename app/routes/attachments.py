"""Attachments REST API.

Surface, all under /api/v1/attachments:
  POST   /<table>/<record_id>      multipart upload, single 'file' field
  GET    /<table>/<record_id>      list attachments on a record
  GET    /<id>/download            302 to a 5-minute presigned MinIO URL
  DELETE /<id>                     remove from MinIO + DB

Validation:
  - <table> must be in ALLOWED_TABLES.
  - The parent record must exist (404 otherwise).
  - File-level rules (size, MIME, dedupe) live in services.attachments.

The download endpoint redirects to a presigned URL rather than streaming
through Flask. Pros: no double-bandwidth, no worker tied up on a slow
client. Cons: the URL is valid for 5 minutes from generation. That's
acceptable for an internal LAN tool; revisit when remote/VPN access
shows up.
"""
from __future__ import annotations

from flask import Blueprint, Response, jsonify, redirect, request
from sqlalchemy import select

from ..auth import login_required
from ..config import ALLOWED_TABLES
from ..db import get_session
from ..models import Attachment, to_dict
from ..services import attachments as att_svc
from ..services.tickets import TABLE_MODELS

bp = Blueprint("attachments", __name__)


def _record_exists(sess, table: str, record_id: int) -> bool:
    Model = TABLE_MODELS.get(table)
    if Model is None:
        return False
    return sess.scalar(select(Model.id).where(Model.id == record_id)) is not None


def _att_dict(att: Attachment) -> dict:
    out = to_dict(att) or {}
    # Convenience field for the UI — full URL the client can hit to get
    # the redirect to MinIO. Keeps frontend code from having to assemble it.
    out["download_url"] = f"/api/v1/attachments/{att.id}/download"
    return out


@bp.route("/api/v1/attachments/<table>/<int:record_id>", methods=["GET"])
@login_required
def list_attachments(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    if not _record_exists(sess, table, record_id):
        return jsonify({"error": "Record not found"}), 404
    rows = att_svc.list_for(sess, table, record_id)
    return jsonify([_att_dict(a) for a in rows])


@bp.route("/api/v1/attachments/<table>/<int:record_id>", methods=["POST"])
@login_required
def upload_attachment(table, record_id):
    if table not in ALLOWED_TABLES:
        return jsonify({"error": "Invalid table"}), 400
    sess = get_session()
    if not _record_exists(sess, table, record_id):
        return jsonify({"error": "Record not found"}), 404

    file_storage = request.files.get("file")
    if file_storage is None:
        return jsonify({"error": "Missing 'file' field in multipart body."}), 400

    try:
        att = att_svc.upload(sess, file_storage, table, record_id)
    except att_svc.AttachmentError as e:
        return jsonify({"error": str(e)}), e.status_code

    sess.commit()
    sess.refresh(att)
    return jsonify(_att_dict(att)), 201


@bp.route("/api/v1/attachments/<int:attachment_id>/download", methods=["GET"])
@login_required
def download_attachment(attachment_id):
    sess = get_session()
    att = sess.get(Attachment, attachment_id)
    if att is None:
        return jsonify({"error": "Attachment not found"}), 404
    try:
        url = att_svc.presigned_download_url(att)
    except att_svc.AttachmentError as e:
        return jsonify({"error": str(e)}), e.status_code
    return redirect(url, code=302)


@bp.route("/api/v1/attachments/<int:attachment_id>", methods=["DELETE"])
@login_required
def delete_attachment(attachment_id):
    sess = get_session()
    try:
        att_svc.delete_attachment(sess, attachment_id)
    except att_svc.AttachmentError as e:
        return jsonify({"error": str(e)}), e.status_code
    sess.commit()
    return Response(status=204)
