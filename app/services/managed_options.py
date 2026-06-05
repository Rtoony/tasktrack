"""Managed dropdown/category option registry."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import SKILL_CATEGORY_DEFAULTS
from ..models import ManagedOption, ManagedOptionSet, SkillCategory
from ..services.competency import seed_default_categories

_KEY_RE = re.compile(r"[^a-z0-9_]+")


def now_utc_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def normalize_set_key(value: str) -> str:
    key = _KEY_RE.sub("_", (value or "").strip().lower()).strip("_")
    return re.sub(r"_+", "_", key)


def _meta(data: dict[str, Any] | None = None) -> str:
    return json.dumps(data or {}, sort_keys=True)


def _boolish(value: Any, default: bool = True) -> int:
    if value is None:
        return 1 if default else 0
    if isinstance(value, str):
        return 1 if value.strip().lower() in {"1", "true", "yes", "on"} else 0
    return 1 if bool(value) else 0


def _intish(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


OPTION_TONES = {"neutral", "info", "success", "warning", "danger", "muted"}
_META_BOOL_DEFAULTS = {
    "is_default": False,
    "is_terminal": False,
    "counts_as_open": True,
}


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _bool_meta(value: Any, *, default: bool = False) -> bool:
    return bool(_boolish(value, default=default))


def _tone(value: Any) -> str:
    tone = str(value or "").strip().lower()
    return tone if tone in OPTION_TONES else "neutral"


def _normalized_metadata(data: dict[str, Any] | None = None,
                         existing: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = dict(existing or {})
    if isinstance(data, dict):
        metadata.update(data)
    for key, default in _META_BOOL_DEFAULTS.items():
        if key in metadata:
            metadata[key] = _bool_meta(metadata.get(key), default=default)
    if "tone" in metadata:
        metadata["tone"] = _tone(metadata.get("tone"))
    return metadata


def _option_metadata_from_data(data: dict[str, Any], *, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    incoming = data.get("metadata") if isinstance(data.get("metadata"), dict) else None
    metadata = _normalized_metadata(incoming, existing=existing)
    for key, default in _META_BOOL_DEFAULTS.items():
        if key in data:
            metadata[key] = _bool_meta(data.get(key), default=default)
    if "tone" in data:
        metadata["tone"] = _tone(data.get("tone"))
    return metadata


def _payload_metadata(raw: str | None) -> dict[str, Any]:
    return _normalized_metadata(_json_dict(raw))


def _clear_other_defaults(sess: Session, option: ManagedOption) -> None:
    metadata = _payload_metadata(option.metadata_json)
    if not _bool_meta(metadata.get("is_default"), default=False):
        return
    rows = sess.scalars(select(ManagedOption).where(
        ManagedOption.set_id == option.set_id,
        ManagedOption.id != option.id,
    )).all()
    for row in rows:
        row_meta = _payload_metadata(row.metadata_json)
        if _bool_meta(row_meta.get("is_default"), default=False):
            row_meta["is_default"] = False
            row.metadata_json = _meta(row_meta)
            row.updated_at = now_utc_naive()


def _option(value: str, label: str | None = None, order: int = 0,
            description: str = "", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "value": value,
        "label": label or value,
        "description": description,
        "display_order": order,
        "metadata_json": _meta(metadata),
        "is_placeholder": 1,
    }


DEFAULT_OPTION_SETS: list[dict[str, Any]] = [
    {
        "key": "cad_skill_area",
        "label": "CAD Skill Areas",
        "surface": "CAD Dev, Training, Capabilities, Intake",
        "description": "Office-editable CAD/Civil skill vocabulary used on task and incident forms.",
        "options": [
            _option(item["name"], order=item["display_order"], metadata={"skill_category_slug": item["slug"]})
            for item in SKILL_CATEGORY_DEFAULTS
        ] + [_option("Other", order=990)],
    },
    {
        "key": "training_skill_area",
        "label": "Training Skill Areas",
        "surface": "Training, Intake",
        "description": "Training and coaching topic buckets. Replace these placeholders with local office terminology.",
        "options": [
            _option("Civil 3D Production", order=10),
            _option("AutoCAD Production", order=20),
            _option("Plan Production", order=30),
            _option("Standards / Templates", order=40),
            _option("QA / Review Workflow", order=50),
            _option("Bluebeam / PDF Workflow", order=60),
            _option("Project Coordination", order=70),
            _option("Other", order=990),
        ],
    },
    {
        "key": "project_billing_phase",
        "label": "Project Billing Phases",
        "surface": "Project Tasks, Intake",
        "description": "Billing/work phase choices used when creating project-linked work.",
        "options": [
            _option("100 - Survey", order=100),
            _option("200 - Prelim Design", order=200),
            _option("300 - Const Docs", order=300),
            _option("400 - Bid Support", order=400),
            _option("500 - Const Admin", order=500),
            _option("Other", order=990),
        ],
    },
    {
        "key": "calendar_event_type",
        "label": "Calendar Event Types",
        "surface": "Calendar, Reports",
        "description": "Internal calendar categories. Values stay lowercase because report filters use these keys.",
        "options": [
            _option("meeting", "Meeting", 10),
            _option("milestone", "Milestone", 20),
            _option("deadline", "Deadline", 30),
            _option("review", "Review", 40),
            _option("task_due", "Task Due", 50),
            _option("prep", "Prep", 60),
            _option("reminder", "Reminder", 70),
            _option("other", "Other", 990),
        ],
    },
    {
        "key": "calendar_visibility",
        "label": "Calendar Visibility",
        "surface": "Calendar",
        "description": "Visibility choices for internal calendar events.",
        "options": [
            _option("internal", "Internal", 10),
            _option("private", "Private", 20),
            _option("shared", "Shared", 30),
        ],
    },
    {
        "key": "intake_source",
        "label": "Intake Sources",
        "surface": "Triage, Inbox, Intake",
        "description": "Source labels for quick captures and inbox items.",
        "options": [
            _option("manual", "Manual", 10),
            _option("voice", "Voice", 20),
            _option("telegram", "Telegram", 30),
            _option("email", "Email", 40),
            _option("paperless", "Paperless", 50),
            _option("remarkable-ocr", "reMarkable OCR", 60),
            _option("meeting", "Meeting", 70),
            _option("field-note", "Field Note", 80),
            _option("other", "Other", 990),
        ],
    },
    {
        "key": "intake_suggestion_category",
        "label": "Suggestion Categories",
        "surface": "Request Intake",
        "description": "Improvement idea categories used by the request intake form.",
        "options": [
            _option("Standards", order=10),
            _option("Workflow", order=20),
            _option("Templates", order=30),
            _option("Blocks", order=40),
            _option("Onboarding", order=50),
            _option("UI", order=60),
            _option("Other", order=990),
        ],
    },
    {
        "key": "feedback_type",
        "label": "Feedback Types",
        "surface": "Feedback",
        "description": "Categories available in the beta feedback capture tool.",
        "options": [
            _option("Bug", order=10),
            _option("Copy", order=20),
            _option("UX", order=30),
            _option("Data", order=40),
            _option("Workflow", order=50),
            _option("Idea", order=60),
        ],
    },
    {
        "key": "task_priority",
        "label": "Task Priorities",
        "surface": "Tasks, Intake, Feedback",
        "description": "Priority labels used by task, intake, and feedback workflows. Backend defaults still expect Low/Medium/High until workflow validation is fully dynamic.",
        "options": [
            _option("Low", order=10, metadata={"rank": 30, "tone": "success"}),
            _option("Medium", order=20, metadata={"is_default": True, "rank": 20, "tone": "warning"}),
            _option("High", order=30, metadata={"rank": 10, "tone": "danger"}),
        ],
    },
    {
        "key": "incident_severity",
        "label": "Incident Severities",
        "surface": "Incidents, Reports, Intake",
        "description": "Impact labels used by incident/capability reports.",
        "options": [
            _option("Low", order=10, metadata={"is_high_severity": False, "tone": "success"}),
            _option("Medium", order=20, metadata={"is_default": True, "is_high_severity": False, "tone": "warning"}),
            _option("High", order=30, metadata={"is_high_severity": True, "tone": "danger"}),
            _option("Critical", order=40, metadata={"is_high_severity": True, "tone": "danger"}),
        ],
    },
    {
        "key": "project_display_status",
        "label": "Project Display Statuses",
        "surface": "Projects, Map, Reports",
        "description": "Project status values shown in the project registry, map, and report filters. Defaults match the master project list.",
        "options": [
            _option("active", "Active", 10, metadata={"is_default": True, "counts_as_open": True, "tone": "success"}),
            _option("dormant", "Dormant", 20, metadata={"is_terminal": True, "counts_as_open": False, "tone": "muted"}),
        ],
    },
]


def seed_default_option_sets(sess: Session) -> bool:
    """Seed built-in option sets only when a set or set's options are absent."""
    changed = False
    for spec in DEFAULT_OPTION_SETS:
        row = sess.scalar(select(ManagedOptionSet).where(ManagedOptionSet.key == spec["key"]))
        if row is None:
            row = ManagedOptionSet(
                key=spec["key"],
                label=spec["label"],
                description=spec.get("description", ""),
                surface=spec.get("surface", ""),
                is_system=1,
                active=1,
            )
            sess.add(row)
            sess.flush()
            changed = True
        else:
            if not row.label:
                row.label = spec["label"]
                changed = True
            row.is_system = 1

        has_options = sess.scalar(
            select(ManagedOption.id).where(ManagedOption.set_id == row.id).limit(1)
        ) is not None
        if not has_options:
            for opt in spec.get("options", []):
                sess.add(ManagedOption(
                    set_id=row.id,
                    value=opt["value"],
                    label=opt["label"],
                    description=opt.get("description", ""),
                    display_order=_intish(opt.get("display_order")),
                    active=1,
                    is_placeholder=_boolish(opt.get("is_placeholder"), default=True),
                    metadata_json=opt.get("metadata_json") or "{}",
                ))
            changed = True
        else:
            existing = {
                option.value: option
                for option in sess.scalars(select(ManagedOption).where(ManagedOption.set_id == row.id)).all()
            }
            for opt in spec.get("options", []):
                option = existing.get(opt["value"])
                if option is None:
                    continue
                default_meta = _json_dict(opt.get("metadata_json"))
                if not default_meta:
                    continue
                current_meta = _payload_metadata(option.metadata_json)
                merged = {**default_meta, **current_meta}
                if merged != current_meta:
                    option.metadata_json = _meta(_normalized_metadata(merged))
                    option.updated_at = now_utc_naive()
                    changed = True
    if changed:
        sess.flush()
    return changed


