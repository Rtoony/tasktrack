"""Report document settings — the letterhead printed on every Document.

Printable Documents (Thursday Packet, Meeting Prep, Competency Report, ...)
carry a letterhead masthead so the browser-printed PDF reads as an official
firm department document. The text is config, not hardcode: one JSON blob in
the existing `app_settings` key/value table (same pattern as
`telegram_link_code` in routes/admin.py and `secret_key` in app/db.py).

Key:   report_letterhead
Value: JSON {"company", "department", "preparer", "title_suffix"}

There is no admin edit UI yet (the admin panel has no generic app_settings
editor to hang it on) — edit via `set_report_letterhead()` from a shell/script
or update the row directly. Missing/invalid rows fall back to defaults, and an
empty company renders gracefully (the department becomes the top line — see
templates/partials/report_letterhead.html).
"""
from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from ..models import AppSetting

LOG = logging.getLogger("tasktrack.report_settings")

LETTERHEAD_KEY = "report_letterhead"

LETTERHEAD_DEFAULTS = {
    "company": "",
    "department": "CAD Department",
    "preparer": "Josh Patheal — CAD Technical Lead",
    "title_suffix": "",
}

_MAX_FIELD_LEN = 160


def get_report_letterhead(sess: Session) -> dict:
    """Stored letterhead merged over defaults. Never raises; never empty."""
    values = dict(LETTERHEAD_DEFAULTS)
    row = sess.get(AppSetting, LETTERHEAD_KEY)
    if row is None:
        return values
    try:
        stored = json.loads(row.value or "{}")
    except (TypeError, ValueError):
        LOG.warning(
            "app_settings.%s is not valid JSON — rendering letterhead defaults",
            LETTERHEAD_KEY,
        )
        return values
    if not isinstance(stored, dict):
        return values
    for key in LETTERHEAD_DEFAULTS:
        if key in stored and stored[key] is not None:
            values[key] = str(stored[key]).strip()[:_MAX_FIELD_LEN]
    return values


def set_report_letterhead(sess: Session, updates: dict) -> dict:
    """Merge `updates` (known keys only) over the stored letterhead and upsert.

    Caller commits. Returns the effective letterhead after the merge.
    """
    values = get_report_letterhead(sess)
    for key in LETTERHEAD_DEFAULTS:
        if key in updates and updates[key] is not None:
            values[key] = str(updates[key]).strip()[:_MAX_FIELD_LEN]
    payload = json.dumps(values, sort_keys=True)
    row = sess.get(AppSetting, LETTERHEAD_KEY)
    if row is None:
        sess.add(AppSetting(key=LETTERHEAD_KEY, value=payload))
    else:
        row.value = payload
    return values


__all__ = [
    "LETTERHEAD_KEY",
    "LETTERHEAD_DEFAULTS",
    "get_report_letterhead",
    "set_report_letterhead",
]
