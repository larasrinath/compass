from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.db.models import AuditLog
from linkedin_dashboard.db.session import Database


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def append_audit_event(
    database: Database,
    *,
    session_id: str,
    actor: str,
    action: str,
    subject_type: str,
    subject_id: str,
    detail: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    event = AuditLog(
        session_id=session_id,
        at=utc_now(),
        actor=actor,
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        detail=detail or {},
        correlation_id=correlation_id or current_correlation_id(),
    )
    with database.sessions.begin() as db_session:
        db_session.add(event)
    return event
