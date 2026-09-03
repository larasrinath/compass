from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from linkedin_dashboard import __version__
from linkedin_dashboard.api._filters import PrivacyFilterMiddleware
from linkedin_dashboard.api.audit import router as audit_router
from linkedin_dashboard.api.health import router as health_router
from linkedin_dashboard.correlation import CorrelationIdMiddleware
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.security import (
    ConfiguredHostMiddleware,
    OriginGuardMiddleware,
    RuntimeBoundaryMiddleware,
)
from linkedin_dashboard.settings import Settings


def create_app(app_settings: Settings) -> FastAPI:
    database = Database(app_settings.db_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        try:
            yield
        finally:
            database.dispose()

    app = FastAPI(
        title="LinkedIn Dashboard",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.settings = app_settings
    app.state.database = database

    app.add_middleware(
        RuntimeBoundaryMiddleware,
        host=app_settings.host,
        port=app_settings.port,
        database=database,
    )
    app.add_middleware(ConfiguredHostMiddleware, allowed_host=app_settings.host)
    app.add_middleware(
        OriginGuardMiddleware,
        allowed_origin=app_settings.frontend_origin,
    )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(PrivacyFilterMiddleware)
    app.include_router(health_router, prefix="/api")
    app.include_router(audit_router, prefix="/api")
    return app


def run() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=False,
        server_header=False,
    )
