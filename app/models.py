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


# ── Trackers (Project Tasks / CAD Dev / Training / Capabilities / Internal) ─

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
    """Internal follow-up items.

    The table name and legacy category values remain for migration safety.
    One table, one schema — the UI presents four filtered internal queues.
    Triage items can promote into this table with a category override; the
    promote endpoint already plumbs that through.
    """
    __tablename__ = "personal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'New'"))
    due_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
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

    Lives as an internal follow-up OR gets promoted into one of the
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



class FeedbackItem(Base):
    """In-app feedback captured while testing or operating TaskTrack.

    Screenshots use the generic attachments table keyed by
    (feedback_items, id). The context_json field is intentionally text so
    future Codex sessions can inspect browser/page context without needing
    a schema migration for every UI detail we decide to capture.
    """
    __tablename__ = "feedback_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, server_default=text("''"))
    feedback_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Bug'"))
    priority: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'New'"))
    page_url: Mapped[str] = mapped_column(Text, server_default=text("''"))
    tab: Mapped[str] = mapped_column(Text, server_default=text("''"))
    component_label: Mapped[str] = mapped_column(Text, server_default=text("''"))
    context_json: Mapped[str] = mapped_column(Text, server_default=text("'{}'"))
    tags: Mapped[str] = mapped_column(Text, server_default=text("''"))
    resolution_notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'in-app'"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)

    __table_args__ = (
        Index("idx_feedback_items_status", "status"),
        Index("idx_feedback_items_priority", "priority"),
        Index("idx_feedback_items_created_at", "created_at"),
        Index("idx_feedback_items_page", "page_url"),
    )


