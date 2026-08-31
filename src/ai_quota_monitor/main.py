from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_quota_monitor import __version__
from ai_quota_monitor.config import Settings, get_settings
from ai_quota_monitor.database import (
    create_database_engine,
    database_is_healthy,
    initialize_database,
)

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database_engine(app_settings)
        initialize_database(engine)
        app.state.settings = app_settings
        app.state.engine = engine
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(PACKAGE_DIR / "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "app_name": app_settings.app_name,
                "version": __version__,
                "timezone": app_settings.timezone,
            },
        )

    @app.get("/health")
    async def health(request: Request) -> JSONResponse:
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
                "app": app_settings.app_name,
                "version": __version__,
                "database": database_status,
            },
        )

    return app
