from __future__ import annotations

import os
import sqlite3
import stat
from typing import Any, cast

import pytest
from linkedin_dashboard.db.migrations import (
    v0001_constraints,
    v0002_integrity,
    v0003_send_invariants,
    v0004_audit_cascade,
    v0005_send_history,
    v0006_send_state_timing,
    v0007_send_provenance,
    v0008_history_hardening,
    v0009_integrity_completion,
    v0010_takeover_guards,
)
from linkedin_dashboard.db.models import (
    Candidate,
    CandidateScore,
    DashboardSession,
    DraftClaim,
    MessageDraft,
    RoleBrief,
    SendAttempt,
    SendConfirmation,
)
from linkedin_dashboard.db.session import Database, get_journal_mode
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError, IntegrityError

NOW = "2026-09-02T12:00:00+00:00"


def seed_candidate(database: Database, suffix: str) -> tuple[str, str]:
    session_id = f"session-{suffix}"
    candidate_id = f"candidate-{suffix}"
    draft_id = f"draft-{suffix}"
    with database.sessions.begin() as db_session:
        db_session.add(
            DashboardSession(
                id=session_id,
                created_at=NOW,
                label="Test session",
                purge_after=NOW,
                nav_budget=120,
                nav_used=0,
                send_enabled=False,
            )
        )
        db_session.flush()
        db_session.add(
            Candidate(
                id=candidate_id,
                session_id=session_id,
                username=f"person-{suffix}",
                profile_url=f"https://www.linkedin.com/in/person-{suffix}/",
                first_seen_at=NOW,
                stage="discovered",
                retrieval_status="pending",
            )
        )
        db_session.flush()
        db_session.add(
            MessageDraft(
                id=draft_id,
                candidate_id=candidate_id,
                version=1,
                body="Hello",
                body_sha256="a" * 64,
                char_count=5,
                generator="manual",
                grounding_status="pass",
                grounding_report={},
                created_at=NOW,
            )
        )
    return candidate_id, draft_id


def attempt(
    *,
    attempt_id: str,
    candidate_id: str,
    draft_id: str,
    state: str,
    confirm_send: bool,
    resolution: str = "unresolved",
    finished_at: str | None = None,
) -> SendAttempt:
    return SendAttempt(
        id=attempt_id,
        candidate_id=candidate_id,
        draft_id=draft_id,
        idempotency_key=(attempt_id + "0" * 64)[:64],
        body_sha256="a" * 64,
        confirm_send=confirm_send,
        state=state,
        started_at=NOW,
        finished_at=finished_at,
        resolution=resolution,
        resolved_at=NOW if resolution != "unresolved" else None,
        resolution_note="checked" if resolution != "unresolved" else None,
    )


def confirmation(
    *, token: str, candidate_id: str, draft_id: str, body_sha256: str = "a" * 64
) -> SendConfirmation:
    return SendConfirmation(
        token=token,
        candidate_id=candidate_id,
        draft_id=draft_id,
        body_sha256=body_sha256,
        created_at=NOW,
        expires_at=NOW,
    )


def prepare_v0001_database(database: Database) -> None:
    database.initialize()
    with database.engine.begin() as connection:
        trigger_names = list(
            connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='trigger'")
            ).scalars()
        )
        for trigger_name in trigger_names:
            connection.exec_driver_sql(f'DROP TRIGGER "{trigger_name}"')
        connection.execute(
            text("DELETE FROM schema_migration WHERE version <> :version"),
            {"version": v0001_constraints.VERSION},
        )
        v0001_constraints.apply(connection)


def restart_database(database: Database) -> Database:
    path = database.path
    database.dispose()
    return Database(path)


