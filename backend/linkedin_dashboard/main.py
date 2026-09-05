from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
from linkedin_dashboard.services.downloads import SearchDownloadService
from linkedin_dashboard.services.enrichment import (
    CompositeResultProcessor,
    EnrichmentResultProcessor,
    EnrichmentService,
)
from linkedin_dashboard.services.scoring_service import ScoringService
from linkedin_dashboard.services.search import DiscoveryResultProcessor, SearchService
from linkedin_dashboard.settings import Settings

_MAX_REQUEST_BODY_BYTES = 256 * 1024


class RequestBodyLimitMiddleware:
    """Reject oversized API bodies before validation or domain writes."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in {
            "POST",
            "PUT",
            "PATCH",
        }:
            await self.app(scope, receive, send)
            return
        for name, raw_value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(raw_value) > self.max_bytes:
                        await self._reject(send)
                        return
                except ValueError:
                    await self._reject(send)
                    return
        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] == "http.disconnect":
                return
            if message["type"] == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    await self._reject(send)
                    return
                if not message.get("more_body", False):
                    break
        index = 0

        async def replay() -> Message:
            nonlocal index
            if index < len(messages):
                message = messages[index]
                index += 1
                return message
            return {"type": "http.disconnect"}

        await self.app(scope, replay, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = b'{"detail":"Request body is too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def _safe_validation_errors(error: RequestValidationError) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in item.items() if key not in {"input", "ctx"}}
        for item in error.errors()
    ]


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

    download_service = SearchDownloadService(database, enrichment_service)
    job_queue.before_claim = download_service.dispatch_pending
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
    app.state.download_service = download_service
    app.state.scoring_service = scoring_service
    app.state.llm_provider = llm_provider

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422, content={"detail": _safe_validation_errors(error)}
        )

    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=_MAX_REQUEST_BODY_BYTES)
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
        timeout_graceful_shutdown=5,
    )
