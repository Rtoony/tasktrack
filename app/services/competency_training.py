"""Competency → Training bridge (Phase 2, WORK_PLAN P2-6).

A competency *cell* — one (employee_id, category_id) pair in the skill
matrix — is not a row in a single tracker table, so it cannot ride the
generic BRIDGE_MAP machinery in `services/bridges.py` (that bridges
real table rows via TABLE_MODELS). A gap in a person's competency is a
*virtual* source: a low cached rollup score for that cell.

This module bridges that virtual source to the Training tracker. Given a
gap cell it either:

  * **creates** a fresh `training_tasks` row, carrying the employee name
    into `trainees`, the skill-category name into `skill_area`, and a
    gap-aware default into `training_goals`; or
  * **links** an existing training task chosen by the caller.

Either way the linkage is reflected **both directions** through the
existing `activity_log` markers (the same two-way pattern bridges.py
uses), so:

  * the cell side records `linked_training:<training_id>` on
    `employee_skill_scores` (idempotent via the marker), and
  * the training side records `linked_competency:<employee_id>:<category_id>`
    on `training_tasks`.

No schema change is required — linkage lives in the existing append-only
`activity_log` table, which both the matrix cell drawer and the training
record already read for history. This keeps the change strictly additive.
"""
from __future__ import annotations

import json

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ..models import (
    ActivityLog,
    Employee,
    EmployeeSkillScore,
    SkillCategory,
    TrainingTask,
    to_dict,
)
from .audit import log_activity
from .competency import aggregate_category
from .tickets import create_direct_record

# A "gap" is a competency the person can't yet do unsupervised — the
# rubric's own decision column calls score 0 "Training target" and 1
# "Supervised / review output". 2+ is "assign freely", not a gap.
GAP_THRESHOLD = 1.0

# Marker action names written to activity_log. Kept as module constants so
# the read path and the write path can never drift on the string.
CELL_TABLE = "employee_skill_scores"
TRAINING_TABLE = "training_tasks"


