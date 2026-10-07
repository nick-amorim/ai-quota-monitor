"""Persist reset-based daily anchor recoveries.

Revision ID: 20261007_0008
Revises: 20260911_0007
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261007_0008"
down_revision: str | None = "20260911_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "daily_anchor_recoveries",
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("originating_anchor_run_id", sa.Integer(), nullable=True),
        sa.Column("origin_decision", sa.String(length=80), nullable=False),
        sa.Column("reset_source", sa.String(length=80), nullable=False),
        sa.Column("reset_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.String(length=36), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("collision_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["originating_anchor_run_id"], ["anchor_runs.id"]),
        sa.PrimaryKeyConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("daily_anchor_recoveries")