def list_sets(sess: Session, *, include_inactive: bool = True) -> list[ManagedOptionSet]:
    seed_default_option_sets(sess)
    stmt = select(ManagedOptionSet).order_by(ManagedOptionSet.label.asc())
    if not include_inactive:
        stmt = stmt.where(ManagedOptionSet.active == 1)
    return sess.scalars(stmt).all()


def get_set(sess: Session, key: str, *, include_inactive: bool = False) -> ManagedOptionSet | None:
    seed_default_option_sets(sess)
    stmt = select(ManagedOptionSet).where(ManagedOptionSet.key == normalize_set_key(key))
    if not include_inactive:
        stmt = stmt.where(ManagedOptionSet.active == 1)
    return sess.scalar(stmt)


def option_rows(sess: Session, set_row: ManagedOptionSet, *, include_inactive: bool = False) -> list[ManagedOption]:
    stmt = select(ManagedOption).where(ManagedOption.set_id == set_row.id)
    if not include_inactive:
        stmt = stmt.where(ManagedOption.active == 1)
    return sess.scalars(stmt.order_by(ManagedOption.display_order.asc(), ManagedOption.label.asc())).all()


def _skill_lookup(sess: Session) -> dict[str, int]:
    if sess.scalar(select(SkillCategory.id).limit(1)) is None:
        seed_default_categories(sess)
    lookup: dict[str, int] = {}
    rows = sess.scalars(select(SkillCategory).where(SkillCategory.active == 1)).all()
    for row in rows:
        lookup[(row.slug or "").strip().lower()] = row.id
        lookup[(row.name or "").strip().lower()] = row.id
    return lookup