def migration_schema_objects(path) -> list[tuple[str, str, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE type IN ('index', 'trigger') AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()


def insert_attempt_sql(
    connection,
    *,
    attempt_id: str,
    candidate_id: str,
    draft_id: str,
    state: str,
    confirm_send: int = 1,
    finished_at: str | None = None,
    resolution: str = "unresolved",
    resolved_at: str | None = None,
    resolution_note: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO send_attempt "
            "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
            "confirm_send, state, started_at, finished_at, resolution, "
            "resolved_at, resolution_note) VALUES "
            "(:id, :candidate_id, :draft_id, :key, :hash, :confirm_send, "
            ":state, :started_at, :finished_at, :resolution, :resolved_at, :note)"
        ),
        {
            "id": attempt_id,
            "candidate_id": candidate_id,
            "draft_id": draft_id,
            "key": (attempt_id + "0" * 64)[:64],
            "hash": "a" * 64,
            "confirm_send": confirm_send,
            "state": state,
            "started_at": NOW,
            "finished_at": finished_at,
            "resolution": resolution,
            "resolved_at": resolved_at,
            "note": resolution_note,
        },
    )


def test_database_uses_wal_and_owner_only_permissions(database: Database) -> None:
    with database.engine.connect() as connection:
        assert get_journal_mode(connection).casefold() == "wal"
        assert connection.exec_driver_sql("PRAGMA recursive_triggers").scalar_one() == 1

    with database.engine.connect() as connection:
        with pytest.raises(DBAPIError, match="not authorized"):
            connection.exec_driver_sql("PRAGMA recursive_triggers=OFF")
        assert connection.exec_driver_sql("PRAGMA recursive_triggers").scalar_one() == 1

    assert stat.S_IMODE(os.stat(database.path).st_mode) == 0o600
    assert database.writable()


def test_database_mode_drift_is_repaired_on_checkout_and_connect(
    database: Database,
) -> None:
    os.chmod(database.path, 0o644)
    with database.engine.connect():
        assert stat.S_IMODE(database.path.stat().st_mode) == 0o600

    database.engine.dispose()
    os.chmod(database.path, 0o640)
    with database.engine.connect():
        assert stat.S_IMODE(database.path.stat().st_mode) == 0o600


@pytest.mark.parametrize("existing", [False, True])
def test_live_sqlite_files_are_owner_only_before_and_after_connect(
    tmp_path, existing: bool
) -> None:
    parent = tmp_path / "traversable"
    parent.mkdir(mode=0o700)
    os.chmod(parent, 0o700)
    path = parent / "private.db"
    if existing:
        path.touch(mode=0o644)
        os.chmod(path, 0o644)
    database = Database(path)
    first_connect_modes: list[int] = []

    def observe_first_connect(dbapi_connection, connection_record) -> None:
        del dbapi_connection, connection_record
        first_connect_modes.append(stat.S_IMODE(database.path.stat().st_mode))

    event.listen(database.engine, "connect", observe_first_connect)
    marker = "PRIVATE-WAL-MARKER-7f00c2"
    try:
        database.initialize()
        with database.engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO session "
                    "(id, created_at, label, purge_after, nav_budget, nav_used, "
                    "send_enabled) VALUES "
                    "('session-sidecar', :now, :marker, :now, 120, 0, 0)"
                ),
                {"now": NOW, "marker": marker},
            )
            transaction.commit()
            wal = database.path.with_name(f"{database.path.name}-wal")
            shm = database.path.with_name(f"{database.path.name}-shm")

            assert marker.encode() in wal.read_bytes()
            assert stat.S_IMODE(database.path.stat().st_mode) == 0o600
            assert stat.S_IMODE(wal.stat().st_mode) == 0o600
            assert stat.S_IMODE(shm.stat().st_mode) == 0o600
            assert stat.S_IMODE(parent.stat().st_mode) == 0o700
    finally:
        database.dispose()

    assert first_connect_modes == [0o600]


def test_existing_public_database_parent_is_rejected_without_chmod(tmp_path) -> None:
    parent = tmp_path / "shared-parent"
    parent.mkdir(mode=0o755)
    os.chmod(parent, 0o755)
    path = parent / "dashboard.db"
    database = Database(path)

    try:
        with pytest.raises(PermissionError, match="no group or world permissions"):
            database.initialize()
    finally:
        database.dispose()

    assert stat.S_IMODE(os.stat(parent).st_mode) == 0o755
    assert not path.exists()


def test_database_parent_must_be_owned_by_current_user(tmp_path, monkeypatch) -> None:
    parent = tmp_path / "private-parent"
    parent.mkdir(mode=0o700)
    os.chmod(parent, 0o700)
    path = parent / "dashboard.db"
    database = Database(path)
    monkeypatch.setattr(os, "geteuid", lambda: parent.stat().st_uid + 1)

    try:
        with pytest.raises(PermissionError, match="owned by the current user"):
            database.initialize()
    finally:
        database.dispose()

    assert not path.exists()


def test_public_parent_blocks_final_path_swap_before_schema_write(tmp_path) -> None:
    parent = tmp_path / "attacker-writable"
    parent.mkdir(mode=0o777)
    os.chmod(parent, 0o777)
    target = tmp_path / "unrelated.db"
    target.write_bytes(b"unrelated-data")
    path = parent / "dashboard.db"
    database = Database(path)
    path.symlink_to(target)

    try:
        with pytest.raises(PermissionError, match="no group or world permissions"):
            database.initialize()
    finally:
        database.dispose()

    assert target.read_bytes() == b"unrelated-data"


def test_new_database_directories_are_owner_only(tmp_path) -> None:
    first = tmp_path / "private"
    second = first / "nested"
    database = Database(second / "dashboard.db")

    try:
        database.initialize()
    finally:
        database.dispose()

    assert stat.S_IMODE(os.stat(first).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(second).st_mode) == 0o700


