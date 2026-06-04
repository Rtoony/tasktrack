"""Reports for OCR/paper/browser intake sources."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InboxItem, PersonalItem, ProjectWorkTask, TrainingTask, WorkTask

INTAKE_REPORT_TABLES = {
    "work_tasks": {
        "label": "CAD Dev",
        "model": WorkTask,
        "title": "title",
        "due": "due_date",
        "project": "project_number",
        "needs_review": "needs_review",
        "tab": "work",
    },
    "project_work_tasks": {
        "label": "Project Tasks",
        "model": ProjectWorkTask,
        "title": "title",
        "due": "due_at",
        "project": "project_number",
        "needs_review": "needs_review",
        "tab": "project",
    },
    "training_tasks": {
        "label": "Training",
        "model": TrainingTask,
        "title": "title",
        "due": "due_date",
        "project": "project_number",
        "needs_review": "needs_review",
        "tab": "training",
    },
    "personal_items": {
        "label": "Internal",
        "model": PersonalItem,
        "title": "title",
        "due": "due_date",
        "project": "",
        "needs_review": "needs_review",
        "tab": "personal_husband",
    },
    "inbox_items": {
        "label": "Triage Inbox",
        "model": InboxItem,
        "title": "title",
        "due": "due_date",
        "project": "",
        "needs_review": "status",
        "tab": "triage",
    },
}

DEFAULT_SOURCES = ["web-form", "paper-form", "remarkable-ocr"]
INTERNAL_CATEGORY_TABS = {
    "Follow-up": "personal_husband",
    "Meetings": "personal_father",
    "Office": "personal_house",
    "Assets": "personal_cars",
}
CSV_FIELDS = [
    "table", "label", "id", "title", "source", "status", "priority",
    "due", "project_number", "requester", "source_ref", "detail",
    "needs_review", "created_at", "record_url",
]

DETAIL_FIELDS = {
    "work_tasks": ["description", "clarifications_needed", "starter_note", "notes"],
    "project_work_tasks": [
        "task_description", "scope_notes", "progress_notes",
        "confirmation_notes", "completion_notes", "notes",
    ],
    "training_tasks": ["training_goals", "additional_context", "notes"],
    "personal_items": ["body", "source_ref"],
    "inbox_items": ["body", "source_ref"],
}

REQUESTER_FIELDS = {
    "work_tasks": ["requested_by"],
    "project_work_tasks": ["engineer"],
    "training_tasks": ["requested_by", "trainees"],
    "personal_items": ["created_by_name"],
    "inbox_items": ["created_by_name"],
}


def _first_text(row, fields: list[str]) -> str:
    for field in fields:
        value = (getattr(row, field, "") or "").strip()
        if value:
            return value
    return ""


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw).replace(" ", "T"))
    except (TypeError, ValueError):
        return None


def _clean_sources(raw) -> list[str]:
    if raw is None:
        return list(DEFAULT_SOURCES)
    if isinstance(raw, str):
        parts = raw.replace("\n", ",").split(",")
    else:
        parts = []
        for value in raw:
            parts.extend(str(value or "").replace("\n", ",").split(","))
    sources = []
    for part in parts:
        item = part.strip()[:32]
        if item and item not in sources:
            sources.append(item)
    return sources or list(DEFAULT_SOURCES)


def _row_payload(table: str, cfg: dict, row) -> dict:
    source = getattr(row, "source", "") or ""
    created = getattr(row, "created_at", "") or ""
    created_text = created.isoformat(sep=" ") if isinstance(created, datetime) else str(created or "")
    due_col = cfg.get("due") or ""
    project_col = cfg.get("project") or ""
    review_col = cfg.get("needs_review") or ""
    category = getattr(row, "category", "") if table == "personal_items" else ""
    tab = INTERNAL_CATEGORY_TABS.get(category, cfg.get("tab") or "triage")
    detail = _first_text(row, DETAIL_FIELDS.get(table, []))
    requester = _first_text(row, REQUESTER_FIELDS.get(table, []))
    source_ref = (getattr(row, "source_ref", "") or getattr(row, "request_reference", "") or "").strip()
    return {
        "table": table,
        "label": cfg.get("label") or table,
        "id": getattr(row, "id", None),
        "title": getattr(row, cfg.get("title") or "title", "") or f"#{getattr(row, 'id', '?')}",
        "source": source,
        "status": getattr(row, "status", "") or "",
        "priority": getattr(row, "priority", "") or "",
        "due": getattr(row, due_col, "") if due_col else "",
        "project_number": getattr(row, project_col, "") if project_col else "",
        "requester": requester,
        "source_ref": source_ref,
        "detail": detail[:500],
        "category": category,
        "needs_review": (
            (getattr(row, "status", "") not in {"Done", "Archived"})
            if table == "inbox_items"
            else bool(getattr(row, review_col, 0)) if review_col else False
        ),
        "created_at": created_text,
        "record_url": f"/?tab={tab}&record={getattr(row, 'id', '')}",
    }


def intake_source_report(sess: Session, *, sources=None, days: int = 30,
                         limit: int = 100, needs_review=None) -> dict:
    """Return a cross-table queue for scanned/OCR/browser intake records."""
    source_values = _clean_sources(sources)
    days = max(1, min(int(days or 30), 3650))
    limit = max(1, min(int(limit or 100), 500))
    since = datetime.now() - timedelta(days=days)
    rows: list[dict] = []

    for table, cfg in INTAKE_REPORT_TABLES.items():
        Model = cfg["model"]
        stmt = select(Model).where(Model.source.in_(source_values))
        for row in sess.scalars(stmt).all():
            created = _parse_dt(getattr(row, "created_at", None))
            if created is not None and created < since:
                continue
            payload = _row_payload(table, cfg, row)
            if needs_review is not None and payload["needs_review"] != bool(needs_review):
                continue
            rows.append(payload)

    rows.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    rows = rows[:limit]
    by_source = {source: 0 for source in source_values}
    by_table = {table: 0 for table in INTAKE_REPORT_TABLES}
    review_count = 0
    for row in rows:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
        by_table[row["table"]] = by_table.get(row["table"], 0) + 1
        if row.get("needs_review"):
            review_count += 1

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "filters": {
            "sources": source_values,
            "days": days,
            "limit": limit,
            "needs_review": needs_review,
        },
        "summary": {
            "count": len(rows),
            "needs_review_count": review_count,
            "by_source": by_source,
            "by_table": by_table,
        },
        "rows": rows,
    }


def intake_report_csv(packet: dict) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    for row in packet.get("rows", []):
        writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})
    return output.getvalue()


__all__ = ["CSV_FIELDS", "DEFAULT_SOURCES", "intake_report_csv", "intake_source_report"]
