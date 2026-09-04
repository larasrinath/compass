from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from linkedin_dashboard import __version__
from linkedin_dashboard.api._filters import PrivacyFilterMiddleware
from linkedin_dashboard.api.audit import router as audit_router
from linkedin_dashboard.api.discovery import router as discovery_router
from linkedin_dashboard.api.gates import router as gates_router
from linkedin_dashboard.api.health import router as health_router
from linkedin_dashboard.api.jobs import router as jobs_router
from linkedin_dashboard.api.scoring import router as scoring_router
from linkedin_dashboard.correlation import CorrelationIdMiddleware
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.llm import NullProvider
from linkedin_dashboard.mcp.client import MCPClient
from linkedin_dashboard.queue.worker import (
    DurableJobQueue,
    JobExecutor,
    MCPReadExecutor,
)
from linkedin_dashboard.security import (
    ConfiguredHostMiddleware,
    OriginGuardMiddleware,
    RuntimeBoundaryMiddleware,
)
from linkedin_dashboard.services.brief import BriefService
from linkedin_dashboard.services.enrichment import (
    CompositeResultProcessor,
    EnrichmentResultProcessor,
    EnrichmentService,
)
from linkedin_dashboard.services.scoring_service import ScoringService
from linkedin_dashboard.services.search import DiscoveryResultProcessor, SearchService
from linkedin_dashboard.settings import Settings


def create_app(
    app_settings: Settings, *, queue_executor: JobExecutor | None = None
) -> FastAPI:
    database = Database(app_settings.db_path)
    executor = queue_executor or MCPReadExecutor(MCPClient(app_settings.mcp_url))
    scoring_service = ScoringService(database)
    discovery_processor = DiscoveryResultProcessor(database, scoring_service)
    enrichment_processor = EnrichmentResultProcessor(database, scoring_service)
    result_processor = CompositeResultProcessor(
        discovery_processor, enrichment_processor
    )
    job_queue = DurableJobQueue(
        database,
        executor,
        inter_call_delay_seconds=app_settings.inter_call_delay_seconds,
        result_processor=result_processor,
    )
    brief_service = BriefService(database, scoring_service)
    search_service = SearchService(database, job_queue, discovery_processor)
    enrichment_service = EnrichmentService(database, job_queue)
    llm_provider = NullProvider()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        del application
        try:
            yield
        finally:
            await job_queue.stop()
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
    app.state.job_queue = job_queue
    app.state.brief_service = brief_service
    app.state.search_service = search_service
    app.state.enrichment_service = enrichment_service
    app.state.scoring_service = scoring_service
    app.state.llm_provider = llm_provider

    app.add_middleware(
        RuntimeBoundaryMiddleware,
        host=app_settings.host,
        port=app_settings.port,
        database=database,
        on_ready=job_queue.start,
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
    app.include_router(jobs_router, prefix="/api")
    app.include_router(discovery_router, prefix="/api")
    app.include_router(scoring_router, prefix="/api")
    app.include_router(gates_router, prefix="/api")
    from linkedin_dashboard.api.enrichment import router as enrichment_router

    app.include_router(enrichment_router, prefix="/api")
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