def test_database_rejects_symlink_created_after_configuration(tmp_path) -> None:
    path = tmp_path / "late-link.db"
    target = tmp_path / "target.db"
    target.touch(mode=0o644)
    os.chmod(target, 0o644)
    database = Database(path)
    path.symlink_to(target)

    try:
        with pytest.raises(ValueError, match="symbolic link"):
            database.initialize()
    finally:
        database.dispose()

    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_connection_inode_check_precedes_every_sqlite_write(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "connection-swap.db"
    target = tmp_path / "unrelated-target.db"
    sentinel = b"unrelated-target-must-remain-byte-identical"
    target.write_bytes(sentinel)
    database = Database(path)
    original_creator = cast(Any, database.engine.pool._creator)

    def swapped_creator():
        path.unlink()
        path.symlink_to(target)
        return original_creator()

    monkeypatch.setattr(database.engine.pool, "_creator", swapped_creator)
    with pytest.raises(ValueError, match=r"unexpected database path|symbolic link"):
        database.initialize()

    assert target.read_bytes() == sentinel
    assert not target.with_name(f"{target.name}-wal").exists()
    assert not target.with_name(f"{target.name}-shm").exists()


def test_checkout_rejects_post_init_main_hardlink_before_write(
    database: Database, tmp_path
) -> None:
    alias = tmp_path / "post-init-hardlink.db"
    os.link(database.path, alias)

    with pytest.raises(ValueError, match="exactly one hard link"):
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE must_not_exist (value TEXT NOT NULL)"
            )

    with sqlite3.connect(f"file:{alias}?mode=ro", uri=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "must_not_exist" not in tables


def test_checkout_rejects_path_replacement_without_mutating_either_database(
    database: Database, tmp_path
) -> None:
    with database.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    original_before = database.path.read_bytes()
    moved = tmp_path / "moved-original.db"
    database.path.rename(moved)

    staged = tmp_path / "staged-replacement.db"
    with sqlite3.connect(staged) as connection:
        connection.execute("CREATE TABLE replacement_sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO replacement_sentinel VALUES ('untouched')")
    os.chmod(staged, 0o600)
    staged.rename(database.path)
    replacement_before = database.path.read_bytes()

    with pytest.raises(ValueError, match=r"changed|unexpected"):
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE must_not_exist (value TEXT NOT NULL)"
            )

    assert moved.read_bytes() == original_before
    assert database.path.read_bytes() == replacement_before
    with sqlite3.connect(f"file:{database.path}?mode=ro", uri=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "replacement_sentinel" in tables
    assert "must_not_exist" not in tables


def test_checkout_rejects_post_init_hardlinked_sidecar_before_write(
    database: Database, tmp_path
) -> None:
    sidecar = database.path.with_name(f"{database.path.name}-wal")
    if not sidecar.exists():
        sidecar.write_bytes(b"post-init-sidecar")
        os.chmod(sidecar, 0o600)
    alias = tmp_path / "post-init-sidecar-hardlink"
    os.link(sidecar, alias)

    with pytest.raises(ValueError, match="exactly one hard link"):
        with database.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE must_not_exist (value TEXT NOT NULL)"
            )

    assert alias.exists()
    with sqlite3.connect(f"file:{database.path}?mode=ro", uri=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "must_not_exist" not in tables


def test_hard_linked_database_is_rejected_without_mutating_original(tmp_path) -> None:
    parent = tmp_path / "private-hardlink"
    parent.mkdir(mode=0o700)
    os.chmod(parent, 0o700)
    original = parent / "original.db"
    with sqlite3.connect(original) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES ('preserve-me')")
    os.chmod(original, 0o640)
    original_bytes = original.read_bytes()
    original_mode = stat.S_IMODE(original.stat().st_mode)
    with sqlite3.connect(f"file:{original}?mode=ro", uri=True) as connection:
        original_schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()

    linked = parent / "dashboard.db"
    os.link(original, linked)
    database = Database(linked)
    try:
        with pytest.raises(ValueError, match="exactly one hard link"):
            database.initialize()
    finally:
        database.dispose()

    with sqlite3.connect(f"file:{original}?mode=ro", uri=True) as connection:
        schema_after = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    assert original.read_bytes() == original_bytes
    assert stat.S_IMODE(original.stat().st_mode) == original_mode
    assert schema_after == original_schema
    assert not linked.with_name(f"{linked.name}-wal").exists()
    assert not linked.with_name(f"{linked.name}-shm").exists()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_hard_linked_existing_sidecar_is_rejected_before_sqlite(
    tmp_path, suffix: str
) -> None:
    parent = tmp_path / f"private-sidecar{suffix}"
    parent.mkdir(mode=0o700)
    os.chmod(parent, 0o700)
    path = parent / "dashboard.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
    os.chmod(path, 0o600)
    original = parent / f"original{suffix}"
    original.write_bytes(b"sidecar-sentinel")
    os.chmod(original, 0o640)
    sidecar = path.with_name(f"{path.name}{suffix}")
    os.link(original, sidecar)
    original_bytes = original.read_bytes()
    original_mode = stat.S_IMODE(original.stat().st_mode)

    database = Database(path)
    try:
        with pytest.raises(ValueError, match="exactly one hard link"):
            database.initialize()
    finally:
        database.dispose()

    assert original.read_bytes() == original_bytes
    assert stat.S_IMODE(original.stat().st_mode) == original_mode


def test_existing_v0001_database_receives_integrity_migration(tmp_path) -> None:
    database = Database(tmp_path / "upgrade.db")
    prepare_v0001_database(database)
    database = restart_database(database)

    try:
        database.initialize()
        with database.engine.connect() as connection:
            versions = list(
                connection.execute(
                    text("SELECT version FROM schema_migration ORDER BY version")
                ).scalars()
            )
            trigger_sql = connection.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='trigger' AND name='send_attempt_is_immutable'"
                )
            ).scalar_one()
    finally:
        database.dispose()

    assert versions == [
        v0001_constraints.VERSION,
        v0002_integrity.VERSION,
        v0003_send_invariants.VERSION,
        v0004_audit_cascade.VERSION,
        v0005_send_history.VERSION,
        v0006_send_state_timing.VERSION,
        v0007_send_provenance.VERSION,
        v0008_history_hardening.VERSION,
        v0009_integrity_completion.VERSION,
        v0010_takeover_guards.VERSION,
    ]
    assert "NEW.candidate_id IS NOT OLD.candidate_id" in trigger_sql


def test_existing_database_receives_session_purge_audit_migration(tmp_path) -> None:
    database = Database(tmp_path / "audit-upgrade.db")
    prepare_v0001_database(database)
    with database.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO session "
                "(id, created_at, label, purge_after, nav_budget, nav_used, "
                "send_enabled) VALUES "
                "('session-audit-upgrade', :now, 'Upgrade', :now, 120, 0, 0)"
            ),
            {"now": NOW},
        )
        connection.execute(
            text(
                "INSERT INTO audit_log "
                "(id, session_id, at, actor, action, subject_type, subject_id, "
                "detail, correlation_id) VALUES "
                "('audit-upgrade', 'session-audit-upgrade', :now, 'system', "
                "'session.created', 'session', 'session-audit-upgrade', '{}', "
                "'upgrade-test')"
            ),
            {"now": NOW},
        )

    database = restart_database(database)
    try:
        database.initialize()
        with database.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM session WHERE id='session-audit-upgrade'")
            )
            remaining = connection.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE id='audit-upgrade'")
            ).scalar_one()
            migrated = connection.execute(
                text("SELECT 1 FROM schema_migration WHERE version=:version"),
                {"version": v0004_audit_cascade.VERSION},
            ).scalar_one()
    finally:
        database.dispose()

    assert remaining == 0
    assert migrated == 1


