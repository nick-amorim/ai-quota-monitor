"""Create usage telemetry tables.

Revision ID: 20260903_0003
Revises: 20260902_0002
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0003"
down_revision: str | None = "20260902_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_raw",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_usage_raw_account_id"), "usage_raw", ["account_id"])

    op.create_table(
        "usage_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("five_hour_used_percent", sa.Float(), nullable=True),
        sa.Column("five_hour_window_minutes", sa.Integer(), nullable=True),
        sa.Column("five_hour_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("weekly_used_percent", sa.Float(), nullable=True),
        sa.Column("weekly_window_minutes", sa.Integer(), nullable=True),
        sa.Column("weekly_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parser_status", sa.String(length=32), nullable=False),
        sa.Column("parser_message", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("raw_usage_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["raw_usage_id"], ["usage_raw.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_usage_snapshots_account_id"),
        "usage_snapshots",
        ["account_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_snapshots_account_id"), table_name="usage_snapshots")
    op.drop_table("usage_snapshots")
    op.drop_index(op.f("ix_usage_raw_account_id"), table_name="usage_raw")
    op.drop_table("usage_raw")
