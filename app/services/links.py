"""Per-record hyperlinks service.

Polymorphic on (table_name, record_id) like attachments / comments. The
useful trick over a plain URL field is the **smart-link recognizer** —
when the URL matches a known Nexus-suite app or a common dev surface,
we derive a friendlier label automatically and tag a `source_kind` so
the UI can show an icon / colour.

Recognizers are deliberately stupid (regex on host + path). Add new
ones by appending to RECOGNIZERS — each entry is (regex, source_kind,
label_factory).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlparse

from flask import session as flask_session
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Link
from .audit import log_activity


class LinkError(Exception):
    """Client-visible link errors (bad URL, missing field, etc.)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class _Recognized:
    source_kind: str
    label: str


# (compiled regex against the full URL, source_kind, label_factory(match) -> str)
_LabelFn = Callable[[re.Match], str]
RECOGNIZERS: list[tuple[re.Pattern, str, _LabelFn]] = [
    # Nexus suite — roonytoony.dev subdomains.
    (re.compile(r"^https?://paperless\.roonytoony\.dev/documents/(\d+)", re.I),
     "paperless",
     lambda m: f"Paperless doc #{m.group(1)}"),
    (re.compile(r"^https?://portal\.roonytoony\.dev/calendar(?:/(\d{4}-\d{2}-\d{2}))?", re.I),
     "calendar",
     lambda m: f"Calendar — {m.group(1)}" if m.group(1) else "Calendar"),
    (re.compile(r"^https?://portal\.roonytoony\.dev/?(.*)", re.I),
     "portal",
     lambda m: f"Nexus Portal{(' — ' + m.group(1)) if m.group(1) else ''}"),
    (re.compile(r"^https?://prowlarr\.roonytoony\.dev", re.I),
     "prowlarr", lambda m: "Prowlarr"),
    (re.compile(r"^https?://(audio|movies|tv)\.roonytoony\.dev", re.I),
     "media", lambda m: m.group(1).capitalize()),

    # Dev surfaces.
    (re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)", re.I),
     "github_pr",
     lambda m: f"PR {m.group(1)}/{m.group(2)}#{m.group(3)}"),
    (re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/issues/(\d+)", re.I),
     "github_issue",
     lambda m: f"Issue {m.group(1)}/{m.group(2)}#{m.group(3)}"),
    (re.compile(r"^https?://github\.com/([^/]+)/([^/]+)", re.I),
     "github_repo",
     lambda m: f"GitHub {m.group(1)}/{m.group(2)}"),

    # Telegram.
    (re.compile(r"^https?://t\.me/([^/]+)/(\d+)", re.I),
     "telegram",
     lambda m: f"Telegram @{m.group(1)} msg {m.group(2)}"),
    (re.compile(r"^https?://t\.me/([^/]+)", re.I),
     "telegram",
     lambda m: f"Telegram @{m.group(1)}"),
]


def _recognize(url: str) -> _Recognized:
    for pattern, kind, factory in RECOGNIZERS:
        m = pattern.match(url)
        if m:
            try:
                return _Recognized(source_kind=kind, label=factory(m))
            except Exception:  # noqa: BLE001
                # A faulty recognizer shouldn't block the link from being saved.
                continue
    # Fallback: host + first path segment.
    try:
        parts = urlparse(url)
        host = parts.netloc or url
        suffix = (parts.path.rstrip("/").split("/")[-1] or "").strip()
        label = f"{host}/{suffix}" if suffix else host
    except Exception:  # noqa: BLE001
        label = url
    return _Recognized(source_kind="generic", label=label)


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        raise LinkError("URL is required.")
    if len(url) > 2048:
        raise LinkError("URL exceeds 2048 characters.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise LinkError("Only http and https URLs are accepted.")
    if not parsed.netloc:
        raise LinkError("URL is missing a host.")
    return url


def list_for(sess: Session, table: str, record_id: int) -> list[Link]:
    return list(
        sess.scalars(
            select(Link)
            .where(Link.table_name == table, Link.record_id == record_id)
            .order_by(Link.created_at.asc())
        )
    )


def add_link(sess: Session, table: str, record_id: int, url: str,
             label: Optional[str] = None) -> Link:
    url = _validate_url(url)

    # Dedupe: same record + same URL → return existing.
    existing = sess.scalar(
        select(Link).where(
            Link.table_name == table,
            Link.record_id == record_id,
            Link.url == url,
        )
    )
    if existing is not None:
        return existing

    recognized = _recognize(url)
    final_label = (label or "").strip() or recognized.label

    link = Link(
        table_name=table,
        record_id=record_id,
        url=url,
        label=final_label,
        source_kind=recognized.source_kind,
        added_by_user_id=flask_session.get("user_id"),
        added_by_name=flask_session.get("user_name", ""),
    )
    sess.add(link)
    sess.flush()
    log_activity(sess, table, record_id, "link_added", new=final_label[:80])
    return link


def delete_link(sess: Session, link_id: int) -> Link:
    link = sess.get(Link, link_id)
    if link is None:
        raise LinkError("Link not found.", status_code=404)
    log_activity(sess, link.table_name, link.record_id, "link_removed", new=link.label)
    sess.delete(link)
    sess.flush()
    return link
