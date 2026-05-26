"""rename internal item categories

Revision ID: 7c9a1e3f4d2b
Revises: a4e317c5b69d
Create Date: 2026-05-26

Rename legacy category values to neutral internal operations queues.
The table name stays unchanged for compatibility; a later migration can rename
the storage layer once API, bridge, and test references are ready.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "7c9a1e3f4d2b"
down_revision: Union[str, Sequence[str], None] = "a4e317c5b69d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_UP = {
    "Husband": "Follow-up",
    "Father": "Meetings",
    "House": "Office",
    "Cars": "Assets",
}
_DOWN = {value: key for key, value in _UP.items()}


def _rename(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    for old, new in mapping.items():
        bind.exec_driver_sql(
            "UPDATE personal_items SET category = ? WHERE category = ?",
            (new, old),
        )


def upgrade() -> None:
    _rename(_UP)


def downgrade() -> None:
    _rename(_DOWN)