def option_payload(option: ManagedOption, *, set_key: str = "", skill_lookup: dict[str, int] | None = None) -> dict[str, Any]:
    metadata = _payload_metadata(option.metadata_json)
    payload = {
        "id": option.id,
        "set_id": option.set_id,
        "set_key": set_key,
        "value": option.value,
        "label": option.label,
        "description": option.description or "",
        "display_order": option.display_order or 0,
        "active": option.active,
        "is_placeholder": option.is_placeholder,
        "metadata": metadata,
        "is_default": _bool_meta(metadata.get("is_default"), default=False),
        "is_terminal": _bool_meta(metadata.get("is_terminal"), default=False),
        "counts_as_open": _bool_meta(metadata.get("counts_as_open"), default=True),
        "tone": _tone(metadata.get("tone")),
    }
    if skill_lookup is not None:
        skill_key = str(metadata.get("skill_category_slug") or "").strip().lower()
        skill_id = skill_lookup.get(skill_key) or skill_lookup.get((option.value or "").strip().lower()) or skill_lookup.get((option.label or "").strip().lower())
        payload["skill_category_id"] = skill_id or None
    return payload


def set_payload(sess: Session, row: ManagedOptionSet, *, include_options: bool = False,
                include_inactive_options: bool = True) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "key": row.key,
        "label": row.label,
        "description": row.description or "",
        "surface": row.surface or "",
        "is_system": row.is_system,
        "active": row.active,
        "created_at": row.created_at.isoformat(sep=" ") if row.created_at else None,
        "updated_at": row.updated_at.isoformat(sep=" ") if row.updated_at else None,
    }
    if include_options:
        lookup = _skill_lookup(sess) if row.key == "cad_skill_area" else None
        payload["options"] = [
            option_payload(opt, set_key=row.key, skill_lookup=lookup)
            for opt in option_rows(sess, row, include_inactive=include_inactive_options)
        ]
    return payload


