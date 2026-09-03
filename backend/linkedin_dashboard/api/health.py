from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from linkedin_dashboard.db.session import Database
from linkedin_dashboard.settings import Settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    database: str
    send_enabled: bool
    llm_provider: str


def get_database(request: Request) -> Database:
    return request.app.state.database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


@router.get("/health", response_model=HealthResponse)
def health(
    database: Annotated[Database, Depends(get_database)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> HealthResponse:
    writable = database.writable()
    return HealthResponse(
        status="ok" if writable else "degraded",
        database="ok" if writable else "unavailable",
        send_enabled=settings.send_enabled,
        llm_provider=settings.llm_provider,
    )
