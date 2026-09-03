from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from linkedin_dashboard.api.health import get_database
from linkedin_dashboard.db.models import AuditLog
from linkedin_dashboard.db.session import Database

router = APIRouter(tags=["audit"])


class AuditRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    at: str
    actor: str
    action: str
    subject_type: str
    subject_id: str
    detail: dict[str, Any]
    correlation_id: str


@router.get("/audit", response_model=list[AuditRecord])
def list_audit_events(
    database: Annotated[Database, Depends(get_database)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AuditRecord]:
    with database.sessions() as db_session:
        events = db_session.scalars(
            select(AuditLog).order_by(AuditLog.at.desc()).limit(limit)
        ).all()
        return [AuditRecord.model_validate(event) for event in events]
