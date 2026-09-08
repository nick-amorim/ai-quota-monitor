from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.models import EventLog


@dataclass(frozen=True)
class EventFilters:
    account_id: int | None = None
    level: str | None = None
    category: str | None = None


def record_event(
    session: Session,
    *,
    level: str,
    category: str,
    message: str,
    account_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> EventLog:
    event = EventLog(
        account_id=account_id,
        created_at=datetime.now(UTC),
        level=level,
        category=category,
        message=message,
        payload_json=json.dumps(payload, sort_keys=True) if payload is not None else None,
    )
    session.add(event)
    session.commit()
    session.refresh(event)
    return event


def recent_events(
    session_factory: sessionmaker[Session],
    *,
    limit: int = 20,
) -> list[EventLog]:
    return list_events(session_factory, filters=EventFilters(), limit=limit)


def list_events(
    session_factory: sessionmaker[Session],
    *,
    filters: EventFilters,
    limit: int = 100,
) -> list[EventLog]:
    with session_factory() as session:
        query = select(EventLog).options(selectinload(EventLog.account))
        if filters.account_id is not None:
            query = query.where(EventLog.account_id == filters.account_id)
        if filters.level:
            query = query.where(EventLog.level == filters.level)
        if filters.category:
            query = query.where(EventLog.category.like(f"{filters.category}%"))

        query = (
            query.order_by(EventLog.created_at.desc(), EventLog.id.desc())
            .limit(limit)
        )
        return list(session.scalars(query))
