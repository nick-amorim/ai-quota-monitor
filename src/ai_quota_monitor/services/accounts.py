from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ai_quota_monitor.config import Settings
from ai_quota_monitor.models import Account, AccountSchedule, AppSetting

WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)

DEFAULT_APP_SETTINGS = {
    "timezone": "America/Recife",
    "usage_poll_interval_minutes": "5",
    "anchor_prompt": "Reply only with OK.",
    "anchor_verification_timeout_seconds": "60",
    "missed_anchor_policy": "run_if_within_grace",
    "missed_anchor_grace_minutes": "30",
    "history_retention_days": "90",
    "dashboard_refresh_interval_seconds": "10",
}


@dataclass(frozen=True)
class AccountSeed:
    name: str
    slug: str
    daily_anchor_time: time
    weekly_target_day: str
    weekly_target_time: time


def default_account_seeds(settings: Settings) -> tuple[AccountSeed, AccountSeed]:
    return (
        AccountSeed("Account A", "account-a", time(5, 0), "monday", time(5, 0)),
        AccountSeed("Account B", "account-b", time(9, 0), "wednesday", time(9, 0)),
    )


def default_app_settings(settings: Settings) -> dict[str, str]:
    return {
        **DEFAULT_APP_SETTINGS,
        "timezone": settings.timezone,
        "usage_poll_interval_minutes": str(settings.usage_poll_interval_minutes),
        "anchor_prompt": settings.anchor_prompt,
        "anchor_verification_timeout_seconds": str(
            settings.anchor_verification_timeout_seconds
        ),
        "missed_anchor_policy": settings.missed_anchor_policy,
        "missed_anchor_grace_minutes": str(settings.missed_anchor_grace_minutes),
        "history_retention_days": str(settings.history_retention_days),
        "dashboard_refresh_interval_seconds": str(
            settings.dashboard_refresh_interval_seconds
        ),
    }


def seed_defaults(session: Session, settings: Settings) -> None:
    for key, value in default_app_settings(settings).items():
        setting = session.get(AppSetting, key)
        if setting is None:
            session.add(AppSetting(key=key, value=value))

    data_root = settings.data_dir.expanduser().resolve()
    for index, seed in enumerate(default_account_seeds(settings), start=1):
        account = session.scalar(select(Account).where(Account.slug == seed.slug))
        if account is None:
            account_root = data_root / seed.slug
            account = Account(
                name=seed.name,
                slug=seed.slug,
                enabled=True,
                sort_order=index,
                codex_home=str(account_root / "codex-home"),
                workspace_path=str(account_root / "workspace"),
            )
            session.add(account)
            session.flush()
        elif account.sort_order == 0:
            account.sort_order = index

        if account.schedule is None:
            session.add(
                AccountSchedule(
                    account_id=account.id,
                    daily_anchor_enabled=True,
                    daily_anchor_time=seed.daily_anchor_time,
                    weekly_target_day=seed.weekly_target_day,
                    weekly_target_time=seed.weekly_target_time,
                    timezone=settings.timezone,
                    monday_enabled=True,
                    tuesday_enabled=True,
                    wednesday_enabled=True,
                    thursday_enabled=True,
                    friday_enabled=True,
                    saturday_enabled=False,
                    sunday_enabled=False,
                    skip_if_window_active=True,
                )
            )

    session.commit()


def create_account(
    session: Session,
    settings: Settings,
    *,
    name: str | None = None,
    enabled: bool = True,
    daily_anchor_enabled: bool = True,
    daily_anchor_time: time = time(9, 0),
    weekly_target_day: str = "monday",
    weekly_target_time: time = time(9, 0),
    timezone: str | None = None,
    active_weekdays: set[str] | None = None,
    skip_if_window_active: bool = True,
) -> Account:
    if weekly_target_day not in WEEKDAYS:
        raise ValueError("weekly_target_day must be a weekday")

    if active_weekdays is None:
        active_weekdays = {"monday", "tuesday", "wednesday", "thursday", "friday"}
    invalid_weekdays = active_weekdays.difference(WEEKDAYS)
    if invalid_weekdays:
        raise ValueError(f"Invalid weekdays: {', '.join(sorted(invalid_weekdays))}")

    number = _next_account_number(session)
    slug = _unique_slug(session, f"account-{number}")
    account_root = settings.data_dir.expanduser().resolve() / slug
    account = Account(
        name=(name or f"Account {number}").strip() or f"Account {number}",
        slug=slug,
        enabled=enabled,
        sort_order=_next_sort_order(session),
        codex_home=str(account_root / "codex-home"),
        workspace_path=str(account_root / "workspace"),
    )
    account.schedule = AccountSchedule(
        daily_anchor_enabled=daily_anchor_enabled,
        daily_anchor_time=daily_anchor_time,
        weekly_target_day=weekly_target_day,
        weekly_target_time=weekly_target_time,
        timezone=timezone or settings.timezone,
        skip_if_window_active=skip_if_window_active,
    )
    for weekday in WEEKDAYS:
        setattr(account.schedule, f"{weekday}_enabled", weekday in active_weekdays)

    session.add(account)
    session.commit()
    session.refresh(account)
    create_runtime_directories(account)
    return account


