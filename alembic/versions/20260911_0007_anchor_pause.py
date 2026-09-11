"""Add per-account scheduled-anchor pause state.

Revision ID: 20260911_0007
Revises: 20260910_0006
Create Date: 2026-09-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260911_0007"
down_revision: str | None = "20260910_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "account_schedules",
        sa.Column(
            "anchor_paused",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("account_schedules", "anchor_paused")
