"""Send a TaskTrack attachment to RToony's reMarkable tablet (feedback #55).

Flow: pull the attachment bytes from MinIO -> render image screenshots to PDF
(the tablet's native doc type; PDFs pass straight through) -> hand the PDF to the
local rmfakecloud *device* process via the inkpress ``RmCloud`` client, into a
"TaskTrack" folder so everything sent from here lands in one place on the tablet.

Design notes
------------
* **Reuse, don't reimplement.** The hard-won rmfakecloud ``/ui/api`` contract
  (login JWT, multipart upload, the sync15 blob-tree that the *server* builds for
  us) lives in ``~/projects/remarkable-templates/inkpress/rmcloud.py``. We import
  that client rather than re-deriving the fragile format. The path is
  env-overridable (``TASKTRACK_RMCLOUD_PATH``) and an import failure degrades to a
  clean 503 — it never crashes a request or the app at boot.
* **Zero-disk creds.** The client reads the ``Nexus - rmfakecloud`` vault item
  (or ``RMCLOUD_USER`` / ``RMCLOUD_PASS`` env). We never read or write a secret here.
* **Device process, instant sync.** Default target is ``https://127.0.0.1:443``
  (the process the tablet's notification websocket is attached to). Uploading via
  the :3030 browser process lands at root and the tablet only sees it on its next
  poll. ``RMCLOUD_URL`` overrides if that ever changes.
"""
from __future__ import annotations

import io
import logging
import os
import sys
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Attachment
from . import attachments as att_svc
from .audit import log_activity

LOG = logging.getLogger("tasktrack.remarkable")

# Where the reusable rmfakecloud client lives. Env-overridable so a future move
# of the inkpress project doesn't silently break this feature — it surfaces as a
# clean "client unavailable" 503 instead.
DEFAULT_RMCLOUD_PATH = os.path.expanduser("~/projects/remarkable-templates/inkpress")

# Folder on the tablet that everything sent from TaskTrack is filed under.
REMARKABLE_FOLDER = os.environ.get("TASKTRACK_REMARKABLE_FOLDER", "TaskTrack")

# reMarkable renders PDF natively; images are converted first. Everything else
# (DWG/DXF/XLSX/DOCX) has no faithful on-tablet rendering, so we reject it with a
# clear message rather than push a file the device can't open.
_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/jpg"}
_PDF_CONTENT_TYPES = {"application/pdf"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
_PDF_EXTS = {".pdf"}


class RemarkableError(Exception):
    """Client-visible failure sending to the tablet (carries an HTTP status)."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _load_rmcloud():
    """Import the inkpress RmCloud client, adding its dir to sys.path.

    Raises RemarkableError(503) if the client can't be found/imported so the
    route returns a clean "not configured" rather than a 500.
    """
    path = os.environ.get("TASKTRACK_RMCLOUD_PATH", DEFAULT_RMCLOUD_PATH)
    if path and path not in sys.path:
        sys.path.insert(0, path)
    try:
        from rmcloud import RmCloud, RmCloudError  # type: ignore
    except Exception as exc:  # ImportError, or a syntax error in a moved file
        LOG.warning("reMarkable client unavailable at %s: %s", path, exc)
        raise RemarkableError(
            "reMarkable client is not available on this host "
            f"(looked in {path}).",
            status_code=503,
        ) from exc
    return RmCloud, RmCloudError


def _classify(att: Attachment) -> str:
    """'pdf' | 'image' — or raise RemarkableError(415) for unsupported types."""
    ct = (att.content_type or "").lower().split(";")[0].strip()
    ext = os.path.splitext(att.filename or "")[1].lower()
    if ct in _PDF_CONTENT_TYPES or ext in _PDF_EXTS:
        return "pdf"
    if ct in _IMAGE_CONTENT_TYPES or ext in _IMAGE_EXTS:
        return "image"
    raise RemarkableError(
        "Only PDF and image (PNG/JPG) attachments can be sent to the "
        "reMarkable — the tablet can't render this file type.",
        status_code=415,
    )


def attachment_to_pdf_bytes(blob: bytes, kind: str) -> bytes:
    """Return PDF bytes for the attachment: pass PDFs through, render images.

    `kind` is the value from `_classify` ('pdf' | 'image'). Pure function — no
    network, no DB — so it unit-tests without MinIO or the tablet.
    """
    if kind == "pdf":
        if not blob.startswith(b"%PDF-"):
            raise RemarkableError("Attachment is not a valid PDF.", status_code=422)
        return blob
    # image -> single-page PDF. Pillow flattens to RGB (drops alpha, which PDF
    # can't carry) so screenshots with transparency don't error out.
    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - Pillow is a hard dep here
        raise RemarkableError("Image conversion is unavailable (Pillow missing).",
                              status_code=503) from exc
    try:
        img = Image.open(io.BytesIO(blob))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PDF", resolution=150.0)
        return out.getvalue()
    except RemarkableError:
        raise
    except Exception as exc:
        raise RemarkableError("Could not render the image to PDF.",
                              status_code=422) from exc


def _doc_name(att: Attachment) -> str:
    """On-tablet document name: filename stem, tagged with the source record so
    several screenshots from different tickets don't collide as 'image'."""
    stem = os.path.splitext(att.filename or "attachment")[0] or "attachment"
    return f"{stem} · {att.table_name}#{att.record_id}"


def send_attachment(sess: Session, att: Attachment, *,
                    folder: str = REMARKABLE_FOLDER) -> dict:
    """Convert + upload one attachment to the tablet. Returns a small result dict.

    Raises RemarkableError (with .status_code) on any client-visible failure.
    Records an activity_log row on success so the send is auditable like every
    other record mutation. The caller owns commit/rollback.
    """
    RmCloud, RmCloudError = _load_rmcloud()
    kind = _classify(att)

    try:
        blob = att_svc.download_bytes(att)
    except att_svc.AttachmentError as exc:
        raise RemarkableError(f"Could not fetch the attachment: {exc}",
                              status_code=exc.status_code) from exc

    pdf_bytes = attachment_to_pdf_bytes(blob, kind)
    name = _doc_name(att)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(prefix="tt-rm-", suffix=".pdf",
                                         delete=False) as fh:
            fh.write(pdf_bytes)
            tmp_path = fh.name
        try:
            client = RmCloud().login()
            parent = client.ensure_folder(folder) if folder else ""
            result = client.upload(tmp_path, name=name, parent=parent)
        except RmCloudError as exc:
            # vault-locked / login / upload failure -> client-visible, not a 500.
            msg = str(exc)
            status = 503 if "vault locked" in msg.lower() else 502
            raise RemarkableError(f"reMarkable upload failed: {msg}",
                                  status_code=status) from exc
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

    log_activity(sess, att.table_name, att.record_id, "sent_to_remarkable",
                 new=f"{name} -> {folder or 'root'}")
    return {
        "ok": True,
        "doc_id": result.get("id"),
        "name": result.get("name", name),
        "folder": folder,
        "moved": result.get("moved"),
    }
