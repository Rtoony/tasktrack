"""projects: lat/lng/display_status (Atlas-lite)

Revision ID: b8d51c92e711
Revises: e7a4c218bf30
Create Date: 2026-05-19

Phase 0.5 (Atlas-lite) — adds three columns to the projects table so we
can render projects on an embedded Leaflet map. Future phases may add
polygon geometry or a separate work_areas table; today's scope is just
"where is this project on a map?".

- lat (REAL, nullable): decimal degrees, e.g. 38.4404
- lng (REAL, nullable): decimal degrees, e.g. -122.7141
- display_status (TEXT, default 'active'): drives map-pin color and
  filtering. Values: active | dormant | completed | draft | review.
  Distinct from `active` (which is the soft-delete flag).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d51c92e711'
down_revision: Union[str, Sequence[str], None] = 'e7a4c218bf30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch:
        batch.add_column(sa.Column('lat', sa.Float(), nullable=True))
        batch.add_column(sa.Column('lng', sa.Float(), nullable=True))
        batch.add_column(sa.Column(
            'display_status', sa.Text(),
            server_default=sa.text("('active')"), nullable=False,
        ))


def downgrade() -> None:
    with op.batch_alter_table('projects', schema=None) as batch:
        batch.drop_column('display_status')
        batch.drop_column('lng')
        batch.drop_column('lat')