@pytest.mark.parametrize(
    ("state", "confirm_send", "expected"),
    [
        ("SENDING", 2, "legacy boolean value"),
        ("AMBIGUOUS", 1, "legacy send-attempt state"),
    ],
)
def test_v0002_preflight_rejects_incompatible_legacy_rows_without_recording(
    tmp_path, state: str, confirm_send: int, expected: str
) -> None:
    database = Database(tmp_path / f"legacy-{state}-{confirm_send}.db")
    prepare_v0001_database(database)
    candidate_id, draft_id = seed_candidate(database, f"legacy-{state}-{confirm_send}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        insert_attempt_sql(
            connection,
            attempt_id=f"attempt-legacy-{state}-{confirm_send}",
            candidate_id=candidate_id,
            draft_id=draft_id,
            state=state,
            confirm_send=confirm_send,
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    database = restart_database(database)
    try:
        with pytest.raises(
            RuntimeError,
            match=rf"{v0002_integrity.VERSION}.*{expected}",
        ):
            database.initialize()
    finally:
        database.dispose()

    with sqlite3.connect(database.path) as connection:
        recorded = connection.execute(
            "SELECT 1 FROM schema_migration WHERE version=?",
            (v0002_integrity.VERSION,),
        ).fetchone()
    assert recorded is None


@pytest.mark.parametrize("failure_after", range(1, len(v0002_integrity.STATEMENTS) + 1))
def test_v0002_each_statement_is_atomic_and_retryable(
    tmp_path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v2-{failure_after}.db")
    prepare_v0001_database(database)
    baseline = migration_schema_objects(database.path)
    database = restart_database(database)
    original_apply = v0002_integrity.apply

    def interrupted_apply(connection) -> None:
        v0002_integrity.preflight_integrity(
            connection,
            version=v0002_integrity.VERSION,
        )
        for index, statement in enumerate(v0002_integrity.STATEMENTS, start=1):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after statement {index}")

    monkeypatch.setattr(v0002_integrity, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"statement {failure_after}"):
        database.initialize()

    assert migration_schema_objects(database.path) == baseline
    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0002_integrity.VERSION,),
            ).fetchone()
            is None
        )

    monkeypatch.setattr(v0002_integrity, "apply", original_apply)
    database.initialize()
    try:
        with database.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT 1 FROM schema_migration WHERE version=:version"),
                    {"version": v0002_integrity.VERSION},
                ).scalar_one()
                == 1
            )
    finally:
        database.dispose()


@pytest.mark.parametrize("partial_count", range(1, len(v0002_integrity.STATEMENTS) + 1))
def test_v0002_reconciles_every_legacy_partial_ddl_state(
    tmp_path, partial_count: int
) -> None:
    database = Database(tmp_path / f"partial-v2-{partial_count}.db")
    prepare_v0001_database(database)
    with database.engine.begin() as connection:
        v0002_integrity.preflight_integrity(
            connection,
            version=v0002_integrity.VERSION,
        )
        for statement in v0002_integrity.STATEMENTS[:partial_count]:
            connection.exec_driver_sql(statement)
    database = restart_database(database)

    try:
        database.initialize()
        with database.engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT 1 FROM schema_migration WHERE version=:version"),
                    {"version": v0002_integrity.VERSION},
                ).scalar_one()
                == 1
            )
            trigger_names = list(
                connection.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='trigger' ORDER BY name"
                    )
                ).scalars()
            )
    finally:
        database.dispose()

    assert {
        "send_attempt_is_immutable",
        "send_resolution_transition_is_valid",
        "validate_session_booleans_insert",
        "validate_send_attempt_booleans_update",
    } <= set(trigger_names)


def test_partial_unique_index_rejects_a_second_live_send(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "live")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-live-1",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(IntegrityError):
        with database.sessions.begin() as db_session:
            db_session.add(
                attempt(
                    attempt_id="attempt-live-2",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                )
            )


def test_failed_dry_run_does_not_block_a_real_send(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "dry")
    with database.sessions.begin() as db_session:
        db_session.add_all(
            [
                attempt(
                    attempt_id="attempt-dry-1",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="DRY_RUN_FAILED",
                    confirm_send=False,
                    finished_at=NOW,
                ),
                attempt(
                    attempt_id="attempt-dry-2",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                ),
            ]
        )


def test_direct_sql_cannot_bypass_live_send_index_with_non_boolean(
    database: Database,
) -> None:
    candidate_id, draft_id = seed_candidate(database, "boolean-index")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-boolean-live",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(DBAPIError, match="invalid boolean"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, resolution) "
                    "SELECT :new_id, candidate_id, draft_id, :new_key, body_sha256, "
                    "2, state, started_at, resolution FROM send_attempt WHERE id=:id"
                ),
                {
                    "new_id": "attempt-boolean-bypass",
                    "new_key": "b" * 64,
                    "id": "attempt-boolean-live",
                },
            )


