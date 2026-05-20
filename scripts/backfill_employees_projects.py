#!/usr/bin/env python3
"""Phase-0 backfill: text → FK on existing tracker rows.

Idempotent. Safe to re-run. Reads the live DB through the same engine
the app uses, scans distinct text values from the four trackers, inserts
matching rows into `employees` and `projects`, then updates the `*_id`
columns on rows where the text value uniquely matches one registry row.

Run with:
    cd /home/rtoony/projects/collab-tracker
    /home/rtoony/miniconda3/bin/python3 scripts/backfill_employees_projects.py

Use --dry-run to see what would happen without writing.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

# Make the app package importable when running from project root.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import DB_PATH  # noqa: E402
from app.models import (  # noqa: E402
    Employee,
    PersonnelIssue,
    Project,
    ProjectWorkTask,
    TrainingTask,
    WorkTask,
)

PROJECT_NUMBER_TABLES = [
    (WorkTask, "project_number", "project_id"),
    (ProjectWorkTask, "project_number", "project_id"),
    (TrainingTask, "project_number", "project_id"),
    (PersonnelIssue, "project_number", "project_id"),
]

PEOPLE_TABLES = [
    (ProjectWorkTask, "engineer", "engineer_id"),
    (PersonnelIssue, "person_name", "person_id"),
]


def _distinct_values(sess: Session, model, column_name: str) -> list[str]:
    """Return distinct non-empty text values for one column."""
    col = getattr(model, column_name)
    rows = sess.scalars(
        select(col).where(col.is_not(None)).where(col != "").distinct()
    ).all()
    seen = set()
    out = []
    for v in rows:
        s = (v or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _seed_projects(sess: Session, dry: bool) -> dict[str, int]:
    """Insert distinct project_number values into projects; return name→id."""
    distinct_pns: set[str] = set()
    for model, text_col, _ in PROJECT_NUMBER_TABLES:
        distinct_pns.update(_distinct_values(sess, model, text_col))

    name_to_id: dict[str, int] = {}
    for pn in sorted(distinct_pns):
        hit = sess.scalar(select(Project).where(Project.project_number == pn))
        if hit is not None:
            name_to_id[pn] = hit.id
            continue
        if dry:
            print(f"  [dry] would create project {pn!r}")
            name_to_id[pn] = -1
            continue
        proj = Project(project_number=pn, name="")
        sess.add(proj)
        sess.flush()
        name_to_id[pn] = proj.id
        print(f"  + created project {pn!r} (id={proj.id})")
    return name_to_id


def _seed_employees(sess: Session, dry: bool) -> dict[str, int]:
    """Insert distinct people names. Returns lowercase name → id."""
    distinct_names: Counter[str] = Counter()
    for model, text_col, _ in PEOPLE_TABLES:
        for v in _distinct_values(sess, model, text_col):
            # Skip comma-separated lists (trainees may carry "Alice, Bob") —
            # those need manual cleanup; ambiguous backfill is worse than
            # no backfill.
            if "," in v:
                continue
            distinct_names[v] += 1

    name_to_id: dict[str, int] = {}
    for name in sorted(distinct_names):
        existing = sess.scalar(
            select(Employee).where(
                func.lower(Employee.display_name) == name.lower()
            )
        )
        if existing is not None:
            name_to_id[name.lower()] = existing.id
            continue
        if dry:
            print(f"  [dry] would create employee {name!r}")
            name_to_id[name.lower()] = -1
            continue
        emp = Employee(display_name=name)
        sess.add(emp)
        sess.flush()
        name_to_id[name.lower()] = emp.id
        print(f"  + created employee {name!r} (id={emp.id})")
    return name_to_id


def _populate_fks(sess: Session, dry: bool,
                  projects: dict[str, int], people: dict[str, int]) -> int:
    """Update *_id columns where text matches a unique registry row."""
    updated = 0
    for model, text_col, fk_col in PROJECT_NUMBER_TABLES:
        rows = sess.scalars(
            select(model).where(getattr(model, fk_col).is_(None))
        ).all()
        for row in rows:
            text = (getattr(row, text_col) or "").strip()
            if not text or text not in projects:
                continue
            target_id = projects[text]
            if target_id < 0:
                continue
            if dry:
                print(f"  [dry] would set {model.__tablename__}#{row.id}."
                      f"{fk_col} = {target_id}")
            else:
                setattr(row, fk_col, target_id)
            updated += 1

    for model, text_col, fk_col in PEOPLE_TABLES:
        rows = sess.scalars(
            select(model).where(getattr(model, fk_col).is_(None))
        ).all()
        for row in rows:
            text = (getattr(row, text_col) or "").strip()
            if not text or "," in text:
                continue
            target_id = people.get(text.lower())
            if not target_id or target_id < 0:
                continue
            if dry:
                print(f"  [dry] would set {model.__tablename__}#{row.id}."
                      f"{fk_col} = {target_id}")
            else:
                setattr(row, fk_col, target_id)
            updated += 1
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without writing")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"DB path (default: {DB_PATH})")
    args = parser.parse_args()

    print(f"Using DB: {args.db}")
    if args.dry_run:
        print("DRY RUN — no changes will be written\n")

    engine = create_engine(f"sqlite:///{args.db}", future=True)
    with Session(engine, future=True) as sess:
        print("Seeding projects…")
        projects = _seed_projects(sess, args.dry_run)
        print(f"  ({len(projects)} project numbers known)\n")

        print("Seeding employees…")
        people = _seed_employees(sess, args.dry_run)
        print(f"  ({len(people)} display names known)\n")

        print("Populating FK columns…")
        updated = _populate_fks(sess, args.dry_run, projects, people)
        print(f"  ({updated} rows {'would be' if args.dry_run else ''} updated)\n")

        if not args.dry_run:
            sess.commit()
            print("Committed.")
        else:
            print("Dry run complete. Re-run without --dry-run to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