class CompetencyTrainingError(Exception):
    """Client-visible validation failure for the competency→training bridge."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _cell_marker(training_id: int) -> str:
    return f"linked_training:{training_id}"


def _training_marker(employee_id: int, category_id: int) -> str:
    return f"linked_competency:{employee_id}:{category_id}"


def _cell_payload(employee_id: int, category_id: int) -> str:
    return json.dumps({"employee_id": employee_id, "category_id": category_id})


def _cell_score(sess: Session, employee_id: int, category_id: int):
    """Current competency score for a cell, or None if never scored.

    Reads the live rollup (not just the cached row) so a freshly-evidenced
    cell that has not had its cache rewritten still reports the right gap.
    """
    rollup = aggregate_category(sess, employee_id, category_id)
    if rollup is not None:
        return rollup["score"]
    row = sess.scalar(
        select(EmployeeSkillScore).where(
            EmployeeSkillScore.employee_id == employee_id,
            EmployeeSkillScore.category_id == category_id,
        )
    )
    return row.score if row is not None else None


def is_gap(score) -> bool:
    """A scored cell at or below the gap threshold. An unscored cell
    (score is None) is *not* treated as a gap — absence of evidence is
    not a documented shortfall."""
    return score is not None and float(score) <= GAP_THRESHOLD


def _validate_cell(sess: Session, employee_id: int,
                   category_id: int) -> tuple[Employee, SkillCategory]:
    emp = sess.get(Employee, employee_id)
    if emp is None:
        raise CompetencyTrainingError("employee not found", 404)
    cat = sess.get(SkillCategory, category_id)
    if cat is None:
        raise CompetencyTrainingError("category not found", 404)
    return emp, cat


def _cell_already_linked(sess: Session, employee_id: int, category_id: int,
                         training_id: int) -> bool:
    """True if this cell already carries a marker to this training task."""
    row = sess.scalar(
        select(ActivityLog).where(and_(
            ActivityLog.table_name == CELL_TABLE,
            ActivityLog.action == _cell_marker(training_id),
            ActivityLog.new_value == _cell_payload(employee_id, category_id),
        )).limit(1)
    )
    return row is not None


def linked_training_ids(sess: Session, employee_id: int,
                        category_id: int) -> list[int]:
    """All training task ids linked to this competency cell (cell side)."""
    rows = sess.scalars(
        select(ActivityLog).where(and_(
            ActivityLog.table_name == CELL_TABLE,
            ActivityLog.action.like("linked_training:%"),
            ActivityLog.new_value == _cell_payload(employee_id, category_id),
        )).order_by(ActivityLog.id.asc())
    ).all()
    seen: list[int] = []
    for r in rows:
        try:
            tid = int(r.action.split(":", 1)[1])
        except (IndexError, ValueError):
            continue
        if tid not in seen:
            seen.append(tid)
    return seen


def linked_cells_for_training(sess: Session, training_id: int) -> list[dict]:
    """All competency cells linked to a training task (training side)."""
    rows = sess.scalars(
        select(ActivityLog).where(and_(
            ActivityLog.table_name == TRAINING_TABLE,
            ActivityLog.record_id == training_id,
            ActivityLog.action.like("linked_competency:%"),
        )).order_by(ActivityLog.id.asc())
    ).all()
    cells: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for r in rows:
        try:
            _, emp, cat = r.action.split(":", 2)
            key = (int(emp), int(cat))
        except (IndexError, ValueError):
            continue
        if key in seen:
            continue
        seen.add(key)
        cells.append({"employee_id": key[0], "category_id": key[1]})
    return cells


def _append_trainee_id(training: TrainingTask, employee_id: int) -> None:
    """Merge the employee id into the training task's trainee_ids JSON array.

    trainee_ids is a TEXT-stored JSON array (see the model). We keep it a
    set-union so re-linking the same person is a no-op rather than a dup.
    """
    try:
        current = json.loads(training.trainee_ids or "[]")
        if not isinstance(current, list):
            current = []
    except (ValueError, TypeError):
        current = []
    if employee_id not in current:
        current.append(employee_id)
        training.trainee_ids = json.dumps(current)


def _write_links(sess: Session, *, employee_id: int, category_id: int,
                 training: TrainingTask) -> None:
    """Write the two-way activity_log markers + FK merge. Idempotent —
    re-linking the same (cell, training) pair adds no duplicate markers."""
    already_training = {"employee_id": employee_id, "category_id": category_id} \
        in linked_cells_for_training(sess, training.id)
    if not already_training:
        log_activity(sess, TRAINING_TABLE, training.id,
                     _training_marker(employee_id, category_id),
                     field="competency_link",
                     new=f"{employee_id}:{category_id}")

    if not _cell_already_linked(sess, employee_id, category_id, training.id):
        cell_row = sess.scalar(
            select(EmployeeSkillScore).where(
                EmployeeSkillScore.employee_id == employee_id,
                EmployeeSkillScore.category_id == category_id,
            )
        )
        # new_value carries the cell coordinates so the link survives even
        # when the cell has no cached score row yet (record_id falls back to 0).
        log_activity(
            sess, CELL_TABLE, cell_row.id if cell_row is not None else 0,
            _cell_marker(training.id),
            field="training_link",
            new=_cell_payload(employee_id, category_id),
        )

    _append_trainee_id(training, employee_id)


def link_existing_training(sess: Session, employee_id: int, category_id: int,
                           training_id: int) -> tuple[TrainingTask, bool]:
    """Link a competency cell to an already-existing training task.

    Returns (training, created=False). Raises CompetencyTrainingError on
    bad input. Idempotent: re-linking the same pair just returns it.
    Caller commits.
    """
    _validate_cell(sess, employee_id, category_id)
    training = sess.get(TrainingTask, training_id)
    if training is None:
        raise CompetencyTrainingError(
            f"training task #{training_id} not found", 404)
    _write_links(sess, employee_id=employee_id, category_id=category_id,
                 training=training)
    return training, False


def create_training_from_gap(
    sess: Session,
    employee_id: int,
    category_id: int,
    *,
    overrides: dict | None = None,
    require_gap: bool = True,
) -> tuple[TrainingTask, bool]:
    """Create a training task from a competency gap and link it both ways.

    Carries:
        employee.display_name -> trainees
        category.name         -> skill_area
        gap-aware default     -> training_goals (unless overridden)

    `require_gap` (default True) refuses to spawn training for a cell that
    is already at "assign freely" (score >= 2) or has never been scored —
    you don't train someone out of a strength, and an unscored cell has no
    documented shortfall. Pass require_gap=False to force it anyway.

    Returns (training, created=True). Caller commits.
    """
    overrides = overrides or {}
    emp, cat = _validate_cell(sess, employee_id, category_id)

    score = _cell_score(sess, employee_id, category_id)
    if require_gap and not is_gap(score):
        if score is None:
            raise CompetencyTrainingError(
                "cell has no competency score yet — nothing documents a gap; "
                "pass require_gap=false to create training anyway", 409)
        raise CompetencyTrainingError(
            f"competency score {score} is not a gap (gap is <= {GAP_THRESHOLD}); "
            "pass require_gap=false to create training anyway", 409)

    level_label = ""
    if score is not None:
        level_label = {0: "Can't yet", 1: "Supervised"}.get(int(score), "")
    default_goal = f"Close the {cat.name} competency gap"
    if level_label:
        default_goal += f" (currently: {level_label})"
    default_goal += f" for {emp.display_name}."

    payload = {
        "title": f"Training: {emp.display_name} — {cat.name}",
        "trainees": emp.display_name,
        "skill_area": cat.name,
        "training_goals": default_goal,
        "source": "competency_bridge",
    }
    payload.update({k: v for k, v in overrides.items() if v not in (None, "")})

    new_id, error = create_direct_record(
        sess, TRAINING_TABLE, payload,
        source_name=f"competency:{employee_id}:{category_id}",
        action="bridged_from",
        action_detail=f"competency_cell:{employee_id}:{category_id}",
    )
    if error:
        raise CompetencyTrainingError(error, 400)

    training = sess.get(TrainingTask, new_id)
    _write_links(sess, employee_id=employee_id, category_id=category_id,
                 training=training)
    return training, True


def cell_link_summary(sess: Session, employee_id: int,
                      category_id: int) -> dict:
    """Read-side payload: the cell's gap status + its linked training tasks.

    Used by the matrix cell drawer to show 'this gap has N training tasks'
    and offer the create/link action."""
    _validate_cell(sess, employee_id, category_id)
    score = _cell_score(sess, employee_id, category_id)
    ids = linked_training_ids(sess, employee_id, category_id)
    tasks = []
    for tid in ids:
        t = sess.get(TrainingTask, tid)
        if t is not None:
            tasks.append(to_dict(t))
    return {
        "employee_id": employee_id,
        "category_id": category_id,
        "score": score,
        "is_gap": is_gap(score),
        "gap_threshold": GAP_THRESHOLD,
        "linked_training": tasks,
    }
