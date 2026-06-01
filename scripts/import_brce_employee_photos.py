#!/usr/bin/env python3
"""Import public BRCE team thumbnails into TaskTrack employees.

Downloads the public staff thumbnail image from https://www.brce.com/team-members,
stores it under static/img/employees/, and writes the local path/source URL to
employees.photo_path/photo_source_url.
"""
from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "tracker.db"
PHOTO_DIR = ROOT / "static" / "img" / "employees"
TEAM_URL = "https://www.brce.com/team-members"
USER_AGENT = "TaskTrack employee photo importer/1.0"

CARD_RE = re.compile(
    r'background-image:url\(&quot;(?P<url>[^&]+)&quot;\).*?'
    r'<div class="avatar-name">(?P<name>.*?)</div>'
    r'<div class="avatar-type">(?P<title>.*?)</div>',
    re.S,
)
CREDENTIAL_RE = re.compile(
    r"\b(p\.?e\.?|e\.?i\.?t\.?|p\.?l\.?s\.?|l\.?s\.?i\.?t\.?|leed\s+ap)\b",
    re.I,
)


def fetch_text(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_bytes(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=30) as resp:
        return resp.read()


def strip_accents(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(ch)
    )


def normalize_name(value: str) -> str:
    value = html.unescape(value or "")
    value = strip_accents(value).lower()
    value = CREDENTIAL_RE.sub(" ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    value = strip_accents(html.unescape(value or "")).lower()
    value = CREDENTIAL_RE.sub(" ", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"-+", "-", value).strip("-") or "employee"


def image_ext(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        return suffix
    return ".jpg"


def parse_team(html_text: str) -> list[dict]:
    seen = set()
    rows = []
    for match in CARD_RE.finditer(html_text):
        name = html.unescape(re.sub(r"<.*?>", "", match.group("name"))).strip()
        title = html.unescape(re.sub(r"<.*?>", "", match.group("title"))).strip()
        url = html.unescape(match.group("url")).strip()
        key = normalize_name(name)
        if not name or not url or key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "title": title, "url": url, "key": key})
    return rows


def employees(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "select id, display_name, title from employees where active=1 order by display_name"
    ).fetchall()
    return {normalize_name(row["display_name"]): row for row in rows}


def import_photos(*, db_path: Path, team_url: str, dry_run: bool = False) -> int:
    html_text = fetch_text(team_url)
    team_rows = parse_team(html_text)
    if not team_rows:
        raise RuntimeError("No team rows parsed from BRCE page")

    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    emp_by_key = employees(conn)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    matched = 0
    unmatched_site = []

    for person in team_rows:
        emp = emp_by_key.get(person["key"])
        if emp is None:
            unmatched_site.append(person["name"])
            continue
        filename = f"{emp['id']:03d}-{slugify(emp['display_name'])}{image_ext(person['url'])}"
        local_path = PHOTO_DIR / filename
        web_path = f"/static/img/employees/{filename}"
        if not dry_run:
            local_path.write_bytes(fetch_bytes(person["url"]))
            conn.execute(
                """
                update employees
                   set photo_path = ?,
                       photo_source_url = ?,
                       photo_updated_at = ?,
                       updated_at = CURRENT_TIMESTAMP
                 where id = ?
                """,
                (web_path, person["url"], now, emp["id"]),
            )
        matched += 1
        print(f"matched: {emp['display_name']} <- {person['name']} ({web_path})")

    if not dry_run:
        conn.commit()
    missing_local = sorted(set(emp_by_key) - {row["key"] for row in team_rows})
    print(f"summary: matched={matched} site_rows={len(team_rows)} db_active={len(emp_by_key)} dry_run={dry_run}")
    if unmatched_site:
        print("site_unmatched:", ", ".join(unmatched_site))
    if missing_local:
        print("db_unmatched:", ", ".join(missing_local))
    conn.close()
    return 0 if matched else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--url", default=TEAM_URL)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return import_photos(db_path=Path(args.db), team_url=args.url, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