def archive_account(session: Session, account: Account) -> Account:
    account.enabled = False
    account.archived_at = datetime.now(UTC)
    session.commit()
    session.refresh(account)
    return account


def create_runtime_directories(account: Account) -> None:
    for path in (account.codex_home, account.workspace_path):
        Path(path).expanduser().mkdir(parents=True, exist_ok=True)


def list_accounts(session: Session, *, include_archived: bool = False) -> list[Account]:
    query = select(Account).options(selectinload(Account.schedule))
    if not include_archived:
        query = query.where(Account.archived_at.is_(None))
    return list(
        session.scalars(
            query.order_by(Account.sort_order, Account.slug)
        )
    )


def get_account(
    session: Session,
    account_id: int,
    *,
    include_archived: bool = False,
) -> Account | None:
    query = (
        select(Account)
        .options(selectinload(Account.schedule))
        .where(Account.id == account_id)
    )
    if not include_archived:
        query = query.where(Account.archived_at.is_(None))
    return session.scalar(query)


def update_account_schedule(
    session: Session,
    account: Account,
    *,
    enabled: bool,
    daily_anchor_enabled: bool,
    daily_anchor_time: time,
    weekly_target_day: str,
    weekly_target_time: time,
    timezone: str,
    active_weekdays: set[str],
    skip_if_window_active: bool,
) -> Account:
    if weekly_target_day not in WEEKDAYS:
        raise ValueError("weekly_target_day must be a weekday")

    invalid_weekdays = active_weekdays.difference(WEEKDAYS)
    if invalid_weekdays:
        raise ValueError(f"Invalid weekdays: {', '.join(sorted(invalid_weekdays))}")

    account.enabled = enabled
    if account.schedule is None:
        account.schedule = AccountSchedule(
            daily_anchor_enabled=daily_anchor_enabled,
            daily_anchor_time=daily_anchor_time,
            weekly_target_day=weekly_target_day,
            weekly_target_time=weekly_target_time,
            timezone=timezone,
            skip_if_window_active=skip_if_window_active,
        )
    else:
        account.schedule.daily_anchor_enabled = daily_anchor_enabled
        account.schedule.daily_anchor_time = daily_anchor_time
        account.schedule.weekly_target_day = weekly_target_day
        account.schedule.weekly_target_time = weekly_target_time
        account.schedule.timezone = timezone
        account.schedule.skip_if_window_active = skip_if_window_active

    for weekday in WEEKDAYS:
        setattr(account.schedule, f"{weekday}_enabled", weekday in active_weekdays)

    session.commit()
    session.refresh(account)
    return account


def ensure_runtime_directories(settings: Settings, session: Session | None = None) -> None:
    data_root = settings.data_dir.expanduser().resolve()
    if session is None:
        for seed in default_account_seeds(settings):
            account_root = data_root / seed.slug
            for path in (account_root / "codex-home", account_root / "workspace"):
                Path(path).mkdir(parents=True, exist_ok=True)
        return

    for account in list_accounts(session, include_archived=True):
        create_runtime_directories(account)


def _next_account_number(session: Session) -> int:
    max_id = session.scalar(select(func.max(Account.id))) or 0
    return int(max_id) + 1


def _next_sort_order(session: Session) -> int:
    max_sort = session.scalar(select(func.max(Account.sort_order))) or 0
    return int(max_sort) + 1


def _unique_slug(session: Session, base: str) -> str:
    candidate = base
    suffix = 2
    while session.scalar(select(Account.id).where(Account.slug == candidate)) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate
