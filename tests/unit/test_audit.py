from __future__ import annotations

import pytest
from linkedin_dashboard.audit import append_audit_event
from linkedin_dashboard.db.models import DashboardSession
from linkedin_dashboard.db.session import Database
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

NOW = "2026-09-02T12:00:00+00:00"


def test_audit_log_is_append_only(database: Database) -> None:
    with database.sessions.begin() as db_session:
        db_session.add(
            DashboardSession(
                id="session-audit",
                created_at=NOW,
                label="Audit test",
                purge_after=NOW,
                nav_budget=120,
                nav_used=0,
                send_enabled=False,
            )
        )

    event = append_audit_event(
        database,
        session_id="session-audit",
        actor="operator",
        action="session.created",
        subject_type="session",
        subject_id="session-audit",
        detail={"source": "test"},
        correlation_id="correlation-test",
    )

    with pytest.raises(DBAPIError, match="append-only"):
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE audit_log SET action='changed' WHERE id=:id"),
                {"id": event.id},
            )

    with pytest.raises(DBAPIError, match="append-only"):
        with database.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM audit_log WHERE id=:id"), {"id": event.id}
            )
