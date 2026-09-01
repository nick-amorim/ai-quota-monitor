from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from pathlib import Path

from sqlalchemy import select
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
        "missed_anchor_grace_minutes": str(settings.missed_anchor_grace_minutes),
        "history_retention_days": str(settings.history_retention_days),
    }


def seed_defaults(session: Session, settings: Settings) -> None:
    for key, value in default_app_settings(settings).items():
        setting = session.get(AppSetting, key)
        if setting is None:
            session.add(AppSetting(key=key, value=value))

    data_root = settings.data_dir.expanduser()
    for seed in default_account_seeds(settings):
        account = session.scalar(select(Account).where(Account.slug == seed.slug))
        if account is None:
            account_root = data_root / seed.slug
            account = Account(
                name=seed.name,
                slug=seed.slug,
                enabled=True,
                codex_home=str(account_root / "codex-home"),
                workspace_path=str(account_root / "workspace"),
            )
            session.add(account)
            session.flush()

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


def list_accounts(session: Session) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .options(selectinload(Account.schedule))
            .order_by(Account.slug)
        )
    )


def get_account(session: Session, account_id: int) -> Account | None:
    return session.scalar(
        select(Account)
        .options(selectinload(Account.schedule))
        .where(Account.id == account_id)
    )


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


def ensure_runtime_directories(settings: Settings) -> None:
    data_root = settings.data_dir.expanduser()
    for seed in default_account_seeds(settings):
        account_root = data_root / seed.slug
        for path in (account_root / "codex-home", account_root / "workspace"):
            Path(path).mkdir(parents=True, exist_ok=True)
