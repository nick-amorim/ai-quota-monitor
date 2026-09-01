from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from ai_quota_monitor import __version__
from ai_quota_monitor.database import database_is_healthy

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    settings = request.app.state.settings
    database_status = "ok" if database_is_healthy(request.app.state.engine) else "error"
    http_status = (
        status.HTTP_200_OK
        if database_status == "ok"
        else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return JSONResponse(
        status_code=http_status,
        content={
            "status": "ok" if database_status == "ok" else "error",
            "app": settings.app_name,
            "version": __version__,
            "database": database_status,
        },
    )
