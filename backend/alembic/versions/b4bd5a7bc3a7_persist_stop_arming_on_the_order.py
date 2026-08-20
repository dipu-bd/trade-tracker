"""Persist stop arming on the order

Revision ID: b4bd5a7bc3a7
Revises: 1aa283dbe2ea
Create Date: 2026-08-20 12:32:58.922071+00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4bd5a7bc3a7"
down_revision: str | Sequence[str] | None = "1aa283dbe2ea"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A server default so the column can land NOT NULL on a table that already has orders;
    # every one of them predates arming and so is correctly false.
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("stop_armed", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.alter_column("stop_armed", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_column("stop_armed")
