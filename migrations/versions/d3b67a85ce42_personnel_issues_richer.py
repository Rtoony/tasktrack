"""personnel_issues: time_loss + immediate_solution + skill_category_id

Revision ID: d3b67a85ce42
Revises: c2d8a91f4736
Create Date: 2026-05-20

Phase 2 of the eng-ops absorption (~/.claude/plans/tingly-crafting-seal.md).
Adds three columns to personnel_issues so the existing "Capabilities"
tracker gets closer to eng-ops's incident_reports shape. Purely
additive — `person_id` and `project_id` were already added in Phase 0;
`skill_category_id` now refers to the table created in Phase 1.

Recommendation from the plan was to EXTEND personnel_issues in place
(not rename to incident_reports) because the table_name string literal
appears in 6+ polymorphic key locations (activity_log, comments,
attachments, links, the search SQL UNION, FORMS.personnel,
ADMIN_WORKFLOW_VIEWS) and the UI label is already "Capabilities."
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd3b67a85ce42'
down_revision: Union[str, Sequence[str], None] = 'c2d8a91f4736'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.add_column(sa.Column(
            'estimated_time_loss_minutes', sa.Integer(),
            server_default=sa.text('0'), nullable=False,
        ))
        batch.add_column(sa.Column(
            'immediate_solution', sa.Text(),
            server_default=sa.text("('')"), nullable=False,
        ))
        batch.add_column(sa.Column(
            'skill_category_id', sa.Integer(), nullable=True,
        ))


def downgrade() -> None:
    with op.batch_alter_table('personnel_issues', schema=None) as batch:
        batch.drop_column('skill_category_id')
        batch.drop_column('immediate_solution')
        batch.drop_column('estimated_time_loss_minutes')
