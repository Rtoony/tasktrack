"""Competency service — evidence rows + cached 1-5 rollups.

The public matrix still reads employee_skill_scores, but that row is now a
cached rollup. Append-only employee_skill_subscores carry the evidence,
source, and notes used to explain each visible score.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import exp

from flask import session as flask_session
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import SKILL_CATEGORY_DEFAULTS, SKILL_DIMENSION_DEFAULTS
from ..models import EmployeeSkillScore, EmployeeSkillSubscore, SkillCategory
from .audit import log_activity

SCORE_MIN = 1.0
SCORE_MAX = 5.0
ROLLUP_VERSION = 2


class CompetencyError(Exception):
    """Client-visible validation failure (bad score, missing employee, etc)."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class Dimension:
    slug: str
    name: str
    weight: float


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _round_half(value: float) -> float:
    return round(value * 2.0) / 2.0


def _clamp_score(raw) -> float:
    """Coerce a request payload value to a clamped 1-5 float."""
    try:
        v = float(raw)
    except (TypeError, ValueError) as e:
        raise CompetencyError("score must be a number") from e
    if v < SCORE_MIN or v > SCORE_MAX:
        raise CompetencyError(f"score must be between {SCORE_MIN} and {SCORE_MAX}")
    return v


def _clamp_weight(raw) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return max(0.05, min(10.0, v))


def seed_default_categories(sess: Session) -> int:
    """Insert any missing default categories. Idempotent."""
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


def dimensions_for_category(category: SkillCategory | None) -> list[Dimension]:
    slug = getattr(category, "slug", "") or "default"
    raw_dims = SKILL_DIMENSION_DEFAULTS.get(slug) or SKILL_DIMENSION_DEFAULTS["default"]
    return [
        Dimension(
            slug=str(d["slug"]),
            name=str(d.get("name") or d["slug"]),
            weight=_clamp_weight(d.get("weight", 1.0)),
        )
        for d in raw_dims
    ]


def confidence_band(confidence: float | None) -> str:
    c = float(confidence or 0.0)
    if c < 0.33:
        return "low"
    if c < 0.66:
        return "medium"
    return "high"


def _weighted_mean(values: list[tuple[float, float]]) -> float | None:
    total_weight = sum(w for _, w in values if w > 0)
    if total_weight <= 0:
        return None
    return sum(v * w for v, w in values if w > 0) / total_weight


def _dimension_summary(rows: list[EmployeeSkillSubscore], dimension: Dimension, *, now: datetime) -> dict:
    dim_rows = [r for r in rows if r.dimension_slug == dimension.slug]
    weighted = []
    latest = None
    for row in dim_rows:
        observed = _parse_dt(row.observed_at) or now
        latest = observed if latest is None or observed > latest else latest
        age_days = max(0.0, (now - observed).total_seconds() / 86400.0)
        weighted.append((float(row.score), float(row.weight or dimension.weight) * exp(-age_days / 180.0)))
    score = _weighted_mean(weighted)
    return {
        "slug": dimension.slug,
        "name": dimension.name,
        "weight": dimension.weight,
        "score": _round_half(score) if score is not None else None,
        "raw_score": score,
        "n": len(dim_rows),
        "last_observed_at": latest.isoformat(sep=" ") if latest else "",
    }


def aggregate_category(sess: Session, employee_id: int, category_id: int) -> dict | None:
    """Compute one cached category rollup from append-only evidence rows."""
    category = sess.get(SkillCategory, category_id)
    dims = dimensions_for_category(category)
    now = _utcnow()
    rows = sess.scalars(
        select(EmployeeSkillSubscore)
        .where(
            EmployeeSkillSubscore.employee_id == employee_id,
            EmployeeSkillSubscore.category_id == category_id,
        )
        .order_by(EmployeeSkillSubscore.observed_at.asc(), EmployeeSkillSubscore.id.asc())
    ).all()
    if not rows:
        return None

    dim_summaries = [_dimension_summary(rows, dim, now=now) for dim in dims]
    scored_dims = [
        (float(d["raw_score"]), float(d["weight"]))
        for d in dim_summaries
        if d["raw_score"] is not None
    ]
    category_score = _weighted_mean(scored_dims)

    manual_rows = [r for r in rows if r.dimension_slug == "manual" or r.source_kind == "manual_override"]
    manual_latest = None
    if manual_rows:
        manual_latest = max(manual_rows, key=lambda r: (_parse_dt(r.observed_at) or now, r.id or 0))
        if category_score is None:
            category_score = float(manual_latest.score)

    if category_score is None:
        return None

    observed_dates = [_parse_dt(r.observed_at) or now for r in rows]
    most_recent = max(observed_dates)
    year_ago = now - timedelta(days=365)
    recent_n = len([d for d in observed_dates if d >= year_ago])
    recency_days = max(0.0, (now - most_recent).total_seconds() / 86400.0)
    recency_factor = exp(-recency_days / 90.0)
    coverage = len([d for d in dim_summaries if d["n"] > 0]) / max(1, len(dims))
    confidence = min(1.0, (recent_n / 10.0) * 0.5 + recency_factor * 0.3 + coverage * 0.2)
    if manual_latest is not None and coverage == 0:
        confidence = max(confidence, 0.3)

    return {
        "score": max(SCORE_MIN, min(SCORE_MAX, _round_half(category_score))),
        "confidence": round(confidence, 4),
        "confidence_band": confidence_band(confidence),
        "sample_size": recent_n,
        "last_observed_at": most_recent,
        "dimensions": [
            {k: v for k, v in d.items() if k != "raw_score"}
            for d in dim_summaries
        ],
    }


