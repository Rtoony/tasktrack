"""Admin-only competency report builders."""
from __future__ import annotations

import csv
import io
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Employee, EmployeeSkillScore, EmployeeSkillSubscore, SkillCategory, User
from .competency import confidence_band, dimensions_for_category

LOW_SCORE_THRESHOLD = 2.0
CSV_FIELDS = [
    "employee",
    "title",
    "role",
    "competency_tracked",
    "category",
    "visible_score",
    "confidence",
    "sample_size",
    "preliminary_score",
    "preliminary_by",
    "preliminary_notes",
    "baseline_score",
    "baseline_by",
    "baseline_notes",
    "status",
]


def _dt(value) -> str:
    return value.isoformat(sep=" ") if value else ""


def _rating_payload(row: EmployeeSkillSubscore | None, user_names: dict[int, str]) -> dict | None:
    if row is None:
        return None
    user_id = row.created_by_user_id
    return {
        "score": row.score,
        "notes": row.notes or "",
        "created_by_user_id": user_id,
        "created_by_name": user_names.get(user_id or -1, ""),
        "observed_at": _dt(row.observed_at),
    }


def _cell_status(score: EmployeeSkillScore | None, prelim: dict | None, baseline: dict | None) -> str:
    flags = []
    if score is None:
        flags.append("missing_score")
    if prelim is None:
        flags.append("missing_preliminary")
    if baseline is None:
        flags.append("missing_baseline")
    if score is not None and float(score.score) < LOW_SCORE_THRESHOLD:
        flags.append("low_score")
    return ",".join(flags) or "complete"


