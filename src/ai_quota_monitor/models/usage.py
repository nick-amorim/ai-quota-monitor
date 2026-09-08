from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_quota_monitor.database import Base


class UsageRaw(Base):
    __tablename__ = "usage_raw"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account = relationship("Account")


class UsageSnapshot(Base):
    __tablename__ = "usage_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    five_hour_used_percent: Mapped[float | None] = mapped_column(Float)
    five_hour_window_minutes: Mapped[int | None] = mapped_column(Integer)
    five_hour_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    five_hour_expected_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    five_hour_observed_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    weekly_used_percent: Mapped[float | None] = mapped_column(Float)
    weekly_window_minutes: Mapped[int | None] = mapped_column(Integer)
    weekly_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    weekly_expected_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    weekly_observed_reset_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    parser_status: Mapped[str] = mapped_column(String(32), nullable=False)
    parser_message: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    raw_usage_id: Mapped[int | None] = mapped_column(ForeignKey("usage_raw.id"))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account = relationship("Account")
    raw_usage = relationship("UsageRaw")
