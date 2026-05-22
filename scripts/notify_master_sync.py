#!/usr/bin/env python3
"""Build + send the Telegram digest for a master-list sync run.

Reads a JSON report on stdin (the one written by
import_projects_from_master.py via --report-json, then forwarded by
sync_master_if_changed.py). Formats a compact human digest and POSTs
to Telegram's sendMessage API using:

  TELEGRAM_BOT_TOKEN        — Claude's bot token (vault: Nexus - Messaging)
  TELEGRAM_CLAUDE_CHAT_ID   — operator's chat id (vault: Nexus - Messaging)

Both env vars are required at send time; if either is missing the
script logs a warning and exits 0 so a missing-secret config doesn't
fail the systemd unit. The sync itself already succeeded by the time
we get here — the digest is informational.

Run standalone for testing:
  cat /tmp/master-sync-report.json | python3 scripts/notify_master_sync.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime

LOG = logging.getLogger("tasktrack.notify_master_sync")

_TG_API = "https://api.telegram.org/bot{token}/sendMessage"

# Maximum sample size per category in the digest. The full lists already
# live in /tmp/master-sync-report.json (transient) and the compact state
# file (persistent) — the digest is the eyeballed summary.
_SAMPLE_N = 8


def _format_sample(items: list[str], total: int) -> str:
    if not items:
        return ""
    shown = items[:_SAMPLE_N]
    line = ", ".join(shown)
    if total > len(shown):
        line += f", +{total - len(shown)} more"
    return f"  ({line})"


def build_digest(report: dict) -> str:
    """Render the Telegram message body. Telegram supports plain text
    up to 4096 chars; this digest is well under that even for big
    sync runs."""
    when_iso = report.get("run_at", "")
    try:
        when = datetime.fromisoformat(when_iso).strftime("%Y-%m-%d %H:%M UTC")
    except (ValueError, TypeError):
        when = when_iso or "(unknown time)"

    created = report.get("created", [])
    updated = report.get("updated", [])
    vanished = report.get("vanished", [])
    returned = report.get("returned", [])
    orphans_created = report.get("orphan_projects_created", 0)

    lines = [f"📋 TaskTrack master-list sync — {when}"]

    if created:
        lines.append(f"✔ {len(created)} new project(s)"
                     + _format_sample(created, len(created)))
    if updated:
        lines.append(f"✔ {len(updated)} metadata update(s)"
                     + _format_sample(updated, len(updated)))
    if vanished:
        lines.append(f"⚠ {len(vanished)} vanished → dormant"
                     + _format_sample(vanished, len(vanished)))
    if returned:
        lines.append(f"↩ {len(returned)} returned from vanished"
                     + _format_sample(returned, len(returned)))
    if orphans_created:
        lines.append(f"➕ {orphans_created} KMZ-only orphan(s) auto-created "
                     "(no master-list row yet)")

    sites = report.get("sites_inserted", 0)
    lines.append(f"📍 {sites} site pin(s) rebuilt across "
                 f"{report.get('kmz_unique_numbers', 0)} unique numbers")

    if len(lines) == 2:  # only the header + the sites footer
        lines.insert(1, "(No project-level changes — pins refreshed only.)")

    return "\n".join(lines)


def send_telegram(text: str, *, token: str, chat_id: str) -> bool:
    """POST sendMessage; return True on success, False otherwise. Never
    raises — the caller should treat the digest as best-effort."""
    payload = urllib.parse.urlencode({
        "chat_id":     chat_id,
        "text":        text,
        "parse_mode":  "",            # plain text; no MarkdownV2 escaping needed
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    url = _TG_API.format(token=token)
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            data = json.loads(body) if body else {}
            if data.get("ok"):
                return True
            LOG.warning("telegram non-ok response: %s", body[:300])
            return False
    except Exception as exc:  # noqa: BLE001
        LOG.warning("telegram POST failed: %s", exc)
        return False


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("TASKTRACK_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    raw = sys.stdin.read()
    if not raw.strip():
        LOG.warning("empty stdin; nothing to send")
        return 0
    try:
        report = json.loads(raw)
    except json.JSONDecodeError as exc:
        LOG.error("could not parse report JSON: %s", exc)
        return 1

    text = build_digest(report)
    print(text)  # also emit to stdout so journald captures the digest

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CLAUDE_CHAT_ID", "").strip()
    if not token or not chat_id:
        LOG.warning(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CLAUDE_CHAT_ID not set; "
            "digest printed to stdout only (operator will not see it on phone)"
        )
        return 0

    sent = send_telegram(text, token=token, chat_id=chat_id)
    return 0 if sent else 0  # never fail the wrapper on notify-only issues


if __name__ == "__main__":
    sys.exit(main())
