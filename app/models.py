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
from typing import Optional

from sqlalchemy import (
    Index, Integer, String, Text, TIMESTAMP, text,
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


# ── Trackers (Project Work / CAD Dev / Training / Capability / Suggestion) ─

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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ai_raw_input: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))


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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    needs_review: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    source: Mapped[str] = mapped_column(Text, server_default=text("'manual'"))
    ai_raw_input: Mapped[str] = mapped_column(Text, server_default=text("''"))
    ai_model: Mapped[str] = mapped_column(Text, server_default=text("''"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))


class PersonnelIssue(Base):
    """Capability tracking. Phase 4 carves this out into its own restricted module."""
    __tablename__ = "personnel_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    person_name: Mapped[str] = mapped_column(Text, nullable=False)
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
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))


class Suggestion(Base):
    __tablename__ = "suggestion_box"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_type: Mapped[str] = mapped_column(Text, server_default=text("''"))
    submitted_by: Mapped[str] = mapped_column(Text, server_default=text("''"))
    submitted_for: Mapped[str] = mapped_column(Text, server_default=text("'Management'"))
    summary: Mapped[str] = mapped_column(Text, server_default=text("''"))
    expected_value: Mapped[str] = mapped_column(Text, server_default=text("''"))
    priority: Mapped[str] = mapped_column(Text, server_default=text("'Medium'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'New'"))
    review_notes: Mapped[str] = mapped_column(Text, server_default=text("''"))
    promoted_work_task_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    project_number: Mapped[str] = mapped_column(Text, server_default=text("''"))


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
    promoted_to_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
    created_by_name: Mapped[str] = mapped_column(Text, server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMP, server_default=text("CURRENT_TIMESTAMP"))
    completed_at: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP)

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
    uploaded_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
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
    added_by_user_id: Mapped[Optional[int]] = mapped_column(Integer)
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
    user_id: Mapped[Optional[int]] = mapped_column(Integer)


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
    "PersonnelIssue", "Suggestion", "InboxItem",
    "ActivityLog", "Comment", "TelegramChatAccess",
    "Attachment", "Link",
    "to_dict",
]
