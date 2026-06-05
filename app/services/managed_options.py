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


def _link_option(value: str, title: str, href: str, subtitle: str,
                 order: int = 0, *, admin_only: bool = False) -> dict[str, Any]:
    return _option(value, title, order, subtitle, metadata={
        "href": href,
        "admin_only": admin_only,
        "tone": "neutral",
    })


def _inventory_option(value: str, area: str, status: str, scope: str,
                      next_step: str, order: int = 0) -> dict[str, Any]:
    return _option(value, area, order, scope, metadata={
        "status": status,
        "next_step": next_step,
        "tone": "success" if status.startswith("Admin-managed") else ("warning" if "Partially" in status else "danger"),
    })


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
        "description": "Categories available in the in-app feedback capture tool.",
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
    {
        "key": "admin_report_shortcut",
        "label": "Admin Report Shortcuts",
        "surface": "Admin Reports",
        "description": "Shortcut cards shown in Admin > Reports. Edit titles, URLs, order, and active state here.",
        "options": [
            _link_option("full_tracker", "Full Tracker", "/", "Dashboard, triage, map, calendar, and task queues.", 10),
            _link_option("project_map", "Project Map", "/?tab=map", "Map pins, workspace drawer, reports, and map focus actions.", 20),
            _link_option("calendar", "Calendar", "/?tab=calendar", "Internal meetings, prep blocks, deadlines, and project-linked events.", 30),
            _link_option("report_center", "Report Center", "/reports", "Central report surface for packets, briefs, and review flows.", 40),
            _link_option("today_brief", "Today Brief", "/reports/today", "Compact daily operator packet with upcoming meetings and project actions.", 50),
            _link_option("management_packet", "Management Packet", "/reports/management", "Print-ready portfolio, action, intake, meeting, and incident summary.", 60),
            _link_option("portfolio_reports", "Portfolio Reports", "/reports/projects", "Management-ready project packets with filters, presets, and print layout.", 70),
            _link_option("at_risk_queue", "At-Risk Queue", "/reports/projects?attention_level=at_risk&limit=25", "Portfolio report filtered to projects needing attention first.", 80),
            _link_option("incident_reports", "Incident Reports", "/reports/incidents?open_only=1", "Admin-only incident reports with full narratives, JSON, and CSV.", 90, admin_only=True),
            _link_option("high_severity_incidents", "High Severity Incidents", "/reports/incidents?severity=High&open_only=1", "Open high-severity capability incidents for management review.", 100, admin_only=True),
            _link_option("incident_csv", "Incident CSV", "/api/v1/reports/incidents.csv?open_only=1", "Download the current open incident report as CSV.", 110, admin_only=True),
            _link_option("at_risk_csv", "At-Risk CSV", "/api/v1/reports/projects/actions.csv?attention_level=at_risk&limit=25", "Download the current management action queue as CSV.", 120),
            _link_option("project_one_pager", "Project One-Pager", "/reports/project", "Single-project status packet with workspace data and activity.", 130),
            _link_option("meeting_packet_batch", "Meeting Packet Batch", "/reports/meetings?days=14&limit=12", "Printable batch of upcoming visible event packets.", 140),
            _link_option("weekly_review", "Weekly Review", "/weekly?days=7", "Seven-day operational digest for check-ins and review meetings.", 150),
            _link_option("submission_forms", "Submission Forms", "/intake", "Authenticated intake forms for triage and operational capture.", 160),
            _link_option("printable_intake_packet", "Printable Intake Packet", "/intake/printable", "Browser PDF and reMarkable-ready request forms.", 170),
            _link_option("intake_review_queue", "Intake Review Queue", "/intake/review?needs_review=1", "Operator queue for web, paper, and OCR-created requests.", 180),
            _link_option("intake_source_report", "Intake Source Report", "/reports/intake", "Review and export paper, OCR, and source-tagged capture records.", 190),
        ],
    },
    {
        "key": "report_console_quick_action",
        "label": "Report Console Quick Actions",
        "surface": "Report Console",
        "description": "Buttons shown in the Report Console quick-action panel.",
        "options": [
            _link_option("today_brief", "Today Brief", "/reports/today", "Daily packet.", 10),
            _link_option("management_packet", "Management Packet", "/reports/management", "Management-ready packet.", 20),
            _link_option("ocr_intake", "OCR Intake", "/capture/ocr", "Open OCR intake workflow.", 30),
            _link_option("intake_review_queue", "Intake Review Queue", "/intake/review?needs_review=1", "Review web, paper, and OCR-created requests.", 40),
            _link_option("intake_source_report", "Intake Source Report", "/reports/intake", "Audit source-tagged capture records.", 50),
            _link_option("quick_ocr_capture", "Quick OCR Capture", "/?capture_source=remarkable-ocr", "Start a source-tagged OCR capture.", 60),
            _link_option("rollout_checklist", "Rollout Checklist", "/testing", "Open the current validation checklist.", 70),
            _link_option("printable_forms", "Printable Forms", "/intake/printable", "Browser PDF and reMarkable-ready forms.", 80),
            _link_option("triage_inbox", "Triage Inbox", "/?tab=triage", "Open review and triage queue.", 90),
            _link_option("calendar", "Calendar", "/?tab=calendar", "Open internal calendar.", 100),
            _link_option("at_risk_projects", "At-Risk Projects", "/reports/projects?attention_level=at_risk&limit=25", "Open the current at-risk project queue.", 110),
            _link_option("open_incidents", "Open Incidents", "/reports/incidents?open_only=1", "Open admin incident report queue.", 120, admin_only=True),
        ],
    },
    {
        "key": "admin_control_inventory",
        "label": "Configuration Coverage",
        "surface": "Admin System",
        "description": "Admin-visible map of what is configurable now and what remains code-controlled.",
        "options": [
            _inventory_option("managed_dropdowns", "Managed dropdowns", "Admin-managed now", "CAD skills, training skills, billing phases, calendar types, intake sources, suggestion categories, feedback types, priorities, and severities.", "Keep expanding simple vocabulary fields here.", 10),
            _inventory_option("people_registry", "People registry", "Admin-managed now", "Employees, active state, and competency tracking participation.", "Add office/team/discipline fields once workflow terminology is settled.", 20),
            _inventory_option("project_registry", "Project registry", "Admin-managed now", "Projects are editable, and display statuses come from the managed Project Display Statuses option set.", "Add map color/legend controls after status semantics are settled.", 30),
            _inventory_option("report_shortcuts", "Report shortcuts and quick actions", "Admin-managed now", "Admin report cards and Report Console quick-action buttons are editable from Admin.", "Add default filter controls for recurring report surfaces.", 40),
            _inventory_option("workflow_states", "Workflow states", "Code-controlled", "Task, feedback, calendar, and incident status semantics still drive done/open counts and kanban lanes.", "Make backend validation and terminal/open semantics dynamic before exposing full CRUD.", 50),
            _inventory_option("intake_presets", "Intake presets and form copy", "Partially code-controlled", "Source labels and priorities are editable; preset labels, default targets, OCR labels, and form copy remain static.", "Promote capture presets and public form copy to Admin > Intake.", 60),
            _inventory_option("map_legends", "Map and visual status legends", "Code-controlled", "Project pin colors, status colors, and map legend labels.", "Expose presentation controls after project statuses are made dynamic.", 70),
            _inventory_option("competency_rubric", "Competency rubric", "Partially admin-managed", "Skill categories are editable; dimensions and rating levels remain static.", "Treat full rubric editing as a separate larger phase.", 80),
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
