from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Time, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_quota_monitor.database import Base


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    codex_home: Mapped[str] = mapped_column(String(500), nullable=False)
    workspace_path: Mapped[str] = mapped_column(String(500), nullable=False)

    account_external_id: Mapped[str | None] = mapped_column(String(255))
    account_display: Mapped[str | None] = mapped_column(String(255))
    plan_type: Mapped[str | None] = mapped_column(String(80))

    auth_status: Mapped[str] = mapped_column(String(32), default="not_configured", nullable=False)
    last_auth_check: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    schedule: Mapped["AccountSchedule"] = relationship(
        back_populates="account",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AccountSchedule(Base):
    __tablename__ = "account_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), unique=True, nullable=False)

    daily_anchor_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_anchor_time: Mapped[time] = mapped_column(Time, nullable=False)

    weekly_target_day: Mapped[str] = mapped_column(String(16), nullable=False)
    weekly_target_time: Mapped[time] = mapped_column(Time, nullable=False)

    timezone: Mapped[str] = mapped_column(String(80), nullable=False)

    monday_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    tuesday_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    wednesday_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    thursday_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    friday_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    saturday_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sunday_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    skip_if_window_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    account: Mapped[Account] = relationship(back_populates="schedule")
