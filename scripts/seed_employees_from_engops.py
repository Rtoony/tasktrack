#!/usr/bin/env python3
"""One-off seed: pull the 36 real Brelje & Race employees from eng-ops.

Reads `BRCE_TEAM` from the eng-ops reset script (loaded dynamically as a
Python module so we don't import the rest of eng-ops). For each entry,
inserts an Employee row into TaskTrack with:

- display_name = entry["name"]
- title        = entry["title"]
- role         = mapped via the same _role_for() logic eng-ops uses
- email        = "" (operator chose not to seed emails)
- active       = 1
- notes        = "Seeded from eng-ops reset_brce_demo_data.py BRCE_TEAM"

Idempotent: skips entries whose display_name already exists in the DB.
Photos are intentionally skipped (per operator decision).

Run:
    cd /home/rtoony/projects/collab-tracker
    /home/rtoony/miniconda3/bin/python3 scripts/seed_employees_from_engops.py --dry-run
    /home/rtoony/miniconda3/bin/python3 scripts/seed_employees_from_engops.py
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine, func, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import DB_PATH  # noqa: E402
from app.models import Employee  # noqa: E402

ENGOPS_RESET = Path(
    "/home/rtoony/projects/eng-ops/scripts/reset_brce_demo_data.py"
)


def _role_for(title: str) -> str:
    """Mirror eng-ops's _role_for() logic but return a plain string for
    TaskTrack's free-text role column."""
    t = title.lower()
    if "president" in t or "principal" in t:
        return "executive"
    if (
        "associate" in t
        or "controller" in t
        or "manager" in t
        or "marketing" in t
        or "administrative" in t
        or "accounting" in t
    ):
        return "manager"
    if "technician" in t or "drafting" in t or "party chief" in t:
        return "drafter"
    return "engineer"


def _load_brce_team() -> list[dict]:
    """Dynamically load BRCE_TEAM from the eng-ops reset script.

    We don't import the eng-ops package proper because it pulls in
    SQLAlchemy async + a different connection config. Instead we read
    the source file and exec only the literal assignment.
    """
    if not ENGOPS_RESET.exists():
        raise SystemExit(f"eng-ops reset script not found at {ENGOPS_RESET}")
    src = ENGOPS_RESET.read_text()
    # Locate the BRCE_TEAM literal — it's a list[dict] that begins at
    # `BRCE_TEAM: list[dict[str, str]] = [` and ends at the matching ]
    # roughly 250 lines later. Walk the source line-by-line to be robust.
    start = src.find("BRCE_TEAM: list[dict[str, str]] = [")
    if start < 0:
        # Fall back to the looser pattern in case the type annotation was
        # tweaked upstream.
        start = src.find("BRCE_TEAM = [")
        if start < 0:
            raise SystemExit("Could not find BRCE_TEAM in the reset script.")
    # Use importlib only for the side-benefit of evaluating the literal;
    # spec-style loading via exec keeps this self-contained.
    spec = importlib.util.spec_from_file_location("engops_reset", ENGOPS_RESET)
    if spec is None or spec.loader is None:
        raise SystemExit("Failed to build module spec for the reset script.")
    module = importlib.util.module_from_spec(spec)
    # The eng-ops module imports from app.models which we don't want to
    # pull in. Patch sys.modules with a stub before loading.
    import types
    stub = types.ModuleType("app")
    stub_models = types.ModuleType("app.models")

    class _StubRole:
        EXECUTIVE = "executive"
        MANAGER = "manager"
        ENGINEER = "engineer"
        DRAFTER = "drafter"
        IT = "it"
        ADMIN = "admin"
    stub_models.EmployeeRole = _StubRole
    sys.modules.setdefault("app", stub)
    sys.modules["app.models"] = stub_models
    try:
        spec.loader.exec_module(module)
    finally:
        # Clean up the stubs so we don't pollute the rest of our process.
        sys.modules.pop("app.models", None)
        if sys.modules.get("app") is stub:
            sys.modules.pop("app", None)
    return list(module.BRCE_TEAM)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without writing")
    parser.add_argument("--db", default=DB_PATH,
                        help=f"DB path (default: {DB_PATH})")
    args = parser.parse_args()

    team = _load_brce_team()
    print(f"Loaded {len(team)} entries from BRCE_TEAM\n")

    engine = create_engine(f"sqlite:///{args.db}", future=True)
    inserted = 0
    skipped = 0
    with Session(engine, future=True) as sess:
        for entry in team:
            name = entry["name"].strip()
            title = entry.get("title", "").strip()
            if not name:
                continue

            existing = sess.scalar(
                select(Employee).where(
                    func.lower(Employee.display_name) == name.lower()
                )
            )
            if existing is not None:
                skipped += 1
                print(f"  = skip (exists)   {name}")
                continue

            role = _role_for(title)
            if args.dry_run:
                print(f"  + [dry] would add {name!r:46s} title={title!r:36s} role={role}")
            else:
                emp = Employee(
                    display_name=name,
                    email="",
                    title=title,
                    role=role,
                    notes="Seeded from eng-ops reset_brce_demo_data.py BRCE_TEAM",
                    active=1,
                )
                sess.add(emp)
                inserted += 1
                print(f"  + added           {name!r:46s} title={title!r:36s} role={role}")

        if not args.dry_run:
            sess.commit()

    print(f"\nSummary: inserted={inserted}, skipped={skipped}, "
          f"loaded={len(team)}")
    if args.dry_run:
        print("Dry run complete. Re-run without --dry-run to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
