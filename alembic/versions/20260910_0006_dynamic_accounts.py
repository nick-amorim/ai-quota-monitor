"""Add account lifecycle metadata.

Revision ID: 20260910_0006
Revises: 20260908_0005
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260910_0006"
down_revision: str | None = "20260908_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "accounts",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )

    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT id FROM accounts ORDER BY slug")
    ).fetchall()
    for index, row in enumerate(rows, start=1):
        connection.execute(
            sa.text("UPDATE accounts SET sort_order = :sort_order WHERE id = :id"),
            {"sort_order": index, "id": row.id},
        )


def downgrade() -> None:
    op.drop_column("accounts", "archived_at")
    op.drop_column("accounts", "sort_order")
