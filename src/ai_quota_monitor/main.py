from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, sessionmaker

from ai_quota_monitor import __version__
from ai_quota_monitor.config import Settings, get_settings
from ai_quota_monitor.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from ai_quota_monitor.logging_config import configure_logging
from ai_quota_monitor.migrations import run_migrations
from ai_quota_monitor.routes import health, system
from ai_quota_monitor.routes.dashboard import register_routes
from ai_quota_monitor.services.accounts import ensure_runtime_directories, seed_defaults
from ai_quota_monitor.services.anchors import (
    AnchorService,
    CodexAnchorBackend,
    CodexSdkAnchorBackend,
)
from ai_quota_monitor.services.codex_auth import (
    CodexAuthBackend,
    CodexAuthManager,
    OpenAiCodexAuthBackend,
)
from ai_quota_monitor.services.live_updates import RateLimitUpdateListener
from ai_quota_monitor.services.scheduler import SchedulerService, QuotaScheduler
from ai_quota_monitor.services.smart_anchors import SmartAnchorService
from ai_quota_monitor.services.system import SystemService
from ai_quota_monitor.services.telemetry import (
    CodexAppServerTelemetryBackend,
    TelemetryBackend,
    TelemetryService,
)

PACKAGE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))


def create_app(
    settings: Settings | None = None,
    auth_backend_factory: Callable[[], CodexAuthBackend] = OpenAiCodexAuthBackend,
    anchor_backend_factory: Callable[[], CodexAnchorBackend] = CodexSdkAnchorBackend,
    telemetry_backend_factory: Callable[
        [],
        TelemetryBackend,
    ] = CodexAppServerTelemetryBackend,
    scheduler_factory: Callable[
        [sessionmaker[Session], SmartAnchorService, Settings, TelemetryService],
        SchedulerService,
    ] = QuotaScheduler,
    system_service_factory: Callable[[Settings], SystemService] = SystemService,
) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        run_migrations(app_settings)
        engine = create_database_engine(app_settings)
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            seed_defaults(session, app_settings)
            ensure_runtime_directories(app_settings, session)
        app.state.settings = app_settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.auth_manager = CodexAuthManager(
            session_factory,
            backend_factory=auth_backend_factory,
        )
        app.state.anchor_service = AnchorService(
            session_factory,
            app_settings,
            backend_factory=anchor_backend_factory,
        )
        app.state.telemetry_service = TelemetryService(
            session_factory,
            backend_factory=telemetry_backend_factory,
        )
        app.state.smart_anchor_service = SmartAnchorService(
            session_factory,
            app_settings,
            app.state.anchor_service,
            app.state.telemetry_service,
        )
        app.state.system_service = system_service_factory(app_settings)
        app.state.rate_limit_listener = RateLimitUpdateListener(
            session_factory,
            app.state.telemetry_service,
            app_settings,
        )
        app.state.auth_manager.set_status_change_callback(
            app.state.rate_limit_listener.reload
        )
        app.state.quota_scheduler = scheduler_factory(
            session_factory,
            app.state.smart_anchor_service,
            app_settings,
            app.state.telemetry_service,
        )
        if app_settings.enable_scheduler:
            app.state.quota_scheduler.start()
        if app_settings.enable_app_server_notifications:
            app.state.rate_limit_listener.start()
        try:
            yield
        finally:
            app.state.rate_limit_listener.shutdown()
            app.state.quota_scheduler.shutdown()
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
    app.include_router(system.router)

    return app
