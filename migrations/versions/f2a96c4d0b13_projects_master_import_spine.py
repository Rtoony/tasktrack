"""projects: master-list fields + project_sites multi-site table

Revision ID: f2a96c4d0b13
Revises: e1c47d8a9b22
Create Date: 2026-05-22

Adds the four metadata columns the master-list spreadsheet provides
(component, principal, start_date, dormant_date) plus two indexes for
the new map filters (component, client). Creates a new project_sites
child table so projects with more than one pin location (the worst is
"209" with 69 sites) keep all of them — `projects.lat`/`projects.lng`
stays in place and mirrors the project's primary site for backward
compatibility with the existing geojson endpoint and ticket map widget.

Display-status values predating this migration get normalized:
  completed/review/draft -> dormant
  (active is left as-is)
The post-import enum is just {"active", "dormant"} to match the
spreadsheet's binary Status column. The column stays plain TEXT (no DB
CHECK constraint) — the route layer validates, mirroring how the rest
of the trackers handle their string enums.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f2a96c4d0b13'
down_revision: Union[str, Sequence[str], None] = 'e1c47d8a9b22'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch:
        batch.add_column(sa.Column(
            'component', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.add_column(sa.Column(
            'principal', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.add_column(sa.Column(
            'start_date', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.add_column(sa.Column(
            'dormant_date', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.create_index('idx_projects_component', ['component'])
        batch.create_index('idx_projects_client', ['client'])

    # Normalize any legacy display_status values into the new
    # {active, dormant} enum. Today the projects table is empty so this
    # is a no-op; it's here so this migration is correct if it later
    # runs against a db that picked up rows via the admin UI before the
    # import script ran.
    op.execute(
        "UPDATE projects "
        "SET display_status = 'dormant' "
        "WHERE display_status IN ('completed', 'review', 'draft')"
    )

    op.create_table(
        'project_sites',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('lat', sa.Float(), nullable=False),
        sa.Column('lng', sa.Float(), nullable=False),
        sa.Column('pin_color', sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column('raw_name', sa.Text(), nullable=False, server_default=sa.text("('')")),
        sa.Column('is_primary', sa.Integer(), nullable=False, server_default=sa.text("(0)")),
        sa.Column('source', sa.Text(), nullable=False, server_default=sa.text("('kmz')")),
        sa.Column(
            'created_at', sa.TIMESTAMP(),
            server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False,
        ),
    )
    op.create_index('idx_project_sites_project_id', 'project_sites', ['project_id'])
    op.create_index('idx_project_sites_pin_color', 'project_sites', ['pin_color'])


def downgrade() -> None:
    op.drop_index('idx_project_sites_pin_color', table_name='project_sites')
    op.drop_index('idx_project_sites_project_id', table_name='project_sites')
    op.drop_table('project_sites')

    with op.batch_alter_table('projects', schema=None) as batch:
        batch.drop_index('idx_projects_client')
        batch.drop_index('idx_projects_component')
        batch.drop_column('dormant_date')
        batch.drop_column('start_date')
        batch.drop_column('principal')
        batch.drop_column('component')
