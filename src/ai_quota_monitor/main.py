from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ai_quota_monitor import __version__
from ai_quota_monitor.config import Settings, get_settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.routes import health
from ai_quota_monitor.routes.dashboard import register_routes
from ai_quota_monitor.services.accounts import ensure_runtime_directories, seed_defaults

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(app_settings)
        engine = create_database_engine(app_settings)
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            seed_defaults(session, app_settings)
        ensure_runtime_directories(app_settings)
        app.state.settings = app_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
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
    app.include_router(register_routes(templates))
    app.include_router(health.router)

    return app
