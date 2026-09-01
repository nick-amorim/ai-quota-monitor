"""Create account configuration tables.

Revision ID: 20260831_0001
Revises:
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("slug", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("codex_home", sa.String(length=500), nullable=False),
        sa.Column("workspace_path", sa.String(length=500), nullable=False),
        sa.Column("account_external_id", sa.String(length=255), nullable=True),
        sa.Column("account_display", sa.String(length=255), nullable=True),
        sa.Column("plan_type", sa.String(length=80), nullable=True),
        sa.Column("auth_status", sa.String(length=32), nullable=False),
        sa.Column("last_auth_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(op.f("ix_accounts_slug"), "accounts", ["slug"], unique=True)

    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "account_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("daily_anchor_enabled", sa.Boolean(), nullable=False),
        sa.Column("daily_anchor_time", sa.Time(), nullable=False),
        sa.Column("weekly_target_day", sa.String(length=16), nullable=False),
        sa.Column("weekly_target_time", sa.Time(), nullable=False),
        sa.Column("timezone", sa.String(length=80), nullable=False),
        sa.Column("monday_enabled", sa.Boolean(), nullable=False),
        sa.Column("tuesday_enabled", sa.Boolean(), nullable=False),
        sa.Column("wednesday_enabled", sa.Boolean(), nullable=False),
        sa.Column("thursday_enabled", sa.Boolean(), nullable=False),
        sa.Column("friday_enabled", sa.Boolean(), nullable=False),
        sa.Column("saturday_enabled", sa.Boolean(), nullable=False),
        sa.Column("sunday_enabled", sa.Boolean(), nullable=False),
        sa.Column("skip_if_window_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.UniqueConstraint("account_id"),
    )


def downgrade() -> None:
    op.drop_table("account_schedules")
    op.drop_table("app_settings")
    op.drop_index(op.f("ix_accounts_slug"), table_name="accounts")
    op.drop_table("accounts")