def test_all_persisted_boolean_columns_reject_non_booleans(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "all-booleans")
    with database.sessions.begin() as db_session:
        brief = RoleBrief(
            id="brief-all-booleans",
            session_id="session-all-booleans",
            version=1,
            created_at=NOW,
            job_description="Test",
            target_titles=[],
            location="Anywhere",
            industries=[],
            positive_keywords=[],
            negative_keywords=[],
            message_tone="plain",
            weights_version="v1",
        )
        db_session.add(brief)
        db_session.flush()
        db_session.add(
            CandidateScore(
                id="score-all-booleans",
                candidate_id=candidate_id,
                brief_id=brief.id,
                weights_version="v1",
                stage="provisional",
                score=0.0,
                score_lower=0.0,
                score_upper=0.0,
                confidence=0.0,
                confidence_band="low",
                computed_at=NOW,
                is_current=True,
            )
        )
        db_session.add(
            DraftClaim(
                id="claim-all-booleans",
                draft_id=draft_id,
                claim_text="Test",
                grounded=True,
            )
        )
        db_session.add(
            attempt(
                attempt_id="attempt-all-booleans",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="DRY_RUN_OK",
                confirm_send=False,
                finished_at=NOW,
            )
        )

    statements = (
        "UPDATE session SET send_enabled=2 WHERE id='session-all-booleans'",
        "UPDATE score SET is_current=2 WHERE id='score-all-booleans'",
        "UPDATE draft_claim SET grounded=2 WHERE id='claim-all-booleans'",
        "UPDATE send_attempt SET confirm_send=2 WHERE id='attempt-all-booleans'",
        "UPDATE send_attempt SET tool_sent=2 WHERE id='attempt-all-booleans'",
        "UPDATE send_attempt SET tool_recipient_selected=2 "
        "WHERE id='attempt-all-booleans'",
    )
    for statement in statements:
        with pytest.raises(DBAPIError, match=r"boolean|CHECK constraint"):
            with database.engine.begin() as connection:
                connection.exec_driver_sql(statement)


def test_send_attempt_cannot_be_inserted_pre_resolved(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "pre-resolved")

    with pytest.raises(DBAPIError, match="must start unresolved"):
        with database.engine.begin() as connection:
            insert_attempt_sql(
                connection,
                attempt_id="attempt-pre-resolved",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                finished_at=NOW,
                resolution="confirmed_sent",
                resolved_at=NOW,
                resolution_note="already resolved",
            )


def test_ambiguous_attempt_must_be_finished_when_inserted(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "unfinished-ambiguous")

    with pytest.raises(DBAPIError, match="every outcome finished"):
        with database.engine.begin() as connection:
            insert_attempt_sql(
                connection,
                attempt_id="attempt-unfinished-ambiguous",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
            )


def test_malformed_legacy_ambiguous_attempt_cannot_be_rewritten(
    database: Database,
) -> None:
    candidate_id, draft_id = seed_candidate(database, "malformed-ambiguous")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER send_attempt_insert_is_valid")
        connection.exec_driver_sql("DROP TRIGGER send_attempt_state_timing_insert")
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        insert_attempt_sql(
            connection,
            attempt_id="attempt-malformed-ambiguous",
            candidate_id=candidate_id,
            draft_id=draft_id,
            state="AMBIGUOUS",
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(
        DBAPIError,
        match=r"immutable|every outcome finished|must match its approved draft",
    ):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE send_attempt SET body_sha256=:hash, state='SENDING' "
                    "WHERE id='attempt-malformed-ambiguous'"
                ),
                {"hash": "f" * 64},
            )


def test_resolution_requires_finished_ambiguous_attempt(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "invalid-resolution-timing")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-invalid-resolution-timing",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(DBAPIError, match="finished AMBIGUOUS transition"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE send_attempt SET resolution='confirmed_sent', "
                    "resolved_at=:now, resolution_note='invalid timing' "
                    "WHERE id='attempt-invalid-resolution-timing'"
                ),
                {"now": NOW},
            )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "attempt-reparented"),
        ("candidate_id", "candidate-immutable-target"),
        ("draft_id", "draft-immutable-target"),
        ("idempotency_key", "c" * 64),
        ("body_sha256", "d" * 64),
        ("confirm_send", 0),
        ("state", "FAILED_CONCLUSIVE"),
        ("tool_status", "changed"),
        ("tool_sent", 1),
        ("tool_recipient_selected", 1),
        ("tool_url", "https://www.linkedin.com/changed"),
        ("raw_response", '{"changed":true}'),
        ("error_class", "ChangedError"),
        ("error_message", "changed"),
        ("started_at", "2026-09-02T13:00:00+00:00"),
        ("finished_at", "2026-09-02T13:00:00+00:00"),
    ],
)
def test_finished_attempt_protects_every_non_resolution_column(
    database: Database, column: str, value: object
) -> None:
    candidate_id, draft_id = seed_candidate(database, "immutable")
    seed_candidate(database, "immutable-target")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-immutable",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with pytest.raises(DBAPIError, match="immutable"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE send_attempt SET {column}=:value WHERE id=:id"),
                {"value": value, "id": "attempt-immutable"},
            )


@pytest.mark.parametrize("column", ["resolved_at", "resolution_note"])
def test_unresolved_attempt_rejects_partial_resolution_updates(
    database: Database, column: str
) -> None:
    candidate_id, draft_id = seed_candidate(database, f"partial-{column}")
    attempt_id = f"attempt-partial-{column}"
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id=attempt_id,
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with pytest.raises(DBAPIError, match="finished AMBIGUOUS transition"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE send_attempt SET {column}=:value WHERE id=:id"),
                {"value": NOW, "id": attempt_id},
            )


