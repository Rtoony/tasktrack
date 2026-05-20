"""Competency service — skill-score upserts + default-category seeding.

Scores are clamped to [1.0, 10.0]. Half-step granularity is enforced
client-side (the UI's number input has step=0.5) but the backend
accepts any float in range; we don't fight the operator over 5.25.

Activity logging uses the existing polymorphic `activity_log` table
keyed by (`employee_skill_scores`, record_id) so the per-cell history
view can reuse the existing `/api/v1/<table>/<id>/activity` endpoint.
"""
from __future__ import annotations

from datetime import datetime

from flask import session as flask_session
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import SKILL_CATEGORY_DEFAULTS
from ..models import EmployeeSkillScore, SkillCategory
from .audit import log_activity

SCORE_MIN = 1.0
SCORE_MAX = 10.0


class CompetencyError(Exception):
    """Client-visible validation failure (bad score, missing employee, etc)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _clamp_score(raw) -> float:
    """Coerce a request payload value to a clamped float."""
    try:
        v = float(raw)
    except (TypeError, ValueError) as e:
        raise CompetencyError("score must be a number") from e
    if v < SCORE_MIN or v > SCORE_MAX:
        raise CompetencyError(f"score must be between {SCORE_MIN} and {SCORE_MAX}")
    return v


def seed_default_categories(sess: Session) -> int:
    """Insert any missing default categories. Idempotent. Returns count
    of new rows. Called from the categories list endpoint so the first
    visit primes the rubric without a separate setup step."""
    existing_slugs = set(sess.scalars(select(SkillCategory.slug)).all())
    inserted = 0
    for entry in SKILL_CATEGORY_DEFAULTS:
        if entry["slug"] in existing_slugs:
            continue
        sess.add(SkillCategory(
            slug=entry["slug"],
            name=entry["name"],
            description=entry.get("description", ""),
            display_order=entry.get("display_order", 0),
            active=1,
        ))
        inserted += 1
    if inserted:
        sess.commit()
    return inserted


def upsert_score(sess: Session, employee_id: int, category_id: int,
                 raw_score, notes: str = "") -> EmployeeSkillScore:
    """Insert or update one (employee, category) cell.

    Writes an activity_log row with old/new score so the matrix can
    surface change history per cell."""
    score = _clamp_score(raw_score)
    row = sess.scalar(
        select(EmployeeSkillScore).where(
            EmployeeSkillScore.employee_id == employee_id,
            EmployeeSkillScore.category_id == category_id,
        )
    )
    user_id = flask_session.get("user_id")
    if row is None:
        row = EmployeeSkillScore(
            employee_id=employee_id,
            category_id=category_id,
            score=score,
            notes=notes or "",
            updated_by_user_id=user_id,
        )
        sess.add(row)
        sess.flush()
        log_activity(sess, "employee_skill_scores", row.id, "score_set",
                     field="score", new=str(score))
    else:
        old = row.score
        row.score = score
        if notes:
            row.notes = notes
        row.updated_by_user_id = user_id
        row.updated_at = datetime.utcnow()
        if old != score:
            log_activity(sess, "employee_skill_scores", row.id, "score_updated",
                         field="score", old=str(old), new=str(score))
    return row
