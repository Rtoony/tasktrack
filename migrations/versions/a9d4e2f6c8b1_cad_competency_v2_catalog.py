"""CAD competency v2 catalog.

Revision ID: a9d4e2f6c8b1
Revises: 8ac94d2e5170
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9d4e2f6c8b1"
down_revision: Union[str, Sequence[str], None] = "8ac94d2e5170"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_DEFAULT_SLUGS = (
    "project-setup",
    "cad-standards",
    "civil-design",
    "survey-coordination",
    "qa-qc-review",
    "sheet-production",
    "permitting",
    "construction-support",
    "client-communication",
    "software-proficiency",
)

V2_CATEGORIES = (
    ("computer-windows-literacy", "Computer & Windows Literacy", "Basic computer, Windows, Office, PDF, file, and troubleshooting fluency.", 10),
    ("autocad-core", "AutoCAD Core", "Core AutoCAD production skill, references, plotting, cleanup, and sheet setup.", 20),
    ("cad-standards-drawing-discipline", "CAD Standards & Drawing Discipline", "Firm standards, templates, title blocks, naming, styles, and drawing organization.", 30),
    ("civil-3d", "Civil 3D", "Civil 3D points, surfaces, alignments, profiles, grading, networks, DREFs, and styles.", 40),
    ("plan-production-deliverables", "Plan Production & Deliverables", "Plan sheets, details, callouts, legends, revisions, final PDFs, and deliverable consistency.", 50),
    ("gis-other-software", "GIS & Other Software", "GIS, coordinate systems, projections, geospatial import/export, and specialty tools.", 60),
)


def _qmarks(values):
    return ",".join([":v%d" % i for i, _ in enumerate(values)])


def upgrade() -> None:
    bind = op.get_bind()
    if OLD_DEFAULT_SLUGS:
        params = {"v%d" % i: slug for i, slug in enumerate(OLD_DEFAULT_SLUGS)}
        bind.execute(
            sa.text(f"UPDATE skill_categories SET active=0, updated_at=CURRENT_TIMESTAMP WHERE slug IN ({_qmarks(OLD_DEFAULT_SLUGS)})"),
            params,
        )
    for slug, name, description, display_order in V2_CATEGORIES:
        existing = bind.execute(sa.text("SELECT id FROM skill_categories WHERE slug = :slug"), {"slug": slug}).first()
        if existing:
            bind.execute(
                sa.text(
                    "UPDATE skill_categories "
                    "SET name=:name, description=:description, display_order=:display_order, active=1, updated_at=CURRENT_TIMESTAMP "
                    "WHERE slug=:slug"
                ),
                {"slug": slug, "name": name, "description": description, "display_order": display_order},
            )
        else:
            bind.execute(
                sa.text(
                    "INSERT INTO skill_categories (slug, name, description, display_order, active) "
                    "VALUES (:slug, :name, :description, :display_order, 1)"
                ),
                {"slug": slug, "name": name, "description": description, "display_order": display_order},
            )


def downgrade() -> None:
    bind = op.get_bind()
    params = {"v%d" % i: slug for i, slug in enumerate([row[0] for row in V2_CATEGORIES])}
    bind.execute(
        sa.text(f"UPDATE skill_categories SET active=0, updated_at=CURRENT_TIMESTAMP WHERE slug IN ({_qmarks([row[0] for row in V2_CATEGORIES])})"),
        params,
    )
    params = {"v%d" % i: slug for i, slug in enumerate(OLD_DEFAULT_SLUGS)}
    bind.execute(
        sa.text(f"UPDATE skill_categories SET active=1, updated_at=CURRENT_TIMESTAMP WHERE slug IN ({_qmarks(OLD_DEFAULT_SLUGS)})"),
        params,
    )