def test_resolution_transitions_once_and_is_final(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "resolution")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-resolution",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="AMBIGUOUS",
                confirm_send=True,
                finished_at=NOW,
            )
        )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE send_attempt SET resolution='confirmed_not_sent', "
                "resolved_at=:at, resolution_note='checked LinkedIn' WHERE id=:id"
            ),
            {"at": NOW, "id": "attempt-resolution"},
        )

    with pytest.raises(DBAPIError, match="already set"):
        with database.engine.begin() as connection:
            connection.execute(
                text("UPDATE send_attempt SET resolution_note='changed' WHERE id=:id"),
                {"id": "attempt-resolution"},
            )

    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-new-after-resolution",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )


def test_send_history_deletes_require_full_session_purge(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "history-delete")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-history-delete",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    direct_deletes = (
        "DELETE FROM send_attempt WHERE id='attempt-history-delete'",
        f"DELETE FROM message_draft WHERE id='{draft_id}'",
        f"DELETE FROM candidate WHERE id='{candidate_id}'",
    )
    for statement in direct_deletes:
        with pytest.raises(DBAPIError, match="full-session purge"):
            with database.engine.begin() as connection:
                connection.exec_driver_sql(statement)

    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM session WHERE id=:id"),
            {"id": "session-history-delete"},
        )
        counts = {
            table: connection.exec_driver_sql(
                f"SELECT COUNT(*) FROM {table}"
            ).scalar_one()
            for table in ("candidate", "message_draft", "send_attempt")
        }

    assert counts == {"candidate": 0, "message_draft": 0, "send_attempt": 0}


@pytest.mark.parametrize("conflict", ["primary_key", "idempotency_key"])
def test_insert_or_replace_cannot_overwrite_send_history(
    database: Database, conflict: str
) -> None:
    candidate_id, draft_id = seed_candidate(database, f"replace-{conflict}")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id=f"attempt-replace-{conflict}",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    existing_id = f"attempt-replace-{conflict}"
    replacement_id = existing_id if conflict == "primary_key" else f"new-{existing_id}"
    existing_key = (existing_id + "0" * 64)[:64]
    with pytest.raises(DBAPIError, match=r"already exists|full-session purge"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT OR REPLACE INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, resolution) VALUES "
                    "(:id, :candidate_id, :draft_id, :key, :hash, 1, "
                    "'SENDING', :started_at, 'unresolved')"
                ),
                {
                    "id": replacement_id,
                    "candidate_id": candidate_id,
                    "draft_id": draft_id,
                    "key": existing_key,
                    "hash": "a" * 64,
                    "started_at": NOW,
                },
            )

    with database.engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, idempotency_key FROM send_attempt "
                "WHERE candidate_id=:candidate_id"
            ),
            {"candidate_id": candidate_id},
        ).all()
    assert rows == [(existing_id, existing_key)]


@pytest.mark.parametrize("record_type", ["confirmation", "attempt"])
@pytest.mark.parametrize("mismatch", ["candidate", "hash"])
def test_send_records_must_match_their_approved_draft(
    database: Database, record_type: str, mismatch: str
) -> None:
    candidate_id, draft_id = seed_candidate(database, f"provenance-{record_type}")
    other_candidate_id, _ = seed_candidate(database, f"provenance-{record_type}-other")
    record_candidate = other_candidate_id if mismatch == "candidate" else candidate_id
    body_sha256 = "b" * 64 if mismatch == "hash" else "a" * 64

    with pytest.raises(DBAPIError, match="must match its approved draft"):
        with database.sessions.begin() as db_session:
            if record_type == "confirmation":
                db_session.add(
                    confirmation(
                        token=f"token-{record_type}-{mismatch}",
                        candidate_id=record_candidate,
                        draft_id=draft_id,
                        body_sha256=body_sha256,
                    )
                )
            else:
                record = attempt(
                    attempt_id=f"attempt-{record_type}-{mismatch}",
                    candidate_id=record_candidate,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                )
                record.body_sha256 = body_sha256
                db_session.add(record)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("candidate_id", "candidate-confirmation-rewrite-target"),
        ("body_sha256", "b" * 64),
    ],
)
def test_confirmation_provenance_cannot_be_rewritten(
    database: Database, column: str, value: str
) -> None:
    candidate_id, draft_id = seed_candidate(database, "confirmation-rewrite-source")
    seed_candidate(database, "confirmation-rewrite-target")
    with database.sessions.begin() as db_session:
        db_session.add(
            confirmation(
                token="token-confirmation-rewrite",
                candidate_id=candidate_id,
                draft_id=draft_id,
            )
        )

    with pytest.raises(
        DBAPIError,
        match=r"send_confirmation is immutable|must match its approved draft",
    ):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE send_confirmation SET {column}=:value WHERE token=:token"
                ),
                {"value": value, "token": "token-confirmation-rewrite"},
            )

    with database.engine.connect() as connection:
        persisted = connection.execute(
            text(
                "SELECT candidate_id, body_sha256 FROM send_confirmation "
                "WHERE token=:token"
            ),
            {"token": "token-confirmation-rewrite"},
        ).one()
    assert tuple(persisted) == (candidate_id, "a" * 64)


