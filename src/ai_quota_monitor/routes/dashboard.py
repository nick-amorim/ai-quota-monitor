from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ai_quota_monitor import __version__
from ai_quota_monitor.services.accounts import (
    WEEKDAYS,
    get_account,
    list_accounts,
    update_account_schedule,
)
from ai_quota_monitor.services.anchors import get_app_setting, update_app_setting
from ai_quota_monitor.services.events import EventFilters, list_events, recent_events
from ai_quota_monitor.services.monitor import (
    MonitorView,
    build_monitor_view,
    format_local_datetime,
    format_local_time,
)

logger = logging.getLogger(__name__)

DASHBOARD_REFRESH_MIN_SECONDS = 5
DASHBOARD_REFRESH_MAX_SECONDS = 300
USAGE_POLL_MIN_MINUTES = 1
USAGE_POLL_MAX_MINUTES = 240


def register_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            accounts = list_accounts(session)
            app_settings = _runtime_app_settings(request, session)
        usage_by_account = request.app.state.telemetry_service.latest_snapshots_by_account()
        telemetry_errors_by_account = (
            request.app.state.telemetry_service.latest_refresh_errors_by_account()
        )
        scheduled_jobs = request.app.state.quota_scheduler.next_runs()
        monitor_view = _monitor_view(
            request,
            accounts,
            usage_by_account,
            scheduled_jobs,
            telemetry_errors_by_account=telemetry_errors_by_account,
        )
        system_info = request.app.state.system_service.info()
        anchor_runs = request.app.state.anchor_service.recent_runs(limit=8)

        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "app_name": request.app.state.settings.app_name,
                "version": __version__,
                "timezone": request.app.state.settings.timezone,
                "accounts": accounts,
                "weekdays": WEEKDAYS,
                "login_attempts": request.app.state.auth_manager.login_snapshots(),
                "anchor_prompt": app_settings["anchor_prompt"],
                "app_settings": app_settings,
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
                "anchor_runs": anchor_runs,
                "latest_anchor_by_account": _latest_anchor_by_account(anchor_runs),
                "usage_by_account": usage_by_account,
                "monitor_view": monitor_view,
                "account_views": monitor_view.account_map,
                "timeline": monitor_view.timeline,
                "scheduler_running": request.app.state.quota_scheduler.running,
                "scheduled_jobs": scheduled_jobs,
                "system_info": system_info,
                "events": recent_events(session_factory, limit=8),
                "account_labels_by_id": _account_labels_by_id(accounts),
                **_template_helpers(request),
            },
        )

    @router.get("/partials/accounts", response_class=HTMLResponse)
    async def accounts_partial(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            accounts = list_accounts(session)
            app_settings = _runtime_app_settings(request, session)
        usage_by_account = request.app.state.telemetry_service.latest_snapshots_by_account()
        telemetry_errors_by_account = (
            request.app.state.telemetry_service.latest_refresh_errors_by_account()
        )
        scheduled_jobs = request.app.state.quota_scheduler.next_runs()
        monitor_view = _monitor_view(
            request,
            accounts,
            usage_by_account,
            scheduled_jobs,
            telemetry_errors_by_account=telemetry_errors_by_account,
        )
        anchor_runs = request.app.state.anchor_service.recent_runs(limit=8)

        return templates.TemplateResponse(
            request,
            "_account_grid.html",
            {
                "accounts": accounts,
                "login_attempts": request.app.state.auth_manager.login_snapshots(),
                "usage_by_account": usage_by_account,
                "account_views": monitor_view.account_map,
                "latest_anchor_by_account": _latest_anchor_by_account(anchor_runs),
                "account_labels_by_id": _account_labels_by_id(accounts),
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
                **_template_helpers(request),
            },
        )

    @router.get("/history", response_class=HTMLResponse)
    async def history(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        query = request.query_params
        selected_account_id = _optional_int(query.get("account_id"))
        selected_level = _optional_choice(
            query.get("level"),
            {"info", "warning", "error"},
        )
        selected_category = (query.get("category") or "").strip() or None

        with session_factory() as session:
            accounts = list_accounts(session)

        events = list_events(
            session_factory,
            filters=EventFilters(
                account_id=selected_account_id,
                level=selected_level,
                category=selected_category,
            ),
            limit=100,
        )
        return templates.TemplateResponse(
            request,
            "history.html",
            {
                "app_name": request.app.state.settings.app_name,
                "version": __version__,
                "accounts": accounts,
                "events": events,
                "levels": ["info", "warning", "error"],
                "selected_account_id": selected_account_id,
                "selected_level": selected_level,
                "selected_category": selected_category,
                **_template_helpers(request),
            },
        )

    @router.get("/monitor", response_class=HTMLResponse)
    async def monitor(request: Request) -> HTMLResponse:
        monitor_view = _load_monitor_view(request)
        app_settings = _load_runtime_app_settings(request)
        return templates.TemplateResponse(
            request,
            "monitor.html",
            {
                "app_name": request.app.state.settings.app_name,
                "version": __version__,
                "body_class": "monitor-body",
                "monitor_view": monitor_view,
                "refresh_path": "/partials/monitor",
                "account_slug": None,
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
            },
        )

    @router.get("/monitor/{account_slug}", response_class=HTMLResponse)
    async def account_monitor(account_slug: str, request: Request) -> HTMLResponse:
        monitor_view = _load_monitor_view(request, account_slug=account_slug)
        app_settings = _load_runtime_app_settings(request)
        return templates.TemplateResponse(
            request,
            "monitor.html",
            {
                "app_name": request.app.state.settings.app_name,
                "version": __version__,
                "body_class": "monitor-body",
                "monitor_view": monitor_view,
                "refresh_path": f"/partials/monitor/{account_slug}",
                "account_slug": account_slug,
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
            },
        )

    @router.get("/partials/monitor", response_class=HTMLResponse)
    async def monitor_partial(request: Request) -> HTMLResponse:
        monitor_view = _load_monitor_view(request)
        app_settings = _load_runtime_app_settings(request)
        return templates.TemplateResponse(
            request,
            "_monitor_content.html",
            {
                "monitor_view": monitor_view,
                "refresh_path": "/partials/monitor",
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
            },
        )

    @router.get("/partials/monitor/{account_slug}", response_class=HTMLResponse)
    async def account_monitor_partial(account_slug: str, request: Request) -> HTMLResponse:
        monitor_view = _load_monitor_view(request, account_slug=account_slug)
        app_settings = _load_runtime_app_settings(request)
        return templates.TemplateResponse(
            request,
            "_monitor_content.html",
            {
                "monitor_view": monitor_view,
                "refresh_path": f"/partials/monitor/{account_slug}",
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
            },
        )

    @router.get("/partials/accounts/{account_id}/usage", response_class=HTMLResponse)
    async def account_usage_partial(account_id: int, request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            account = get_account(session, account_id)
            if account is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
            app_settings = _runtime_app_settings(request, session)
        usage_by_account = request.app.state.telemetry_service.latest_snapshots_by_account()
        telemetry_errors_by_account = (
            request.app.state.telemetry_service.latest_refresh_errors_by_account()
        )
        monitor_view = _monitor_view(
            request,
            [account],
            usage_by_account,
            request.app.state.quota_scheduler.next_runs(),
            telemetry_errors_by_account=telemetry_errors_by_account,
        )

        return templates.TemplateResponse(
            request,
            "_account_usage.html",
            {
                "account": account,
                "usage_by_account": usage_by_account,
                "account_views": monitor_view.account_map,
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
                **_template_helpers(request),
            },
        )

    @router.get("/partials/scheduler", response_class=HTMLResponse)
    async def scheduler_partial(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            accounts = list_accounts(session)
            app_settings = _runtime_app_settings(request, session)
        return templates.TemplateResponse(
            request,
            "_scheduled_jobs.html",
            {
                "scheduled_jobs": request.app.state.quota_scheduler.next_runs(),
                "account_labels_by_id": _account_labels_by_id(accounts),
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
                **_template_helpers(request),
            },
        )

    @router.get("/partials/events/recent", response_class=HTMLResponse)
    async def recent_events_partial(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            app_settings = _runtime_app_settings(request, session)
        return templates.TemplateResponse(
            request,
            "_recent_events.html",
            {
                "events": recent_events(session_factory, limit=8),
                "dashboard_refresh_interval_seconds": app_settings[
                    "dashboard_refresh_interval_seconds"
                ],
                **_template_helpers(request),
            },
        )

    @router.post("/settings/anchor")
    async def save_anchor_settings(request: Request) -> RedirectResponse:
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        prompt = _required(form, "anchor_prompt")
        session_factory = request.app.state.session_factory

        with session_factory() as session:
            update_app_setting(session, "anchor_prompt", prompt)

        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/settings/global")
    async def save_global_settings(request: Request) -> RedirectResponse:
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        prompt = _required(form, "anchor_prompt")
        dashboard_refresh_interval_seconds = _parse_int_range(
            _required(form, "dashboard_refresh_interval_seconds"),
            "dashboard_refresh_interval_seconds",
            minimum=DASHBOARD_REFRESH_MIN_SECONDS,
            maximum=DASHBOARD_REFRESH_MAX_SECONDS,
        )
        usage_poll_interval_minutes = _parse_int_range(
            _required(form, "usage_poll_interval_minutes"),
            "usage_poll_interval_minutes",
            minimum=USAGE_POLL_MIN_MINUTES,
            maximum=USAGE_POLL_MAX_MINUTES,
        )
        session_factory = request.app.state.session_factory

        with session_factory() as session:
            update_app_setting(session, "anchor_prompt", prompt)
            update_app_setting(
                session,
                "dashboard_refresh_interval_seconds",
                str(dashboard_refresh_interval_seconds),
            )
            update_app_setting(
                session,
                "usage_poll_interval_minutes",
                str(usage_poll_interval_minutes),
            )

        request.app.state.settings.anchor_prompt = prompt
        request.app.state.settings.dashboard_refresh_interval_seconds = (
            dashboard_refresh_interval_seconds
        )
        request.app.state.settings.usage_poll_interval_minutes = usage_poll_interval_minutes
        request.app.state.quota_scheduler.reload()
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/schedule")
    async def save_schedule(account_id: int, request: Request) -> RedirectResponse:
        form = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        session_factory = request.app.state.session_factory

        with session_factory() as session:
            account = get_account(session, account_id)
            if account is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

            update_account_schedule(
                session,
                account,
                enabled=_checkbox(form, "enabled"),
                daily_anchor_enabled=_checkbox(form, "daily_anchor_enabled"),
                daily_anchor_time=_parse_time(_required(form, "daily_anchor_time")),
                weekly_target_day=_required(form, "weekly_target_day"),
                weekly_target_time=_parse_time(_required(form, "weekly_target_time")),
                timezone=_required(form, "timezone"),
                active_weekdays={
                    weekday for weekday in WEEKDAYS if _checkbox(form, f"{weekday}_enabled")
                },
                skip_if_window_active=_checkbox(form, "skip_if_window_active"),
            )

        request.app.state.quota_scheduler.reload()
        request.app.state.rate_limit_listener.reload()
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/auth/device-login")
    async def start_device_login(account_id: int, request: Request) -> RedirectResponse:
        try:
            request.app.state.auth_manager.start_device_login(account_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        except Exception:
            logger.exception("Failed to start device login for account %s", account_id)
            pass

        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/auth/cancel")
    async def cancel_device_login(account_id: int, request: Request) -> RedirectResponse:
        request.app.state.auth_manager.cancel_device_login(account_id)
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/auth/status")
    async def refresh_auth_status(account_id: int, request: Request) -> RedirectResponse:
        try:
            request.app.state.auth_manager.refresh_status(account_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        except Exception:
            logger.exception("Failed to refresh auth status for account %s", account_id)
            pass

        request.app.state.rate_limit_listener.reload()
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/auth/logout")
    async def logout(account_id: int, request: Request) -> RedirectResponse:
        try:
            request.app.state.auth_manager.logout(account_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        except Exception:
            logger.exception("Failed to log out account %s", account_id)
            pass

        request.app.state.rate_limit_listener.reload()
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/anchors/run")
    async def run_anchor(account_id: int, request: Request) -> RedirectResponse:
        try:
            await asyncio.to_thread(
                request.app.state.anchor_service.run_manual_anchor,
                account_id,
            )
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        except Exception:
            logger.exception("Manual anchor failed for account %s", account_id)
            pass
        else:
            try:
                await asyncio.to_thread(
                    request.app.state.telemetry_service.refresh_account_usage,
                    account_id,
                )
            except Exception:
                logger.exception("Post-anchor usage refresh failed for account %s", account_id)

        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/usage/refresh")
    async def refresh_account_usage(account_id: int, request: Request) -> RedirectResponse:
        try:
            await asyncio.to_thread(
                request.app.state.telemetry_service.refresh_account_usage,
                account_id,
            )
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None

        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/usage/refresh-all")
    async def refresh_all_usage(request: Request) -> RedirectResponse:
        await asyncio.to_thread(request.app.state.telemetry_service.refresh_all_accounts)
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    return router


def _required(form: dict[str, list[str]], key: str) -> str:
    value = form.get(key, [""])[0].strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{key} is required",
        )
    return value


def _checkbox(form: dict[str, list[str]], key: str) -> bool:
    return form.get(key, [""])[0] == "on"


def _parse_time(value: str) -> time:
    try:
        hour, minute = value.split(":", maxsplit=1)
        return time(hour=int(hour), minute=int(minute))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid time: {value}",
        ) from exc


def _parse_int_range(
    value: str,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} must be a number",
        ) from exc

    if parsed < minimum or parsed > maximum:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{label} must be between {minimum} and {maximum}",
        )
    return parsed


def _optional_int(value: str | None) -> int | None:
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid integer: {value}",
        ) from exc


def _optional_choice(value: str | None, allowed: set[str]) -> str | None:
    if value is None or value.strip() == "":
        return None
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid choice: {value}",
        )
    return normalized


def _load_monitor_view(
    request: Request,
    *,
    account_slug: str | None = None,
) -> MonitorView:
    session_factory = request.app.state.session_factory
    with session_factory() as session:
        accounts = list_accounts(session)

    if account_slug is not None:
        accounts = [account for account in accounts if account.slug == account_slug]
        if not accounts:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    usage_by_account = request.app.state.telemetry_service.latest_snapshots_by_account()
    telemetry_errors_by_account = (
        request.app.state.telemetry_service.latest_refresh_errors_by_account()
    )
    scheduled_jobs = [
        job
        for job in request.app.state.quota_scheduler.next_runs()
        if account_slug is None or any(account.id == job.account_id for account in accounts)
    ]
    return _monitor_view(
        request,
        accounts,
        usage_by_account,
        scheduled_jobs,
        telemetry_errors_by_account=telemetry_errors_by_account,
    )


def _monitor_view(
    request: Request,
    accounts,
    usage_by_account,
    scheduled_jobs,
    *,
    telemetry_errors_by_account=None,
) -> MonitorView:
    return build_monitor_view(
        accounts=accounts,
        usage_by_account=usage_by_account,
        telemetry_errors_by_account=telemetry_errors_by_account,
        scheduled_jobs=scheduled_jobs,
        settings=request.app.state.settings,
        now=datetime.now(UTC),
    )


def _load_runtime_app_settings(request: Request) -> dict[str, str | int]:
    with request.app.state.session_factory() as session:
        return _runtime_app_settings(request, session)


def _runtime_app_settings(request: Request, session) -> dict[str, str | int]:
    settings = request.app.state.settings
    return {
        "anchor_prompt": get_app_setting(
            session,
            "anchor_prompt",
            settings.anchor_prompt,
        )
        or settings.anchor_prompt,
        "dashboard_refresh_interval_seconds": _int_app_setting(
            session,
            "dashboard_refresh_interval_seconds",
            settings.dashboard_refresh_interval_seconds,
            minimum=DASHBOARD_REFRESH_MIN_SECONDS,
            maximum=DASHBOARD_REFRESH_MAX_SECONDS,
        ),
        "usage_poll_interval_minutes": _int_app_setting(
            session,
            "usage_poll_interval_minutes",
            settings.usage_poll_interval_minutes,
            minimum=USAGE_POLL_MIN_MINUTES,
            maximum=USAGE_POLL_MAX_MINUTES,
        ),
    }


def _int_app_setting(
    session,
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = get_app_setting(session, key, str(default)) or str(default)
    try:
        parsed = int(value)
    except ValueError:
        return default
    return min(max(parsed, minimum), maximum)


def _latest_anchor_by_account(anchor_runs) -> dict[int, object]:
    latest: dict[int, object] = {}
    for run in anchor_runs:
        if run.account_id not in latest:
            latest[run.account_id] = run
    return latest


def _template_helpers(request: Request) -> dict[str, object]:
    timezone = request.app.state.settings.timezone
    return {
        "format_datetime": lambda value: format_local_datetime(value, timezone),
        "format_time": lambda value: format_local_time(value, timezone),
        "account_label": _account_label,
    }


def _account_label(account) -> str:
    if account is None:
        return "-"
    return account.account_display or "Account not logged in"


def _account_labels_by_id(accounts) -> dict[int, str]:
    return {account.id: _account_label(account) for account in accounts}
