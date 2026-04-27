#!/usr/bin/env python3
"""TaskTrack Email Intake poller.

Reads unread mail from an IMAP mailbox, extracts body + sender + subject, and
POSTs to `${TASKTRACK_URL}/api/v1/triage` with `commit=true` and `source=email` so
the task lands in TaskTrack flagged `needs_review`.

Designed to run as a short-lived systemd user service triggered by a timer
(every 5 minutes). Exits 0 when no messages are waiting; exits non-zero on
auth/connection failures so the timer surfaces the problem.

Environment:
  INTAKE_IMAP_HOST     required — IMAP server hostname
  INTAKE_IMAP_PORT     default 993
  INTAKE_IMAP_USER     required — mailbox login
  INTAKE_IMAP_PASS     required — mailbox password / app password
  INTAKE_IMAP_FOLDER   default "INBOX"
  INTAKE_IMAP_SSL      default "1" (set "0" to use STARTTLS on 143)
  INTAKE_MAX_MESSAGES  default 10
  TASKTRACK_URL        default "http://127.0.0.1:5050"
  TASKTRACK_TOKEN      required — matches TASKTRACK_TOKEN on the server
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import logging
import os
import sys
from email.message import EmailMessage

import requests

LOG = logging.getLogger("email_intake")


def _env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        raise SystemExit(f"missing required env var: {name}")
    return val


def _extract_body(msg: EmailMessage) -> str:
    """Prefer text/plain; fall back to stripped text/html."""
    if msg.is_multipart():
        plain_parts = []
        html_parts = []
        for part in msg.walk():
            if part.is_multipart():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_parts.append(part.get_content())
            elif ctype == "text/html":
                html_parts.append(part.get_content())
        if plain_parts:
            return "\n\n".join(plain_parts).strip()
        if html_parts:
            return _strip_html(html_parts[0])
        return ""
    ctype = msg.get_content_type()
    if ctype == "text/plain":
        return msg.get_content().strip()
    if ctype == "text/html":
        return _strip_html(msg.get_content())
    return ""


def _strip_html(html: str) -> str:
    import re
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _compose_intake_text(msg: EmailMessage) -> tuple[str, str]:
    """Return (raw_text_for_triage, sender_display)."""
    subject = (msg.get("Subject") or "").strip()
    from_raw = msg.get("From") or ""
    sender_name, sender_addr = email.utils.parseaddr(from_raw)
    sender_display = sender_name or sender_addr or from_raw.strip()
    body = _extract_body(msg)
    parts = []
    if subject:
        parts.append(f"Subject: {subject}")
    if sender_display:
        parts.append(f"From: {sender_display}" + (f" <{sender_addr}>" if sender_addr and sender_addr != sender_display else ""))
    if body:
        parts.append("")
        parts.append(body)
    return "\n".join(parts).strip(), sender_display


def _post_triage(url: str, token: str, text: str, requested_by: str) -> dict:
    payload = {
        "text": text,
        "commit": True,
        "source": "email",
        "requested_by": requested_by,
    }
    resp = requests.post(
        url.rstrip("/") + "/api/v1/triage",
        headers={"Content-Type": "application/json", "X-Token": token},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def _connect(host: str, port: int, use_ssl: bool, user: str, password: str, folder: str):
    cls = imaplib.IMAP4_SSL if use_ssl else imaplib.IMAP4
    conn = cls(host, port)
    if not use_ssl:
        conn.starttls()
    conn.login(user, password)
    typ, _ = conn.select(folder)
    if typ != "OK":
        raise RuntimeError(f"cannot select folder {folder!r}")
    return conn


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("INTAKE_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    host = _env("INTAKE_IMAP_HOST", required=True)
    user = _env("INTAKE_IMAP_USER", required=True)
    password = _env("INTAKE_IMAP_PASS", required=True)
    port = int(_env("INTAKE_IMAP_PORT", "993"))
    folder = _env("INTAKE_IMAP_FOLDER", "INBOX")
    use_ssl = _env("INTAKE_IMAP_SSL", "1") not in ("0", "false", "False")
    max_messages = int(_env("INTAKE_MAX_MESSAGES", "10"))

    tt_url = _env("TASKTRACK_URL", "http://127.0.0.1:5050")
    tt_token = _env("TASKTRACK_TOKEN", required=True)

    LOG.info("connecting to %s:%s as %s (ssl=%s, folder=%s)", host, port, user, use_ssl, folder)
    conn = _connect(host, port, use_ssl, user, password, folder)

    try:
        typ, raw_ids = conn.search(None, "UNSEEN")
        if typ != "OK":
            LOG.error("IMAP search failed: %s", typ)
            return 2

        ids = raw_ids[0].split() if raw_ids and raw_ids[0] else []
        if not ids:
            LOG.info("no new messages")
            return 0

        LOG.info("%d unread message(s) waiting", len(ids))
        processed = 0
        for msg_id in ids[:max_messages]:
            typ, data = conn.fetch(msg_id, "(RFC822)")
            if typ != "OK" or not data or not data[0]:
                LOG.warning("failed to fetch message id=%s", msg_id)
                continue
            raw = data[0][1]
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            text, sender = _compose_intake_text(msg)
            if not text:
                LOG.info("skipping empty message id=%s", msg_id)
                conn.store(msg_id, "+FLAGS", "\\Seen")
                continue
            try:
                result = _post_triage(tt_url, tt_token, text, sender)
            except Exception as exc:  # noqa: BLE001 — log and keep message unread
                LOG.error("triage POST failed for id=%s: %s", msg_id, exc)
                continue
            task_id = result.get("task_id")
            LOG.info("id=%s -> task_id=%s model=%s", msg_id, task_id, result.get("model"))
            conn.store(msg_id, "+FLAGS", "\\Seen")
            processed += 1

        LOG.info("processed %d message(s)", processed)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()


if __name__ == "__main__":
    sys.exit(main())