@pytest.mark.parametrize("record_type", ["confirmation", "attempt"])
@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "draft-rewrite-target-id"),
        ("candidate_id", "candidate-draft-rewrite-target"),
        ("version", 2),
        ("body", "Changed"),
        ("body_sha256", "c" * 64),
        ("char_count", 7),
    ],
)
def test_referenced_draft_requires_a_new_version_for_edits(
    database: Database, record_type: str, column: str, value: object
) -> None:
    candidate_id, draft_id = seed_candidate(database, "draft-rewrite-source")
    seed_candidate(database, "draft-rewrite-target")
    with database.sessions.begin() as db_session:
        if record_type == "confirmation":
            db_session.add(
                confirmation(
                    token="token-draft-rewrite",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                )
            )
        else:
            db_session.add(
                attempt(
                    attempt_id="attempt-draft-rewrite",
                    candidate_id=candidate_id,
                    draft_id=draft_id,
                    state="SENDING",
                    confirm_send=True,
                )
            )

    with pytest.raises(DBAPIError, match="create a new draft version"):
        with database.engine.begin() as connection:
            connection.execute(
                text(f"UPDATE message_draft SET {column}=:value WHERE id=:id"),
                {"value": value, "id": draft_id},
            )


def test_new_draft_version_remains_allowed_after_reference(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "new-draft-version")
    with database.sessions.begin() as db_session:
        db_session.add(
            confirmation(
                token="token-new-draft-version",
                candidate_id=candidate_id,
                draft_id=draft_id,
            )
        )
        db_session.add(
            MessageDraft(
                id="draft-new-version-2",
                candidate_id=candidate_id,
                version=2,
                body="Updated hello",
                body_sha256="d" * 64,
                char_count=13,
                generator="manual",
                grounding_status="pass",
                grounding_report={},
                created_at=NOW,
            )
        )


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "attempt-reidentified-at-completion"),
        ("candidate_id", "candidate-completion-target"),
        ("draft_id", "draft-completion-target"),
        ("idempotency_key", "f" * 64),
        ("body_sha256", "e" * 64),
        ("confirm_send", 0),
        ("started_at", "2026-09-02T11:00:00+00:00"),
    ],
)
def test_send_identity_cannot_change_while_completing(
    database: Database, column: str, value: object
) -> None:
    candidate_id, draft_id = seed_candidate(database, "completion-source")
    seed_candidate(database, "completion-target")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-completion-identity",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with pytest.raises(
        DBAPIError,
        match=(
            r"identity and provenance are immutable|must match its approved draft|"
            r"confirm_send state family"
        ),
    ):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    f"UPDATE send_attempt SET {column}=:value, "
                    "state='SENT', finished_at=:finished_at "
                    "WHERE id='attempt-completion-identity'"
                ),
                {
                    "value": value,
                    "finished_at": NOW,
                },
            )


def test_send_result_can_complete_without_changing_identity(database: Database) -> None:
    candidate_id, draft_id = seed_candidate(database, "legal-completion")
    with database.sessions.begin() as db_session:
        db_session.add(
            attempt(
                attempt_id="attempt-legal-completion",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
                confirm_send=True,
            )
        )

    with database.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE send_attempt SET state='SENT', tool_status='success', "
                "tool_sent=1, finished_at=:finished_at "
                "WHERE id='attempt-legal-completion'"
            ),
            {"finished_at": NOW},
        )
        row = connection.execute(
            text(
                "SELECT candidate_id, draft_id, state, finished_at "
                "FROM send_attempt WHERE id='attempt-legal-completion'"
            )
        ).one()

    assert tuple(row) == (candidate_id, draft_id, "SENT", NOW)


@pytest.mark.parametrize(
    ("state", "confirm_send", "finished_at"),
    [
        ("SENDING", 1, None),
        ("SENT", 1, NOW),
        ("FAILED_CONCLUSIVE", 1, NOW),
        ("AMBIGUOUS", 1, NOW),
        ("DRY_RUN_OK", 0, NOW),
        ("DRY_RUN_FAILED", 0, NOW),
    ],
)
def test_every_send_state_accepts_only_its_valid_timing(
    database: Database, state: str, confirm_send: int, finished_at: str | None
) -> None:
    candidate_id, draft_id = seed_candidate(database, f"state-{state}")
    with database.engine.begin() as connection:
        insert_attempt_sql(
            connection,
            attempt_id=f"attempt-valid-{state}",
            candidate_id=candidate_id,
            draft_id=draft_id,
            state=state,
            confirm_send=confirm_send,
            finished_at=finished_at,
        )

    invalid_finished_at = NOW if finished_at is None else None
    with pytest.raises(DBAPIError, match=r"state|finished|CHECK constraint"):
        with database.engine.begin() as connection:
            insert_attempt_sql(
                connection,
                attempt_id=f"attempt-invalid-{state}",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state=state,
                confirm_send=confirm_send,
                finished_at=invalid_finished_at,
            )


def test_malformed_sent_cannot_be_rewritten_to_bypass_new_send(
    database: Database,
) -> None:
    candidate_id, draft_id = seed_candidate(database, "sent-null-bypass")
    with database.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER send_attempt_state_timing_insert")
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        insert_attempt_sql(
            connection,
            attempt_id="attempt-sent-null",
            candidate_id=candidate_id,
            draft_id=draft_id,
            state="SENT",
            confirm_send=1,
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    with pytest.raises(DBAPIError, match="every outcome finished"):
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE send_attempt SET state='FAILED_CONCLUSIVE', "
                    "finished_at=:finished WHERE id='attempt-sent-null'"
                ),
                {"finished": NOW},
            )

    with pytest.raises(IntegrityError):
        with database.engine.begin() as connection:
            insert_attempt_sql(
                connection,
                attempt_id="attempt-new-after-malformed-sent",
                candidate_id=candidate_id,
                draft_id=draft_id,
                state="SENDING",
            )


