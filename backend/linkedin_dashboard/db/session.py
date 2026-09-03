from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from errno import ELOOP
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import URL, Connection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

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
    v0011_purged_evidence_ancestry,
    v0012_score_session_provenance,
    v0013_history_root_immutability,
    v0014_history_identity_completion,
    v0015_approved_evidence_roots,
    v0016_durable_queue,
    v0017_role_discovery,
)
from linkedin_dashboard.db.models import Base
from linkedin_dashboard.settings import normalize_database_path

_MIGRATION_MODULES = (
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
    v0011_purged_evidence_ancestry,
    v0012_score_session_provenance,
    v0013_history_root_immutability,
    v0014_history_identity_completion,
    v0015_approved_evidence_roots,
    v0016_durable_queue,
    v0017_role_discovery,
)

_SCHEMA_ACTIONS = {
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_CREATE_VTABLE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_DROP_VTABLE,
}

_INITIALIZATION_LOCKS_GUARD = Lock()
_INITIALIZATION_LOCKS: dict[Path, Lock] = {}
_INITIALIZATION_LOCK_TIMEOUT_SECONDS = 30.0
_WORKER_LOCKS_GUARD = Lock()
_WORKER_LOCKS: dict[int, Path] = {}
_WORKER_LOCK_PATHS: set[Path] = set()


