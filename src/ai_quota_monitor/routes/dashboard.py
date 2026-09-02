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

        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    @router.post("/accounts/{account_id}/auth/logout")
    async def logout(account_id: int, request: Request) -> RedirectResponse:
        try:
            request.app.state.auth_manager.logout(account_id)
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND) from None
        except Exception:
            pass

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