@pytest.mark.parametrize(
    ("state", "finished_at"),
    [("SENDING", NOW), ("SENT", None)],
)
def test_v0006_preflight_rejects_legacy_state_timing(
    tmp_path, state: str, finished_at: str | None
) -> None:
    database = Database(tmp_path / f"legacy-state-timing-{state}.db")
    database.initialize()
    candidate_id, draft_id = seed_candidate(database, f"legacy-state-timing-{state}")
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0006_send_state_timing.VERSION},
        )
        for name in (
            "send_attempt_state_timing_insert",
            "send_attempt_state_timing_update",
        ):
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=ON")
        insert_attempt_sql(
            connection,
            attempt_id=f"attempt-legacy-timing-{state}",
            candidate_id=candidate_id,
            draft_id=draft_id,
            state=state,
            confirm_send=1,
            finished_at=finished_at,
        )
        connection.exec_driver_sql("PRAGMA ignore_check_constraints=OFF")

    database = restart_database(database)
    try:
        with pytest.raises(RuntimeError, match="incompatible send state timing"):
            database.initialize()
    finally:
        database.dispose()

    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0006_send_state_timing.VERSION,),
            ).fetchone()
            is None
        )


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0006_send_state_timing.STATEMENTS) + 1)
)
def test_v0006_each_statement_is_atomic_and_retryable(
    tmp_path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v6-{failure_after}.db")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0006_send_state_timing.VERSION},
        )
        for name in (
            "send_attempt_state_timing_insert",
            "send_attempt_state_timing_update",
        ):
            connection.exec_driver_sql(f'DROP TRIGGER "{name}"')
    baseline = migration_schema_objects(database.path)
    database = restart_database(database)
    original_apply = v0006_send_state_timing.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(v0006_send_state_timing.STATEMENTS, start=1):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after statement {index}")

    monkeypatch.setattr(v0006_send_state_timing, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"statement {failure_after}"):
        database.initialize()

    assert migration_schema_objects(database.path) == baseline
    monkeypatch.setattr(v0006_send_state_timing, "apply", original_apply)
    database.initialize()
    database.dispose()


@pytest.mark.parametrize("record_type", ["confirmation", "attempt"])
@pytest.mark.parametrize("mismatch", ["candidate", "hash"])
def test_v0007_preflight_rejects_legacy_provenance_mismatch(
    tmp_path, record_type: str, mismatch: str
) -> None:
    database = Database(tmp_path / f"legacy-provenance-{record_type}-{mismatch}.db")
    database.initialize()
    candidate_id, draft_id = seed_candidate(
        database, f"legacy-provenance-{record_type}-{mismatch}"
    )
    other_candidate_id, _ = seed_candidate(
        database, f"legacy-provenance-{record_type}-{mismatch}-other"
    )
    record_candidate = other_candidate_id if mismatch == "candidate" else candidate_id
    body_sha256 = "b" * 64 if mismatch == "hash" else "a" * 64
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0007_send_provenance.VERSION},
        )
        connection.exec_driver_sql(f"DROP TRIGGER send_{record_type}_provenance_insert")
        if record_type == "confirmation":
            connection.execute(
                text(
                    "INSERT INTO send_confirmation "
                    "(token, candidate_id, draft_id, body_sha256, created_at, "
                    "expires_at) VALUES "
                    "('legacy-token', :candidate, :draft, :hash, :now, :now)"
                ),
                {
                    "candidate": record_candidate,
                    "draft": draft_id,
                    "hash": body_sha256,
                    "now": NOW,
                },
            )
        else:
            connection.execute(
                text(
                    "INSERT INTO send_attempt "
                    "(id, candidate_id, draft_id, idempotency_key, body_sha256, "
                    "confirm_send, state, started_at, resolution) VALUES "
                    "('legacy-attempt', :candidate, :draft, :key, :hash, 1, "
                    "'SENDING', :now, 'unresolved')"
                ),
                {
                    "candidate": record_candidate,
                    "draft": draft_id,
                    "key": f"legacy-{mismatch}".ljust(64, "0"),
                    "hash": body_sha256,
                    "now": NOW,
                },
            )

    database = restart_database(database)
    try:
        with pytest.raises(RuntimeError, match="incompatible send provenance"):
            database.initialize()
    finally:
        database.dispose()

    with sqlite3.connect(database.path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM schema_migration WHERE version=?",
                (v0007_send_provenance.VERSION,),
            ).fetchone()
            is None
        )
        table = f"send_{record_type}"
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)


@pytest.mark.parametrize(
    "failure_after", range(1, len(v0007_send_provenance.STATEMENTS) + 1)
)
def test_v0007_each_statement_is_atomic_and_retryable(
    tmp_path, monkeypatch, failure_after: int
) -> None:
    database = Database(tmp_path / f"interrupted-v7-{failure_after}.db")
    database.initialize()
    with database.engine.begin() as connection:
        connection.execute(
            text("DELETE FROM schema_migration WHERE version=:version"),
            {"version": v0007_send_provenance.VERSION},
        )
    baseline = migration_schema_objects(database.path)
    database = restart_database(database)
    original_apply = v0007_send_provenance.apply

    def interrupted_apply(connection) -> None:
        for index, statement in enumerate(v0007_send_provenance.STATEMENTS, start=1):
            connection.exec_driver_sql(statement)
            if index == failure_after:
                raise RuntimeError(f"interrupted after statement {index}")

    monkeypatch.setattr(v0007_send_provenance, "apply", interrupted_apply)
    with pytest.raises(RuntimeError, match=f"statement {failure_after}"):
        database.initialize()

    assert migration_schema_objects(database.path) == baseline
    monkeypatch.setattr(v0007_send_provenance, "apply", original_apply)
    database.initialize()
    database.dispose()
