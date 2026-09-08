from __future__ import annotations

import asyncio
from urllib.parse import parse_qs

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ai_quota_monitor.services.system import system_info_to_dict, update_result_to_dict

router = APIRouter(prefix="/api/system")


@router.get("/info")
async def system_info(request: Request) -> JSONResponse:
    info = await asyncio.to_thread(request.app.state.system_service.info)
    return JSONResponse(content=system_info_to_dict(info))


@router.post("/update")
async def system_update(request: Request) -> JSONResponse:
    payload = await _payload(request)
    dry_run = _bool(payload.get("dry_run"), default=True)
    restart = _bool(payload.get("restart"), default=False)

    info = await asyncio.to_thread(request.app.state.system_service.info)
    if (
        not dry_run
        and info.deployment_mode != "docker"
        and not request.app.state.settings.enable_web_updates
    ):
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": (
                    "Real web updates are disabled. Set "
                    "AI_QUOTA_MONITOR_ENABLE_WEB_UPDATES=true or use the CLI updater."
                )
            },
        )

    result = await asyncio.to_thread(
        request.app.state.system_service.update,
        dry_run=dry_run,
        restart=restart,
    )
    http_status = status.HTTP_200_OK if result.supported else status.HTTP_409_CONFLICT
    if any(step.status == "failed" for step in result.steps):
        http_status = status.HTTP_409_CONFLICT
    return JSONResponse(status_code=http_status, content=update_result_to_dict(result))


async def _payload(request: Request) -> dict[str, str]:
    values: dict[str, str] = {key: value for key, value in request.query_params.items()}
    body = await request.body()
    if not body:
        return values

    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        parsed = await request.json()
        return {**values, **{str(key): str(value) for key, value in parsed.items()}}

    parsed_form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    values.update({key: item[0] for key, item in parsed_form.items()})
    return values


def _bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