def options_payload(sess: Session, set_key: str, *, include_inactive: bool = False) -> list[dict[str, Any]]:
    row = get_set(sess, set_key, include_inactive=include_inactive)
    if row is None:
        return []
    lookup = _skill_lookup(sess) if row.key == "cad_skill_area" else None
    return [
        option_payload(opt, set_key=row.key, skill_lookup=lookup)
        for opt in option_rows(sess, row, include_inactive=include_inactive)
    ]


def create_set(sess: Session, data: dict[str, Any]) -> ManagedOptionSet | tuple[None, str]:
    key = normalize_set_key(data.get("key") or "")
    label = str(data.get("label") or "").strip()
    if not key or not label:
        return None, "key and label are required"
    if sess.scalar(select(ManagedOptionSet.id).where(ManagedOptionSet.key == key)) is not None:
        return None, "option set key already exists"
    row = ManagedOptionSet(
        key=key,
        label=label,
        description=str(data.get("description") or "").strip(),
        surface=str(data.get("surface") or "").strip(),
        is_system=0,
        active=_boolish(data.get("active"), default=True),
    )
    sess.add(row)
    sess.flush()
    return row


def update_set(row: ManagedOptionSet, data: dict[str, Any]) -> str | None:
    if "label" in data:
        label = str(data.get("label") or "").strip()
        if not label:
            return "label cannot be blank"
        row.label = label
    for field in ("description", "surface"):
        if field in data:
            setattr(row, field, str(data.get(field) or "").strip())
    if "active" in data:
        row.active = _boolish(data.get("active"), default=True)
    row.updated_at = now_utc_naive()
    return None


def create_option(sess: Session, set_row: ManagedOptionSet, data: dict[str, Any]) -> ManagedOption | tuple[None, str]:
    value = str(data.get("value") or data.get("label") or "").strip()
    label = str(data.get("label") or value).strip()
    if not value or not label:
        return None, "value and label are required"
    if sess.scalar(select(ManagedOption.id).where(
        ManagedOption.set_id == set_row.id,
        ManagedOption.value == value,
    )) is not None:
        return None, "option value already exists in this set"
    row = ManagedOption(
        set_id=set_row.id,
        value=value,
        label=label,
        description=str(data.get("description") or "").strip(),
        display_order=_intish(data.get("display_order")),
        active=_boolish(data.get("active"), default=True),
        is_placeholder=_boolish(data.get("is_placeholder"), default=False),
        metadata_json=_meta(_option_metadata_from_data(data)),
    )
    sess.add(row)
    sess.flush()
    _clear_other_defaults(sess, row)
    return row


def update_option(sess: Session, row: ManagedOption, data: dict[str, Any]) -> str | None:
    if "value" in data:
        value = str(data.get("value") or "").strip()
        if not value:
            return "value cannot be blank"
        row.value = value
    if "label" in data:
        label = str(data.get("label") or "").strip()
        if not label:
            return "label cannot be blank"
        row.label = label
    if "description" in data:
        row.description = str(data.get("description") or "").strip()
    if "display_order" in data:
        row.display_order = _intish(data.get("display_order"))
    if "active" in data:
        row.active = _boolish(data.get("active"), default=True)
    if "is_placeholder" in data:
        row.is_placeholder = _boolish(data.get("is_placeholder"), default=False)
    metadata_keys = {"metadata", "is_default", "is_terminal", "counts_as_open", "tone"}
    if any(key in data for key in metadata_keys):
        row.metadata_json = _meta(_option_metadata_from_data(data, existing=_payload_metadata(row.metadata_json)))
    row.updated_at = now_utc_naive()
    _clear_other_defaults(sess, row)
    return None
