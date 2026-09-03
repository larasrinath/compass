from __future__ import annotations

from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import JobAttempt, QueueControl

VERSION = "0016_durable_queue"

TRIGGER_NAMES = (
    "job_kind_is_allowlisted_insert",
    "job_kind_is_allowlisted_update",
    "job_attempt_raw_response_is_immutable",
    "job_attempt_finished_is_immutable",
    "job_lifecycle_is_valid_insert",
    "job_lifecycle_is_valid_update",
)

INDEX_NAMES = ("one_running_job", "job_dequeue_order", "job_session_state")

_ALLOWED_JOB = """
NEW.kind IN ('list_tools','search_people','get_person_profile','get_company_profile')
AND NEW.max_attempts = CASE WHEN NEW.kind = 'list_tools' THEN 1 ELSE 2 END
AND NEW.attempts >= 0
AND NEW.attempts <= NEW.max_attempts
"""

STATEMENTS = (
    f"""
    CREATE TRIGGER job_kind_is_allowlisted_insert
    BEFORE INSERT ON job
    FOR EACH ROW
    WHEN NOT ({_ALLOWED_JOB})
    BEGIN
      SELECT RAISE(ABORT, 'job kind or attempt policy is not allowed');
    END
    """,
    f"""
    CREATE TRIGGER job_kind_is_allowlisted_update
    BEFORE UPDATE OF kind, attempts, max_attempts ON job
    FOR EACH ROW
    WHEN NOT ({_ALLOWED_JOB})
    BEGIN
      SELECT RAISE(ABORT, 'job kind or attempt policy is not allowed');
    END
    """,
    """
    CREATE TRIGGER job_attempt_raw_response_is_immutable
    BEFORE UPDATE OF raw_response, raw_error ON job_attempt
    FOR EACH ROW
    WHEN (OLD.raw_response IS NOT NULL AND OLD.raw_response <> 'null'
          AND NEW.raw_response <> OLD.raw_response)
      OR (OLD.raw_error IS NOT NULL AND OLD.raw_error <> 'null'
          AND NEW.raw_error <> OLD.raw_error)
    BEGIN
      SELECT RAISE(ABORT, 'captured job attempt data is immutable');
    END
    """,
    """
    CREATE TRIGGER job_attempt_finished_is_immutable
    BEFORE UPDATE ON job_attempt
    FOR EACH ROW
    WHEN OLD.finished_at IS NOT NULL
     AND (NEW.id IS NOT OLD.id
       OR NEW.job_id IS NOT OLD.job_id
       OR NEW.attempt_number IS NOT OLD.attempt_number
       OR NEW.started_at IS NOT OLD.started_at
       OR NEW.response_received_at IS NOT OLD.response_received_at
       OR NEW.finished_at IS NOT OLD.finished_at
       OR NEW.outcome IS NOT OLD.outcome
       OR NEW.raw_response IS NOT OLD.raw_response
       OR NEW.raw_error IS NOT OLD.raw_error
       OR NEW.error_class IS NOT OLD.error_class
       OR NEW.safe_error_message IS NOT OLD.safe_error_message
       OR NEW.retry_at IS NOT OLD.retry_at)
    BEGIN
      SELECT RAISE(ABORT, 'finished job attempt is immutable');
    END
    """,
    """
    CREATE TRIGGER job_lifecycle_is_valid_insert
    BEFORE INSERT ON job
    FOR EACH ROW
    WHEN NEW.attempts < 0 OR NEW.max_attempts < 1
      OR NEW.attempts > NEW.max_attempts
      OR (NEW.state = 'running'
          AND (NEW.started_at IS NULL OR NEW.finished_at IS NOT NULL))
      OR (NEW.state IN ('done','failed','interrupted','cancelled')
          AND NEW.finished_at IS NULL)
      OR (NEW.state = 'queued' AND NEW.finished_at IS NOT NULL)
    BEGIN
      SELECT RAISE(ABORT, 'invalid job lifecycle');
    END
    """,
    """
    CREATE TRIGGER job_lifecycle_is_valid_update
    BEFORE UPDATE ON job
    FOR EACH ROW
    WHEN NEW.attempts < 0 OR NEW.max_attempts < 1
      OR NEW.attempts > NEW.max_attempts
      OR (NEW.state = 'running'
          AND (NEW.started_at IS NULL OR NEW.finished_at IS NOT NULL))
      OR (NEW.state IN ('done','failed','interrupted','cancelled')
          AND NEW.finished_at IS NULL)
      OR (NEW.state = 'queued' AND NEW.finished_at IS NOT NULL)
    BEGIN
      SELECT RAISE(ABORT, 'invalid job lifecycle');
    END
    """,
)


def apply(connection: Connection) -> None:
    cast(Table, JobAttempt.__table__).create(connection, checkfirst=True)
    cast(Table, QueueControl.__table__).create(connection, checkfirst=True)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS one_running_job "
        "ON job ((1)) WHERE state = 'running'"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS job_dequeue_order ON job (state, queued_at, id)"
    )
    connection.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS job_session_state "
        "ON job (session_id, state, queued_at)"
    )