def _score_row(sess: Session, employee_id: int, category_id: int) -> EmployeeSkillScore | None:
    return sess.scalar(
        select(EmployeeSkillScore).where(
            EmployeeSkillScore.employee_id == employee_id,
            EmployeeSkillScore.category_id == category_id,
        )
    )


def write_cached_rollup(sess: Session, employee_id: int, category_id: int) -> EmployeeSkillScore | None:
    rollup = aggregate_category(sess, employee_id, category_id)
    if rollup is None:
        return None
    row = _score_row(sess, employee_id, category_id)
    user_id = flask_session.get("user_id")
    if row is None:
        row = EmployeeSkillScore(
            employee_id=employee_id,
            category_id=category_id,
            score=rollup["score"],
            confidence=rollup["confidence"],
            sample_size=rollup["sample_size"],
            last_observed_at=rollup["last_observed_at"],
            rollup_version=ROLLUP_VERSION,
            updated_by_user_id=user_id,
        )
        sess.add(row)
        sess.flush()
        log_activity(sess, "employee_skill_scores", row.id, "score_set", field="score", new=str(row.score))
    else:
        old = row.score
        row.score = rollup["score"]
        row.confidence = rollup["confidence"]
        row.sample_size = rollup["sample_size"]
        row.last_observed_at = rollup["last_observed_at"]
        row.rollup_version = ROLLUP_VERSION
        row.updated_by_user_id = user_id
        row.updated_at = _utcnow()
        if old != row.score:
            log_activity(sess, "employee_skill_scores", row.id, "score_updated", field="score", old=str(old), new=str(row.score))
    return row


def add_subscore(
    sess: Session,
    *,
    employee_id: int,
    category_id: int,
    dimension_slug: str,
    raw_score,
    weight=None,
    observed_at=None,
    source_kind: str = "manual",
    source_id=None,
    notes: str = "",
) -> tuple[EmployeeSkillSubscore, EmployeeSkillScore | None]:
    score = _clamp_score(raw_score)
    observed = _parse_dt(observed_at) or _utcnow()
    dimension = (dimension_slug or "").strip().lower()
    if not dimension:
        raise CompetencyError("dimension_slug is required")
    row = EmployeeSkillSubscore(
        employee_id=employee_id,
        category_id=category_id,
        dimension_slug=dimension,
        score=score,
        weight=_clamp_weight(weight if weight is not None else 1.0),
        observed_at=observed,
        source_kind=(source_kind or "manual").strip() or "manual",
        source_id=int(source_id) if source_id not in (None, "") else None,
        notes=(notes or "").strip(),
        created_by_user_id=flask_session.get("user_id"),
    )
    sess.add(row)
    sess.flush()
    cached = write_cached_rollup(sess, employee_id, category_id)
    if cached is not None:
        log_activity(
            sess,
            "employee_skill_scores",
            cached.id,
            "subscore_added",
            field=dimension,
            new=str(score),
        )
    return row, cached


def upsert_score(sess: Session, employee_id: int, category_id: int, raw_score, notes: str = "", source_kind: str = "manual_override") -> EmployeeSkillScore:
    """Backward-compatible manual score entry.

    The old endpoint directly overwrote the cached score. It now records a
    manual evidence row so the power view can explain where the number came
    from, while preserving the old response shape.
    """
    _, row = add_subscore(
        sess,
        employee_id=employee_id,
        category_id=category_id,
        dimension_slug="manual",
        raw_score=raw_score,
        weight=1.0,
        source_kind=source_kind or "manual_override",
        notes=notes,
    )
    if row is None:
        raise CompetencyError("score rollup failed")
    if notes:
        row.notes = notes
    return row


def detail_for_cell(sess: Session, employee_id: int, category_id: int) -> dict | None:
    row = _score_row(sess, employee_id, category_id)
    rollup = aggregate_category(sess, employee_id, category_id)
    if row is None and rollup is None:
        return None
    if rollup is None:
        return {
            "score": row.score,
            "confidence": row.confidence,
            "confidence_band": confidence_band(row.confidence),
            "sample_size": row.sample_size,
            "last_observed_at": row.last_observed_at.isoformat(sep=" ") if row.last_observed_at else "",
            "dimensions": [],
        }
    return {
        "score": rollup["score"],
        "confidence": rollup["confidence"],
        "confidence_band": rollup["confidence_band"],
        "sample_size": rollup["sample_size"],
        "last_observed_at": rollup["last_observed_at"].isoformat(sep=" ") if rollup["last_observed_at"] else "",
        "dimensions": rollup["dimensions"],
    }


def recompute_all(sess: Session) -> int:
    pairs = sess.execute(
        select(EmployeeSkillSubscore.employee_id, EmployeeSkillSubscore.category_id).distinct()
    ).all()
    updated = 0
    for employee_id, category_id in pairs:
        if write_cached_rollup(sess, int(employee_id), int(category_id)) is not None:
            updated += 1
    return updated
