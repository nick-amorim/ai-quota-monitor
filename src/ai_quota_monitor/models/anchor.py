from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_quota_monitor.database import Base


class AnchorRun(Base):
    __tablename__ = "anchor_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)

    thread_id: Mapped[str | None] = mapped_column(String(255))
    turn_id: Mapped[str | None] = mapped_column(String(255))
    final_response: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    token_usage_json: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    account = relationship("Account")


class DailyAnchorRecovery(Base):
    """The single outstanding reset-based retry for an account."""

    __tablename__ = "daily_anchor_recoveries"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    originating_anchor_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("anchor_runs.id")
    )
    origin_decision: Mapped[str] = mapped_column(String(80), nullable=False)
    reset_source: Mapped[str] = mapped_column(String(80), nullable=False)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generation: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    collision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    account = relationship("Account")
    originating_anchor_run = relationship("AnchorRun")
