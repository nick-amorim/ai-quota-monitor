"""Add expected and observed reset timestamps.

Revision ID: 20260908_0005
Revises: 20260908_0004
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260908_0005"
down_revision: str | None = "20260908_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "usage_snapshots",
        sa.Column("five_hour_expected_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "usage_snapshots",
        sa.Column("five_hour_observed_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "usage_snapshots",
        sa.Column("weekly_expected_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "usage_snapshots",
        sa.Column("weekly_observed_reset_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        "UPDATE usage_snapshots "
        "SET five_hour_observed_reset_at = five_hour_reset_at, "
        "weekly_observed_reset_at = weekly_reset_at"
    )


def downgrade() -> None:
    op.drop_column("usage_snapshots", "weekly_observed_reset_at")
    op.drop_column("usage_snapshots", "weekly_expected_reset_at")
    op.drop_column("usage_snapshots", "five_hour_observed_reset_at")
    op.drop_column("usage_snapshots", "five_hour_expected_reset_at")