class Database:
    """SQLite ownership boundary for the local dashboard."""

    def __init__(self, path: Path) -> None:
        self.path = normalize_database_path(path)
        self._database_fd: int | None = None
        self._initialize_lock = Lock()
        self._initialized = False
        self._initializing = False
        self.engine = _create_runtime_engine(
            self.path,
            lambda: self._database_fd,
            lambda: self._initialized and not self._initializing,
        )
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def initialize(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            initialization_lock = _initialization_lock_for(self.path)
            if not initialization_lock.acquire(
                timeout=_INITIALIZATION_LOCK_TIMEOUT_SECONDS
            ):
                raise TimeoutError(
                    "timed out waiting for another database initialization to finish"
                )
            self._initializing = True
            database_fd: int | None = None
            migration_engine: Engine | None = None
            try:
                _create_private_directories(self.path.parent)
                _require_private_directory(self.path.parent)
                database_fd = _open_owner_only_file(self.path, create=True)
                self._database_fd = database_fd
                _secure_existing_sidecars(self.path)
                migration_engine, _ = _create_migration_engine(self.path, database_fd)
                try:
                    with migration_engine.connect() as connection:
                        connection.exec_driver_sql("BEGIN IMMEDIATE")
                        try:
                            # The blank/populated decision belongs to the write
                            # transaction.  Looking before BEGIN IMMEDIATE lets two
                            # first bootstraps both conclude that they own an empty DB.
                            bootstrap = _database_has_no_user_schema(connection)
                            migration_authorizer = connection.info.get(
                                "migration_authorizer"
                            )
                            if not isinstance(
                                migration_authorizer, _MigrationAuthorizer
                            ):
                                raise RuntimeError(
                                    "migration connection has no local authority"
                                )
                            migrations_applied = self._initialize_schema(
                                connection,
                                bootstrap=bootstrap,
                                migration_authorizer=migration_authorizer,
                            )
                            if migrations_applied:
                                _reconcile_invariant_objects(connection)
                            _verify_schema_and_contents(connection)
                        except BaseException:
                            connection.rollback()
                            raise
                        else:
                            connection.commit()
                finally:
                    migration_engine.dispose()
                _require_same_file(self.path, database_fd)
                _secure_existing_sidecars(self.path)
                self._initialized = True
            except BaseException:
                self.engine.dispose()
                if database_fd is not None:
                    os.close(database_fd)
                self._database_fd = None
                raise
            finally:
                self._initializing = False
                initialization_lock.release()

    def acquire_worker_lock(self) -> int:
        """Hold the exclusive sidecar lock required before queue initialization."""
        lock_path = self.path.with_name(f"{self.path.name}.queue.lock")
        with _WORKER_LOCKS_GUARD:
            if lock_path in _WORKER_LOCK_PATHS:
                raise BlockingIOError("another queue owner is active")
            _WORKER_LOCK_PATHS.add(lock_path)
        descriptor: int | None = None
        try:
            _create_private_directories(lock_path.parent)
            _require_private_directory(lock_path.parent)
            descriptor = _open_owner_only_file(lock_path, create=True)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                raise BlockingIOError("another queue owner is active") from error
            with _WORKER_LOCKS_GUARD:
                _WORKER_LOCKS[descriptor] = lock_path
            return descriptor
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            with _WORKER_LOCKS_GUARD:
                _WORKER_LOCK_PATHS.discard(lock_path)
            raise

    @staticmethod
    def release_worker_lock(descriptor: int) -> None:
        with _WORKER_LOCKS_GUARD:
            lock_path = _WORKER_LOCKS.pop(descriptor, None)
            if lock_path is None:
                return
            _WORKER_LOCK_PATHS.discard(lock_path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _initialize_schema(
        self,
        connection: Connection,
        *,
        bootstrap: bool,
        migration_authorizer: _MigrationAuthorizer,
    ) -> bool:
        _required_migration_versions()
        if bootstrap:
            Base.metadata.create_all(connection)
            connection.exec_driver_sql(
                "CREATE TABLE schema_migration "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
        elif not connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migration'"
        ).scalar_one_or_none():
            raise RuntimeError("existing database has no migration history")

        migrations_applied = False
        for migration in _MIGRATION_MODULES:
            version = migration.VERSION
            applied = connection.execute(
                text("SELECT 1 FROM schema_migration WHERE version = :version"),
                {"version": version},
            ).scalar_one_or_none()
            if applied is None:
                migrations_applied = True
                migration.apply(connection)
                with migration_authorizer.history_write():
                    connection.execute(
                        text(
                            "INSERT INTO schema_migration(version, applied_at) "
                            "VALUES (:version, "
                            "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
                        ),
                        {"version": version},
                    )
        return migrations_applied

    def writable(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                connection.exec_driver_sql("SELECT 1")
                connection.rollback()
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        with self._initialize_lock:
            self.engine.dispose()
            if self._database_fd is not None:
                os.close(self._database_fd)
                self._database_fd = None
            self._initialized = False


def _create_runtime_engine(
    path: Path,
    expected_fd: Callable[[], int | None],
    runtime_ready: Callable[[], bool],
) -> Engine:
    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(
        dbapi_connection: Any, connection_record: Any
    ) -> None:  # pragma: no cover - SQLAlchemy callback signature
        del connection_record
        if not runtime_ready():
            raise RuntimeError("runtime database is unavailable during initialization")
        database_fd = expected_fd()
        if database_fd is None:
            raise RuntimeError("database connections require secure initialization")
        _revalidate_storage(dbapi_connection, path, database_fd)
        dbapi_connection.set_authorizer(
            lambda *arguments: _sqlite_authorizer(
                *arguments,
                allow_schema_changes=False,
                allow_migration_history_insert=False,
            )
        )
        _configure_required_pragmas(dbapi_connection)

    @event.listens_for(engine, "checkout")
    def validate_sqlite_checkout(
        dbapi_connection: Any, connection_record: Any, connection_proxy: Any
    ) -> None:  # pragma: no cover - SQLAlchemy callback signature
        del connection_record, connection_proxy
        if not runtime_ready():
            raise RuntimeError("runtime database is unavailable during initialization")
        database_fd = expected_fd()
        if database_fd is None:
            raise RuntimeError("database connections require secure initialization")
        _revalidate_storage(dbapi_connection, path, database_fd)
        dbapi_connection.set_authorizer(
            lambda *arguments: _sqlite_authorizer(
                *arguments,
                allow_schema_changes=False,
                allow_migration_history_insert=False,
            )
        )
        _configure_required_pragmas(dbapi_connection)
        _verify_schema_and_contents_dbapi(dbapi_connection)

    return engine


class _MigrationAuthorizer:
    """Connection-local schema authority with one narrow history-write window."""

    def __init__(self) -> None:
        self._history_write = False

    def __call__(self, *arguments: Any) -> int:
        return _sqlite_authorizer(
            *arguments,
            allow_schema_changes=True,
            allow_migration_history_insert=self._history_write,
        )

    @contextmanager
    def history_write(self) -> Iterator[None]:
        if self._history_write:
            raise RuntimeError("migration history capability is already active")
        self._history_write = True
        try:
            yield
        finally:
            self._history_write = False


def _create_migration_engine(path: Path, expected_fd: int) -> tuple[Engine, None]:
    """Build a one-connection capability that can author only migration schema."""
    engine = create_engine(
        URL.create("sqlite", database=str(path)),
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def configure_migration_sqlite(
        dbapi_connection: Any, connection_record: Any
    ) -> None:  # pragma: no cover - SQLAlchemy callback signature
        _revalidate_storage(dbapi_connection, path, expected_fd)
        # A capability is minted for this physical connection only.  A second
        # connection from the same engine gets a different, closed capability.
        authorizer = _MigrationAuthorizer()
        connection_record.info["migration_authorizer"] = authorizer
        dbapi_connection.set_authorizer(authorizer)
        _configure_required_pragmas(dbapi_connection)

    # Preserve the original factory shape without returning an authority object:
    # authority is deliberately available only through the checked-out
    # connection's info mapping.
    return engine, None


def _initialization_lock_for(path: Path) -> Lock:
    """Return the bounded in-process coordinator for one canonical DB path."""
    with _INITIALIZATION_LOCKS_GUARD:
        return _INITIALIZATION_LOCKS.setdefault(path, Lock())


def _configure_required_pragmas(dbapi_connection: Any) -> None:
    """Restore and prove every SQLite invariant required by persistence guards."""
    cursor = dbapi_connection.cursor()
    try:
        # Install SQLite's own bounded lock wait before the first write-like
        # journal-mode operation.  The path lock coordinates Database instances
        # in this process; this protects against an independent local process.
        cursor.execute("PRAGMA busy_timeout=5000")

        cursor.execute("PRAGMA foreign_keys=ON")
        if cursor.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise RuntimeError("SQLite foreign keys could not be enabled")

        cursor.execute("PRAGMA recursive_triggers=ON")
        if cursor.execute("PRAGMA recursive_triggers").fetchone()[0] != 1:
            raise RuntimeError("SQLite recursive triggers could not be enabled")

        cursor.execute("PRAGMA ignore_check_constraints=OFF")
        if cursor.execute("PRAGMA ignore_check_constraints").fetchone()[0] != 0:
            raise RuntimeError("SQLite CHECK constraints could not be enabled")

        cursor.execute("PRAGMA writable_schema=OFF")
        if cursor.execute("PRAGMA writable_schema").fetchone()[0] != 0:
            raise RuntimeError("SQLite writable_schema could not be disabled")

        cursor.execute("PRAGMA trusted_schema=OFF")
        if cursor.execute("PRAGMA trusted_schema").fetchone()[0] != 0:
            raise RuntimeError("SQLite trusted_schema could not be disabled")

        cursor.execute("PRAGMA synchronous=FULL")
        if cursor.execute("PRAGMA synchronous").fetchone()[0] != 2:
            raise RuntimeError("SQLite synchronous durability could not be enabled")

        journal_mode = cursor.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(journal_mode).casefold() != "wal":
            raise RuntimeError("SQLite WAL journal mode could not be enabled")
        verified_mode = cursor.execute("PRAGMA journal_mode").fetchone()[0]
        if str(verified_mode).casefold() != "wal":
            raise RuntimeError("SQLite WAL journal mode could not be verified")

    finally:
        cursor.close()


def _revalidate_storage(dbapi_connection: Any, path: Path, expected_fd: int) -> None:
    """Re-prove the configured storage boundary before a pooled connection escapes."""
    if normalize_database_path(path) != path:
        raise ValueError("configured database path changed after startup")
    _require_private_directory(path.parent)
    _require_same_file(path, expected_fd)
    _verify_connection_target(dbapi_connection, path, expected_fd)
    _restore_owner_only_mode(path, expected_fd)
    _secure_existing_sidecars(path)


def _restore_owner_only_mode(path: Path, expected_fd: int) -> None:
    """Repair mode through the held, proven inode and verify the result."""
    _require_same_file(path, expected_fd)
    os.fchmod(expected_fd, stat.S_IRUSR | stat.S_IWUSR)
    file_stat = os.fstat(expected_fd)
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PermissionError(f"database mode could not be restored to 0600: {path}")
    _require_same_file(path, expected_fd)


def _verify_connection_target(
    dbapi_connection: Any, path: Path, expected_fd: int
) -> None:
    """Verify SQLite opened the held inode before any write-capable operation."""
    cursor = dbapi_connection.cursor()
    try:
        rows = cursor.execute("PRAGMA database_list").fetchall()
    finally:
        cursor.close()
    main_paths = [row[2] for row in rows if row[1] == "main"]
    if len(main_paths) != 1 or Path(main_paths[0]) != path:
        raise ValueError("SQLite opened an unexpected database path")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    actual_fd = _open_existing_file(path, flags)
    try:
        actual_stat = os.fstat(actual_fd)
        expected_stat = os.fstat(expected_fd)
        _require_single_link(actual_stat, path)
        _require_single_link(expected_stat, path)
        if (actual_stat.st_dev, actual_stat.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise ValueError(f"SQLite database target changed before opening: {path}")
    finally:
        os.close(actual_fd)


def _create_private_directories(directory: Path) -> None:
    """Create missing DB parents privately without changing existing parents."""
    missing: list[Path] = []
    cursor = directory
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent

    if not cursor.is_dir():
        raise NotADirectoryError(cursor)

    for path in reversed(missing):
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if not path.is_dir():
                raise
        else:
            os.chmod(path, 0o700)


def _require_private_directory(directory: Path) -> None:
    """Require a private, current-user-owned directory before SQLite writes."""
    directory_stat = directory.lstat()
    if stat.S_ISLNK(directory_stat.st_mode):
        raise PermissionError(
            f"database parent must not be a symbolic link: {directory}"
        )
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise NotADirectoryError(directory)

    current_uid = getattr(os, "geteuid", os.getuid)()
    if directory_stat.st_uid != current_uid:
        raise PermissionError(
            f"database parent must be owned by the current user: {directory}"
        )

    mode = stat.S_IMODE(directory_stat.st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(
            "database parent must grant no group or world permissions "
            f"(expected mode 0700 or stricter): {directory}"
        )


def _open_owner_only_file(path: Path, *, create: bool) -> int:
    """Open a regular file without following its final path component."""
    flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if create:
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            descriptor = _open_existing_file(path, flags)
    else:
        descriptor = _open_existing_file(path, flags)

    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValueError(f"database path is not a regular file: {path}")
        _require_same_file(path, descriptor)
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        _require_same_file(path, descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _open_existing_file(path: Path, flags: int) -> int:
    try:
        return os.open(path, flags)
    except OSError as error:
        if error.errno == ELOOP:
            raise ValueError(
                f"database path must not be a symbolic link: {path}"
            ) from error
        raise


def _require_same_file(path: Path, descriptor: int) -> None:
    path_stat = path.lstat()
    file_stat = os.fstat(descriptor)
    _require_single_link(path_stat, path)
    _require_single_link(file_stat, path)
    if stat.S_ISLNK(path_stat.st_mode) or (
        path_stat.st_dev,
        path_stat.st_ino,
    ) != (file_stat.st_dev, file_stat.st_ino):
        raise ValueError(f"database path changed while opening it: {path}")


def _require_single_link(file_stat: os.stat_result, path: Path) -> None:
    if file_stat.st_nlink != 1:
        raise ValueError(f"database file must have exactly one hard link: {path}")


def _secure_existing_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{path}{suffix}")
        try:
            sidecar_stat = sidecar.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(sidecar_stat.st_mode):
            raise ValueError(f"database sidecar must not be a symbolic link: {sidecar}")
        if not stat.S_ISREG(sidecar_stat.st_mode):
            raise ValueError(f"database sidecar is not a regular file: {sidecar}")
        _require_single_link(sidecar_stat, sidecar)
        try:
            descriptor = _open_owner_only_file(sidecar, create=False)
        except FileNotFoundError:
            continue
        else:
            os.close(descriptor)


def _sqlite_authorizer(
    action: int,
    argument_one: str | None,
    argument_two: str | None,
    database_name: str | None,
    trigger_name: str | None,
    *,
    allow_schema_changes: bool = False,
    allow_migration_history_insert: bool = False,
) -> int:
    """Prevent managed SQL from dismantling required SQLite invariants."""
    del database_name
    if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}:
        return sqlite3.SQLITE_DENY
    if action in _SCHEMA_ACTIONS and not allow_schema_changes:
        return sqlite3.SQLITE_DENY
    if (
        action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
        and (argument_one or "").casefold() in {"sqlite_master", "sqlite_schema"}
        and not allow_schema_changes
    ):
        return sqlite3.SQLITE_DENY
    if (
        action in {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
        and (argument_one or "").casefold() == "schema_migration"
    ):
        # Migration history is append-only even while schema authority is open.
        # Only a direct statement on the intended physical connection is
        # permitted; trigger side effects carry trigger_name and are denied.
        if not (
            action == sqlite3.SQLITE_INSERT
            and allow_migration_history_insert
            and trigger_name is None
        ):
            return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_PRAGMA and argument_two is not None:
        pragma = (argument_one or "").casefold()
        setting = argument_two.strip(" \t'\"").casefold()
        if pragma in {"foreign_keys", "recursive_triggers"} and setting not in {
            "1",
            "on",
            "true",
            "yes",
        }:
            return sqlite3.SQLITE_DENY
        if pragma == "journal_mode" and setting != "wal":
            return sqlite3.SQLITE_DENY
        if pragma == "synchronous" and setting not in {"2", "full"}:
            return sqlite3.SQLITE_DENY
        if pragma in {
            "writable_schema",
            "ignore_check_constraints",
            "trusted_schema",
        } and setting not in {"0", "off", "false", "no"}:
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _normalized_schema_sql(value: str) -> str:
    output: list[str] = []
    quote: str | None = None
    pending_space = False
    index = 0
    while index < len(value):
        character = value[index]
        if quote is not None:
            output.append(character)
            if character == quote:
                if index + 1 < len(value) and value[index + 1] == quote:
                    output.append(value[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if character in {"'", '"'}:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            quote = character
            output.append(character)
        elif character.isspace():
            pending_space = True
        else:
            if pending_space and output:
                output.append(" ")
            pending_space = False
            output.append(character)
        index += 1
    return "".join(output).strip()


def _normalized_table_sql(value: str) -> str:
    """Canonicalize table DDL without weakening quoted-literal semantics.

    Old M0 databases were emitted with both TEXT and VARCHAR declarations.
    SQLite assigns both TEXT affinity, so that one representation difference is
    intentionally normalized.  Everything else outside quotes, including
    COLLATE clauses and CHECK operators, remains part of the signed shape.
    """
    normalized = _normalized_schema_sql(value)
    output: list[str] = []
    outside: list[str] = []
    quote: str | None = None
    index = 0

    def flush_outside() -> None:
        if not outside:
            return
        fragment = "".join(outside)
        fragment = re.sub(
            r"\bVARCHAR(?:\s*\(\s*\d+\s*\))?",
            "TEXT",
            fragment,
            flags=re.IGNORECASE,
        )
        output.append(fragment.casefold())
        outside.clear()

    while index < len(normalized):
        character = normalized[index]
        if quote is None:
            if character in {"'", '"'}:
                flush_outside()
                quote = character
                output.append(character)
            else:
                outside.append(character)
        else:
            output.append(character)
            if character == quote:
                if index + 1 < len(normalized) and normalized[index + 1] == quote:
                    output.append(normalized[index + 1])
                    index += 1
                else:
                    quote = None
        index += 1
    flush_outside()
    return "".join(output)


def _database_has_no_user_schema(connection: Connection) -> bool:
    return (
        connection.exec_driver_sql(
            "SELECT COUNT(*) FROM sqlite_master "
            "WHERE type IN ('table','index','trigger','view') "
            "AND name NOT LIKE 'sqlite_%'"
        ).scalar_one()
        == 0
    )


def _required_migration_versions() -> frozenset[str]:
    versions = tuple(migration.VERSION for migration in _MIGRATION_MODULES)
    unique = frozenset(versions)
    if len(unique) != len(versions):
        raise RuntimeError("configured migration versions must be unique")
    return unique


def _schema_manifest(connection: Any) -> frozenset[tuple[str, str, str, str]]:
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','trigger','view') "
        "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL"
    ).fetchall()
    return frozenset(
        (
            kind,
            name,
            table,
            _normalized_table_sql(sql)
            if kind == "table"
            else _normalized_schema_sql(sql),
        )
        for kind, name, table, sql in rows
    )


def _check_expressions(sql: str) -> tuple[str, ...]:
    normalized = _normalized_schema_sql(sql)
    shadow = normalized.casefold()
    expressions: list[str] = []
    cursor = 0
    while (start := shadow.find("check (", cursor)) >= 0:
        expression_start = start + len("check ")
        depth = 0
        quote: str | None = None
        for index in range(expression_start, len(normalized)):
            character = normalized[index]
            if quote is not None:
                if character == quote:
                    quote = None
                continue
            if character in {"'", '"'}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    expressions.append(normalized[expression_start : index + 1])
                    cursor = index + 1
                    break
        else:
            expressions.append("[malformed-check]")
            break
    return tuple(sorted(expressions))


def _schema_structure(connection: Any) -> tuple[Any, ...]:
    tables = tuple(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    )
    structure: list[Any] = []
    for table in tables:
        quoted = table.replace('"', '""')
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        columns = tuple(
            (*row[:2], _sqlite_type_affinity(row[2]), *row[3:])
            for row in connection.execute(f'PRAGMA table_xinfo("{quoted}")').fetchall()
        )
        foreign_keys = tuple(
            connection.execute(f'PRAGMA foreign_key_list("{quoted}")').fetchall()
        )
        indexes = []
        for index_row in connection.execute(
            f'PRAGMA index_list("{quoted}")'
        ).fetchall():
            index_name = index_row[1]
            quoted_index = index_name.replace('"', '""')
            indexes.append(
                (
                    tuple(index_row[1:]),
                    tuple(
                        connection.execute(
                            f'PRAGMA index_xinfo("{quoted_index}")'
                        ).fetchall()
                    ),
                )
            )
        structure.append(
            (table, columns, foreign_keys, tuple(indexes), _check_expressions(sql))
        )
    return tuple(structure)


def _sqlite_type_affinity(declared_type: str) -> str:
    normalized = declared_type.casefold()
    if "int" in normalized:
        return "integer"
    if any(marker in normalized for marker in ("char", "clob", "text")):
        return "text"
    if not normalized or "blob" in normalized:
        return "blob"
    if any(marker in normalized for marker in ("real", "floa", "doub")):
        return "real"
    return "numeric"


@lru_cache(maxsize=1)
def _expected_schema() -> tuple[
    frozenset[tuple[str, str, str, str]],
    tuple[Any, ...],
    tuple[tuple[str, str, str], ...],
]:
    canonical = create_engine("sqlite://")
    try:
        with canonical.begin() as connection:
            Base.metadata.create_all(connection)
            connection.exec_driver_sql(
                "CREATE TABLE schema_migration "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            for migration in _MIGRATION_MODULES:
                migration.apply(connection)
            dbapi_connection = connection.connection.driver_connection
            if dbapi_connection is None:  # pragma: no cover - SQLite always supplies it
                raise RuntimeError("canonical SQLite connection is unavailable")
            invariant_ddl = tuple(
                (kind, name, _normalized_schema_sql(sql))
                for kind, name, sql in dbapi_connection.execute(
                    "SELECT type, name, sql FROM sqlite_master "
                    "WHERE type IN ('trigger','index') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY rowid"
                ).fetchall()
            )
            return (
                _schema_manifest(dbapi_connection),
                _schema_structure(dbapi_connection),
                invariant_ddl,
            )
    finally:
        canonical.dispose()


def _reconcile_invariant_objects(connection: Connection) -> None:
    """Canonicalize guards only when migration history proves work was pending."""
    _, _, expected_objects = _expected_schema()
    current = connection.exec_driver_sql(
        "SELECT type, name FROM sqlite_master "
        "WHERE type IN ('trigger','index') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for kind, name in current:
        quoted = name.replace('"', '""')
        connection.exec_driver_sql(f'DROP {kind.upper()} "{quoted}"')
    for _, _, sql in expected_objects:
        connection.exec_driver_sql(sql)


def _verify_schema_and_contents(connection: Connection) -> None:
    _verify_schema_and_contents_dbapi(connection.connection.driver_connection)


def _verify_schema_and_contents_dbapi(dbapi_connection: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        actual = _schema_manifest(dbapi_connection)
        expected, expected_structure, _ = _expected_schema()
        actual_structure = _schema_structure(dbapi_connection)
        actual_keys = {(kind, name) for kind, name, _, _ in actual}
        expected_keys = {(kind, name) for kind, name, _, _ in expected}
        exact_kinds = {"table", "index", "trigger"}
        exact_expected = {row for row in expected if row[0] in exact_kinds}
        exact_actual = {row for row in actual if row[0] in exact_kinds}
        if (
            actual_keys != expected_keys
            or exact_actual != exact_expected
            or actual_structure != expected_structure
        ):
            missing = sorted(expected_keys - actual_keys)
            unexpected = sorted(actual_keys - expected_keys)
            changed = sorted(
                {(kind, name) for kind, name, _, _ in exact_actual}
                & {(kind, name) for kind, name, _, _ in exact_expected}
                - {
                    (kind, name)
                    for kind, name, table, sql in exact_actual
                    if (kind, name, table, sql) in exact_expected
                }
            )
            actual_tables = {row[0]: row[1:] for row in actual_structure}
            expected_tables = {row[0]: row[1:] for row in expected_structure}
            changed_tables = sorted(
                name
                for name in actual_tables.keys() | expected_tables.keys()
                if actual_tables.get(name) != expected_tables.get(name)
            )
            raise RuntimeError(
                "SQLite schema does not match the required manifest "
                f"(missing={missing!r}, unexpected={unexpected!r}, "
                f"changed={changed!r}, changed_tables={changed_tables!r})"
            )
        integrity = cursor.execute("PRAGMA integrity_check").fetchall()
        if integrity != [("ok",)]:
            raise RuntimeError("SQLite integrity check failed")
        if cursor.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("SQLite foreign key check failed")
        expected_versions = _required_migration_versions()
        actual_versions = {
            row[0]
            for row in cursor.execute("SELECT version FROM schema_migration").fetchall()
        }
        if actual_versions != expected_versions:
            raise RuntimeError(
                "SQLite migration history does not match the required versions "
                f"(missing={sorted(expected_versions - actual_versions)!r}, "
                f"unexpected={sorted(actual_versions - expected_versions)!r})"
            )
    finally:
        cursor.close()


def get_journal_mode(connection: Connection) -> str:
    return str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
