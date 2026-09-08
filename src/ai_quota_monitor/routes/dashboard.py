from __future__ import annotations

import asyncio
from datetime import time
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


def register_routes(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            accounts = list_accounts(session)
            anchor_prompt = get_app_setting(
                session,
                "anchor_prompt",
                request.app.state.settings.anchor_prompt,
            )

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
                "anchor_prompt": anchor_prompt,
                "anchor_runs": request.app.state.anchor_service.recent_runs(limit=8),
                "usage_by_account": (
                    request.app.state.telemetry_service.latest_snapshots_by_account()
                ),
                "scheduler_running": request.app.state.quota_scheduler.running,
                "scheduled_jobs": request.app.state.quota_scheduler.next_runs(),
                "events": recent_events(session_factory, limit=8),
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
            },
        )

    @router.get("/partials/accounts/{account_id}/usage", response_class=HTMLResponse)
    async def account_usage_partial(account_id: int, request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        with session_factory() as session:
            account = get_account(session, account_id)
            if account is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

        return templates.TemplateResponse(
            request,
            "_account_usage.html",
            {
                "account": account,
                "usage_by_account": (
                    request.app.state.telemetry_service.latest_snapshots_by_account()
                ),
            },
        )

    @router.get("/partials/scheduler", response_class=HTMLResponse)
    async def scheduler_partial(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "_scheduled_jobs.html",
            {
                "scheduled_jobs": request.app.state.quota_scheduler.next_runs(),
            },
        )

    @router.get("/partials/events/recent", response_class=HTMLResponse)
    async def recent_events_partial(request: Request) -> HTMLResponse:
        session_factory = request.app.state.session_factory
        return templates.TemplateResponse(
            request,
            "_recent_events.html",
            {
                "events": recent_events(session_factory, limit=8),
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
            pass

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
