"""SQLAlchemy declarative models matching the live SQLite schema.

Phase 1D-1: models exist, alembic baselined, app still uses raw
sqlite3 in route handlers. Phase 1D-2 will replace those raw calls
with SQLAlchemy session usage, blueprint by blueprint.

Design notes:
- Single-file layout for speed; can be split into a `models/` package
  later if it grows. Plan v4 envisaged a per-concern split.
- Column order matches the live DB exactly (verified against
  `sqlite3 tracker.db .schema <table>`). Some columns landed via
  ALTER TABLE later than the original CREATE, hence the "trailing"
  blocks (e.g., role on users, AI columns on tickets).
- Defaults are server-side strings to mirror the existing CREATE
  statements; SQLAlchemy `default=` is also set so brand-new rows
  get the same value if inserted via the ORM.
- No relationships defined yet — the existing `activity_log` and
  `comments` tables key off (table_name, record_id) text + int
  pairs, not real FKs. RBAC + soft-delete in Phase 3+ will tighten
  these.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    TIMESTAMP,
    Float,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ── Users / settings ──────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    role: Mapped[str] = mapped_column(String, nullable=False, server_default=text("'user'"))


class ApprovedEmail(Base):
    __tablename__ = "approved_emails"

    email: Mapped[str] = mapped_column(String, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# ── Trackers (Project Tasks / CAD Dev / Training / Capabilities / Personal) ─

class WorkTask(Base):
    __tablename__ = "work_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    cad_skill_area: Mapped[str] = mapped_column(Text, server_default=text("''"))
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    requested_by: Mapped[str] = mapped_column(Text, server_default=text("''"))
    request_reference: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'Not Started'"))
    due_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    starter_note: Mapped[str] = mapped_column(Text, server_default=text("''"))
    clarifications_needed: Mapped[str] = mapped_column(Text, server_default=text("''"))
    software: Mapped[str] = mapped_column(Text, server_default=text("''"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ai_raw_input: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Phase-0 FK spine: nullable, additive. Text columns stay authoritative.
    project_id: Mapped[int | None] = mapped_column(Integer)


class ProjectWorkTask(Base):
    __tablename__ = "project_work_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))
    billing_phase: Mapped[str] = mapped_column(Text, server_default=text("''"))
    engineer: Mapped[str] = mapped_column(Text, server_default=text("''"))
    task_description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'Not Started'"))
    due_at: Mapped[str] = mapped_column(Text, server_default=text("''"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ai_raw_input: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Phase-0 FK spine: nullable, additive. Text columns stay authoritative.
    project_id: Mapped[int | None] = mapped_column(Integer)
    engineer_id: Mapped[int | None] = mapped_column(Integer)


class TrainingTask(Base):
    __tablename__ = "training_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    trainees: Mapped[str] = mapped_column(Text, server_default=text("''"))
    requested_by: Mapped[str] = mapped_column(Text, server_default=text("''"))
    skill_area: Mapped[str] = mapped_column(Text, server_default=text("''"))
    training_goals: Mapped[str] = mapped_column(Text, server_default=text("''"))
    additional_context: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'Not Started'"))
    due_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ai_raw_input: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Phase-0 FK spine. trainee_ids is a JSON-string array of employee ids
    # (kept as TEXT so the generic CRUD/AI paths don't choke on a list type).
    project_id: Mapped[int | None] = mapped_column(Integer)
    trainee_ids: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))


class PersonnelIssue(Base):
    """Capability / incident tracking.

    Phase 5.5: `person_name` became nullable so 0-person incidents are
    allowed (process gaps, equipment incidents); `person_ids` (JSON
    array) carries the multi-FK list when one or more employees are
    identified.
    """
    __tablename__ = "personnel_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_name: Mapped[str | None] = mapped_column(Text)
    observed_by: Mapped[str] = mapped_column(Text, server_default=text("''"))
    cad_skill_area: Mapped[str] = mapped_column(Text, server_default=text("''"))
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    incident_context: Mapped[str] = mapped_column(Text, server_default=text("''"))
    recommended_training: Mapped[str] = mapped_column(Text, server_default=text("''"))
    severity: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'Observed'"))
    reported_date: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    follow_up_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    resolution_notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Phase-0 FK spine.
    project_id: Mapped[int | None] = mapped_column(Integer)
    person_id: Mapped[int | None] = mapped_column(Integer)
    # Phase-2 richer incident shape — mirrors eng-ops's IncidentReport.
    estimated_time_loss_minutes: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    immediate_solution: Mapped[str] = mapped_column(Text, server_default=text("''"))
    skill_category_id: Mapped[int | None] = mapped_column(Integer)
    # Phase-5.5: multi-person support. JSON array of employee ids;
    # `person_id` (above) becomes the convenience "primary person" and
    # is auto-populated from person_ids[0] if set.
    person_ids: Mapped[str] = mapped_column(Text, server_default=text("'[]'"))


class PersonalItem(Base):
    """Personal-life items, categorized into Husband/Father/House/Cars.

    One table, one schema — the UI presents four filtered tabs (one per
    category). Triage items can promote into this table with a category
    override; the promote endpoint already plumbs that through.
    """
    __tablename__ = "personal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'New'"))
    due_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    source_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    __table_args__ = (
        Index("idx_personal_items_category", "category"),
        Index("idx_personal_items_status", "status"),
    )


class InboxItem(Base):
    """Unified capture surface for the Nexus suite.

    Lives forever as a personal todo OR gets promoted into one of the
    five trackers via /api/v1/inbox/<id>/promote (which records where
    it went via promoted_to_table / promoted_to_id).
    """
    __tablename__ = "inbox_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    source_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'New'"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    due_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    promoted_to_table: Mapped[str] = mapped_column(Text, server_default=text("''"))
    promoted_to_id: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    __table_args__ = (
        Index("idx_inbox_items_status", "status"),
        Index("idx_inbox_items_source_ref", "source", "source_ref"),
    )


# ── Audit / activity / comments / telegram ────────────────────────────────

class ActivityLog(Base):
    __tablename__ = "activity_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    old_value: Mapped[str] = mapped_column(Text, server_default=text("''"))
    new_value: Mapped[str] = mapped_column(Text, server_default=text("''"))
    user_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_name: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, server_default=text("''"))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by_user_id: Mapped[int | None] = mapped_column(Integer)
    uploaded_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    uploaded_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_attachments_table_record", "table_name", "record_id"),
    )


class Link(Base):
    __tablename__ = "links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    record_id: Mapped[int] = mapped_column(Integer, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source_kind: Mapped[str] = mapped_column(Text, server_default=text("'generic'"))
    added_by_user_id: Mapped[int | None] = mapped_column(Integer)
    added_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_links_table_record", "table_name", "record_id"),
    )


class TelegramChatAccess(Base):
    __tablename__ = "telegram_chat_access"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, server_default=text("''"))
    display_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    linked_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    is_active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    user_id: Mapped[int | None] = mapped_column(Integer)


# ── Registry: employees + projects (Phase 0 FK spine) ─────────────────────

class Employee(Base):
    """Person being tracked by TaskTrack.

    Distinct from `User` (which is the login identity). Josh tracks
    employees here regardless of whether they have TaskTrack accounts.
    """
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, server_default=text("''"))
    role: Mapped[str] = mapped_column(Text, server_default=text("''"))
    title: Mapped[str] = mapped_column(Text, server_default=text("''"))
    active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_employees_active", "active"),
        Index("idx_employees_display_name", "display_name"),
    )


class Project(Base):
    """Project being tracked by TaskTrack.

    `project_number` is the human key (e.g. "1234.56"). `external_ref` +
    `external_system` are slots for a future external project registry
    integration (eng-ops's Atlas pattern) — empty for now.
    """
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_number: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    client: Mapped[str] = mapped_column(Text, server_default=text("''"))
    billing_phase_default: Mapped[str] = mapped_column(Text, server_default=text("''"))
    active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    external_ref: Mapped[str] = mapped_column(Text, server_default=text("''"))
    external_system: Mapped[str] = mapped_column(Text, server_default=text("''"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Phase-0.5 (Atlas-lite): primary point location + workflow status that
    # drives map-pin color. `active` above is the soft-delete flag and is
    # intentionally distinct from `display_status` here.
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    display_status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_projects_project_number", "project_number", unique=True),
        Index("idx_projects_active", "active"),
    )


# ── Competency (Phase 1) ──────────────────────────────────────────────────

class SkillCategory(Base):
    """Skill rubric used by the Competency matrix. Slug is the stable
    identifier safe to embed; name is the human label that shows in the
    column header. Display_order drives left-to-right column ordering."""
    __tablename__ = "skill_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    display_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_skill_categories_slug", "slug", unique=True),
        Index("idx_skill_categories_active", "active"),
    )


class EmployeeSkillScore(Base):
    """One row per (employee, category): the assertion that the employee
    has reached `score` proficiency. Upsert-only; the polymorphic
    activity_log keyed by (`employee_skill_scores`, score.id) carries
    per-cell history."""
    __tablename__ = "employee_skill_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("5.0"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_emp_skill_employee", "employee_id"),
        Index("idx_emp_skill_category", "category_id"),
        Index("idx_emp_skill_unique", "employee_id", "category_id", unique=True),
    )


def to_dict(obj) -> dict | None:
    """Serialize a SQLAlchemy model instance to a plain column-name dict.

    Used by route handlers that need to return JSON or pass row data
    to a template — replaces the legacy `dict(sqlite3_row)` pattern.

    Datetime / date values are serialized as ISO strings (with a space
    separator for datetimes, e.g. "2026-04-27 20:06:13") to match the
    shape the legacy raw-sqlite3 path produced. The SPA's formatTimeAgo
    helper appends "Z" to that string to parse as UTC; if we let
    Flask's default JSON encoder render datetimes as RFC 822 ("Mon, 27
    Apr 2026 GMT") instead, that helper silently breaks. This is the
    contract surface tests don't cover yet — keep the format stable.
    """
    if obj is None:
        return None
    out = {}
    for c in obj.__table__.columns:
        value = getattr(obj, c.name)
        if isinstance(value, datetime):
            out[c.name] = value.isoformat(sep=" ")
        elif isinstance(value, date):
            out[c.name] = value.isoformat()
        else:
            out[c.name] = value
    return out


__all__ = [
    "Base",
    "User", "ApprovedEmail", "AppSetting",
    "WorkTask", "ProjectWorkTask", "TrainingTask",
    "PersonnelIssue", "PersonalItem", "InboxItem",
    "ActivityLog", "Comment", "TelegramChatAccess",
    "Attachment", "Link",
    "Employee", "Project",
    "SkillCategory", "EmployeeSkillScore",
    "to_dict",
]