class CalendarEvent(Base):
    """Internal operations calendar event.

    Stores meeting prep, milestones, due dates, reporting deadlines, and
    reminders without depending on an external personal calendar service.
    Dates stay as ISO TEXT to match the rest of TaskTrack and keep the
    SQLite-to-Postgres migration path simple.
    """
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'meeting'"))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"))
    start_at: Mapped[str] = mapped_column(Text, nullable=False)
    end_at: Mapped[str] = mapped_column(Text, server_default=text("''"))
    all_day: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'scheduled'"))
    project_id: Mapped[int | None] = mapped_column(Integer)
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))
    related_table: Mapped[str] = mapped_column(Text, server_default=text("''"))
    related_id: Mapped[int | None] = mapped_column(Integer)
    reminder_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    location: Mapped[str] = mapped_column(Text, server_default=text("''"))
    visibility: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'internal'"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_calendar_events_start_at", "start_at"),
        Index("idx_calendar_events_status", "status"),
        Index("idx_calendar_events_event_type", "event_type"),
        Index("idx_calendar_events_project_id", "project_id"),
        Index("idx_calendar_events_related", "related_table", "related_id"),
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

    Distinct from `User` (which is the login identity). The operator tracks
    employees here regardless of whether they have TaskTrack accounts.
    """
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, server_default=text("''"))
    role: Mapped[str] = mapped_column(Text, server_default=text("''"))
    title: Mapped[str] = mapped_column(Text, server_default=text("''"))
    active: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    competency_tracked: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    photo_path: Mapped[str] = mapped_column(Text, server_default=text("''"))
    photo_source_url: Mapped[str] = mapped_column(Text, server_default=text("''"))
    photo_updated_at: Mapped[str] = mapped_column(Text, server_default=text("''"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_employees_active", "active"),
        Index("idx_employees_competency_tracked", "competency_tracked"),
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
    # intentionally distinct from `display_status` here. As of the
    # master-list import, `display_status` is constrained to
    # `{"active", "dormant"}` to match the source spreadsheet.
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    display_status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    # Master-list import fields. `component` is the project TYPE (e.g.
    # "Site Improvement Plans", "Topographic Mapping" — 33 distinct values
    # in the source). `principal` is the lead-of-record. Dates are stored
    # as ISO YYYY-MM-DD strings to keep the generic CRUD/to_dict path
    # simple; empty string means "not set" (consistent with the other
    # date-ish columns in this app).
    component: Mapped[str] = mapped_column(Text, server_default=text("''"))
    principal: Mapped[str] = mapped_column(Text, server_default=text("''"))
    start_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    dormant_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    # Vanish-tracking for the automated master-list sync. Both are
    # ISO 8601 strings or "" for "never". `last_seen_in_master_at` is
    # bumped to the run start time on every sync that finds the
    # project_number in the Excel. `vanished_from_master_at` is set on
    # the first sync that doesn't, and cleared back to "" if the
    # project later reappears.
    last_seen_in_master_at: Mapped[str] = mapped_column(Text, server_default=text("''"))
    vanished_from_master_at: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_projects_project_number", "project_number", unique=True),
        Index("idx_projects_active", "active"),
        Index("idx_projects_component", "component"),
        Index("idx_projects_client", "client"),
        Index("idx_projects_vanished_from_master_at",
              "vanished_from_master_at"),
    )


class ProjectOverlay(Base):
    """TaskTrack-owned project metadata that importers must not overwrite."""
    __tablename__ = "project_overlays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int | None] = mapped_column(Integer)
    project_number: Mapped[str] = mapped_column(Text, nullable=False)
    operator_status: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("''"))
    tags: Mapped[str] = mapped_column(Text, server_default=text("''"))
    next_review_date: Mapped[str] = mapped_column(Text, server_default=text("''"))
    internal_notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    report_note: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_project_overlays_project_id", "project_id", unique=True),
        Index("idx_project_overlays_project_number", "project_number", unique=True),
    )


class ReportPreset(Base):
    """Saved report filter set owned by TaskTrack users."""
    __tablename__ = "report_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    surface: Mapped[str] = mapped_column(Text, nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[int | None] = mapped_column(Integer)
    is_shared: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"), default=0)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_report_presets_surface", "surface"),
        Index("idx_report_presets_owner", "owner_user_id"),
        Index("idx_report_presets_shared", "is_shared"),
    )


class ProjectSite(Base):
    """One physical pin location for a project.

    Most projects have a single site (matching the legacy
    `projects.lat`/`projects.lng` columns, which are kept and mirror the
    primary site for backward compatibility). A handful — e.g. multi-
    parcel work for repeat clients like Forestville Water District — have
    many sites under one project number; the worst offender ("209") has
    69 pins. Each row carries a `pin_color` derived from the source KMZ
    pushpin icon, which encodes per-site artifact metadata (yellow =
    primary form placement, red = archived PDF on file, green = topo
    survey on file, blue = archived PDF + survey, pink = stray legacy).
    """
    __tablename__ = "project_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)
    pin_color: Mapped[str] = mapped_column(Text, server_default=text("''"))
    raw_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    is_primary: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'kmz'"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_project_sites_project_id", "project_id"),
        Index("idx_project_sites_pin_color", "pin_color"),
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
    """Cached competency rollup for one (employee, category) cell.

    Detailed evidence lives in employee_skill_subscores. This table stays as
    the fast matrix read path and backward-compatible endpoint contract.
    """
    __tablename__ = "employee_skill_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("5.0"))
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    updated_by_user_id: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("0.0"))
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_observed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP)
    rollup_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        Index("idx_emp_skill_employee", "employee_id"),
        Index("idx_emp_skill_category", "category_id"),
        Index("idx_emp_skill_unique", "employee_id", "category_id", unique=True),
    )


class EmployeeSkillSubscore(Base):
    """Append-only evidence behind competency score rollups."""
    __tablename__ = "employee_skill_subscores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, nullable=False)
    category_id: Mapped[int] = mapped_column(Integer, nullable=False)
    dimension_slug: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False, server_default=text("1.0"))
    observed_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    source_kind: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'manual'"))
    source_id: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_by_user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))

    __table_args__ = (
        Index("idx_skill_subscore_dim", "employee_id", "category_id", "dimension_slug"),
        Index("idx_skill_subscore_observed", "employee_id", "category_id", "observed_at"),
        Index("idx_skill_subscore_source", "source_kind", "source_id"),
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
    "PersonnelIssue", "PersonalItem", "InboxItem", "CalendarEvent",
    "ActivityLog", "Comment", "TelegramChatAccess",
    "Attachment", "Link",
    "Employee", "Project", "ProjectOverlay", "ReportPreset",
    "SkillCategory", "EmployeeSkillScore", "EmployeeSkillSubscore",
    "to_dict",
]