def competency_report(sess: Session, *, filters: dict | None = None) -> dict:
    filters = filters or {}
    include_untracked = bool(filters.get("include_untracked"))
    role = str(filters.get("role") or "").strip()
    q = str(filters.get("q") or "").strip().lower()
    status_filter = str(filters.get("status") or "").strip()

    emp_stmt = select(Employee).order_by(Employee.display_name.asc())
    if not include_untracked:
        emp_stmt = emp_stmt.where(Employee.competency_tracked == 1)
    employees = sess.scalars(emp_stmt).all()
    if role:
        employees = [e for e in employees if (e.role or "") == role]
    if q:
        employees = [
            e for e in employees
            if q in (e.display_name or "").lower()
            or q in (e.title or "").lower()
            or q in (e.role or "").lower()
            or q in (e.email or "").lower()
        ]

    categories = sess.scalars(
        select(SkillCategory)
        .where(SkillCategory.active == 1)
        .order_by(SkillCategory.display_order.asc(), SkillCategory.name.asc())
    ).all()
    emp_ids = [e.id for e in employees]
    cat_ids = [c.id for c in categories]

    score_map: dict[tuple[int, int], EmployeeSkillScore] = {}
    if emp_ids and cat_ids:
        for row in sess.scalars(
            select(EmployeeSkillScore).where(
                EmployeeSkillScore.employee_id.in_(emp_ids),
                EmployeeSkillScore.category_id.in_(cat_ids),
            )
        ).all():
            score_map[(row.employee_id, row.category_id)] = row

    evidence_rows = []
    if emp_ids and cat_ids:
        evidence_rows = sess.scalars(
            select(EmployeeSkillSubscore).where(
                EmployeeSkillSubscore.employee_id.in_(emp_ids),
                EmployeeSkillSubscore.category_id.in_(cat_ids),
                EmployeeSkillSubscore.source_kind.in_(("preliminary_rating", "official_baseline")),
            ).order_by(EmployeeSkillSubscore.observed_at.desc(), EmployeeSkillSubscore.id.desc())
        ).all()
    user_ids = {r.created_by_user_id for r in evidence_rows if r.created_by_user_id}
    user_names = {}
    if user_ids:
        user_names = dict(sess.execute(select(User.id, User.display_name).where(User.id.in_(user_ids))).all())

    marker_map: dict[tuple[int, int], dict[str, EmployeeSkillSubscore | None]] = {}
    for row in evidence_rows:
        key = (row.employee_id, row.category_id)
        bucket = marker_map.setdefault(key, {"preliminary": None, "baseline": None})
        if row.source_kind == "preliminary_rating" and bucket["preliminary"] is None:
            bucket["preliminary"] = row
        if row.source_kind == "official_baseline" and bucket["baseline"] is None:
            bucket["baseline"] = row

    employee_rows = []
    all_csv_rows = []

    for emp in employees:
        cells = []
        score_sum = 0.0
        score_count = 0
        prelim_count = 0
        baseline_count = 0
        low_scores = []
        missing_scores = 0
        missing_prelim = 0
        missing_baseline = 0

        for cat in categories:
            score_row = score_map.get((emp.id, cat.id))
            markers = marker_map.get((emp.id, cat.id), {})
            prelim = _rating_payload(markers.get("preliminary"), user_names)
            baseline = _rating_payload(markers.get("baseline"), user_names)
            status = _cell_status(score_row, prelim, baseline)
            visible_score = float(score_row.score) if score_row is not None else None

            if visible_score is not None:
                score_sum += visible_score
                score_count += 1
                if visible_score < LOW_SCORE_THRESHOLD:
                    low_scores.append({"category": cat.name, "score": visible_score})
            else:
                missing_scores += 1
            if prelim is not None:
                prelim_count += 1
            else:
                missing_prelim += 1
            if baseline is not None:
                baseline_count += 1
            else:
                missing_baseline += 1

            cell = {
                "category_id": cat.id,
                "category": cat.name,
                "score": visible_score,
                "confidence": float(score_row.confidence) if score_row is not None else None,
                "confidence_band": confidence_band(score_row.confidence) if score_row is not None else "low",
                "sample_size": int(score_row.sample_size) if score_row is not None else 0,
                "preliminary": prelim,
                "baseline": baseline,
                "status": status,
            }
            cells.append(cell)
            all_csv_rows.append({
                "_employee_id": emp.id,
                "employee": emp.display_name,
                "title": emp.title or "",
                "role": emp.role or "",
                "competency_tracked": "yes" if emp.competency_tracked else "no",
                "category": cat.name,
                "visible_score": "" if visible_score is None else f"{visible_score:.1f}",
                "confidence": "" if score_row is None else cell["confidence_band"],
                "sample_size": cell["sample_size"],
                "preliminary_score": "" if prelim is None else f"{float(prelim['score']):.1f}",
                "preliminary_by": "" if prelim is None else prelim["created_by_name"],
                "preliminary_notes": "" if prelim is None else prelim["notes"],
                "baseline_score": "" if baseline is None else f"{float(baseline['score']):.1f}",
                "baseline_by": "" if baseline is None else baseline["created_by_name"],
                "baseline_notes": "" if baseline is None else baseline["notes"],
                "status": status,
            })

        average = round(score_sum / score_count, 2) if score_count else None
        emp_status = "complete"
        if missing_prelim:
            emp_status = "needs_preliminary"
        if missing_baseline:
            emp_status = "needs_baseline" if emp_status == "complete" else emp_status + ",needs_baseline"
        if low_scores:
            emp_status = "low_scores" if emp_status == "complete" else emp_status + ",low_scores"
        row = {
            "id": emp.id,
            "display_name": emp.display_name,
            "title": emp.title or "",
            "role": emp.role or "",
            "email": emp.email or "",
            "competency_tracked": emp.competency_tracked,
            "average_score": average,
            "score_count": score_count,
            "missing_score_count": missing_scores,
            "preliminary_count": prelim_count,
            "missing_preliminary_count": missing_prelim,
            "baseline_count": baseline_count,
            "missing_baseline_count": missing_baseline,
            "low_scores": low_scores,
            "status": emp_status,
            "cells": cells,
        }
        if status_filter:
            if status_filter == "complete" and row["status"] != "complete":
                continue
            if status_filter != "complete" and status_filter not in row["status"]:
                continue
        employee_rows.append(row)

    included_ids = {row["id"] for row in employee_rows}
    csv_rows = []
    for raw_row in all_csv_rows:
        if raw_row.get("_employee_id") not in included_ids:
            continue
        row = dict(raw_row)
        row.pop("_employee_id", None)
        csv_rows.append(row)
    category_summary = {
        c.id: {
            "id": c.id,
            "name": c.name,
            "slug": c.slug,
            "score_count": 0,
            "preliminary_count": 0,
            "baseline_count": 0,
            "low_score_count": 0,
            "independent_count": 0,
            "teach_count": 0,
            "average_score": None,
            "_score_sum": 0.0,
        }
        for c in categories
    }
    scored_cells = preliminary_cells = baseline_cells = low_score_cells = 0
    for emp_row in employee_rows:
        for cell in emp_row["cells"]:
            summary = category_summary[cell["category_id"]]
            if cell["score"] is not None:
                scored_cells += 1
                summary["score_count"] += 1
                summary["_score_sum"] += float(cell["score"])
                if float(cell["score"]) < LOW_SCORE_THRESHOLD:
                    low_score_cells += 1
                    summary["low_score_count"] += 1
                if float(cell["score"]) >= 2.0:
                    summary["independent_count"] += 1
                if float(cell["score"]) >= 3.0:
                    summary["teach_count"] += 1
            if cell["preliminary"] is not None:
                preliminary_cells += 1
                summary["preliminary_count"] += 1
            if cell["baseline"] is not None:
                baseline_cells += 1
                summary["baseline_count"] += 1
    for summary in category_summary.values():
        if summary["score_count"]:
            summary["average_score"] = round(summary["_score_sum"] / summary["score_count"], 2)
        summary.pop("_score_sum", None)
    total_cells = len(employee_rows) * len(categories)

    return {
        "generated_at": datetime.utcnow().isoformat(sep=" "),
        "filters": {
            "include_untracked": include_untracked,
            "role": role,
            "q": q,
            "status": status_filter,
        },
        "summary": {
            "employee_count": len(employee_rows),
            "category_count": len(categories),
            "total_cells": total_cells,
            "scored_cells": scored_cells,
            "preliminary_cells": preliminary_cells,
            "baseline_cells": baseline_cells,
            "missing_preliminary_cells": max(0, total_cells - preliminary_cells),
            "missing_baseline_cells": max(0, total_cells - baseline_cells),
            "low_score_cells": low_score_cells,
        },
        "categories": [
            {
                "id": c.id,
                "slug": c.slug,
                "name": c.name,
                "description": c.description or "",
                "tasks": [d.__dict__ for d in dimensions_for_category(c)],
            }
            for c in categories
        ],
        "category_summary": list(category_summary.values()),
        "employees": employee_rows,
        "csv_rows": csv_rows,
    }


def competency_report_csv(packet: dict) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(packet.get("csv_rows") or [])
    return output.getvalue()


__all__ = ["CSV_FIELDS", "competency_report", "competency_report_csv"]
