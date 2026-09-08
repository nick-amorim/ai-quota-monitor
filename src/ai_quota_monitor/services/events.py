from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from ai_quota_monitor.models import EventLog


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
    with session_factory() as session:
        return list(
            session.scalars(
                select(EventLog)
                .options(selectinload(EventLog.account))
                .order_by(EventLog.created_at.desc(), EventLog.id.desc())
                .limit(limit)
            )
        )
