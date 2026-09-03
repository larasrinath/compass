from __future__ import annotations

import os
import stat
from collections.abc import Callable
from errno import ELOOP
from pathlib import Path
from threading import Lock
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from linkedin_dashboard.db.migrations import (
    v0001_constraints,
    v0002_integrity,
    v0003_send_invariants,
    v0004_audit_cascade,
    v0005_send_history,
)
from linkedin_dashboard.db.models import Base
from linkedin_dashboard.settings import normalize_database_path


class Database:
    """SQLite ownership boundary for the local dashboard."""

    def __init__(self, path: Path) -> None:
        self.path = normalize_database_path(path)
        self._database_fd: int | None = None
        self._initialize_lock = Lock()
        self._initialized = False
        self.engine = _create_engine(self.path, lambda: self._database_fd)
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
            _create_private_directories(self.path.parent)
            _require_private_directory(self.path.parent)
            database_fd = _open_owner_only_file(self.path, create=True)
            self._database_fd = database_fd
            try:
                with self.engine.connect() as connection:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    try:
                        self._initialize_schema(connection)
                    except BaseException:
                        connection.rollback()
                        raise
                    else:
                        connection.commit()
                _require_same_file(self.path, database_fd)
                _secure_existing_sidecars(self.path)
                self._initialized = True
            except BaseException:
                self.engine.dispose()
                os.close(database_fd)
                self._database_fd = None
                raise

    def _initialize_schema(self, connection: Connection) -> None:
        Base.metadata.create_all(connection)
        connection.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS schema_migration "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for version, apply in (
            (v0001_constraints.VERSION, v0001_constraints.apply),
            (v0002_integrity.VERSION, v0002_integrity.apply),
            (v0003_send_invariants.VERSION, v0003_send_invariants.apply),
            (v0004_audit_cascade.VERSION, v0004_audit_cascade.apply),
            (v0005_send_history.VERSION, v0005_send_history.apply),
        ):
            applied = connection.execute(
                text("SELECT 1 FROM schema_migration WHERE version = :version"),
                {"version": version},
            ).scalar_one_or_none()
            if applied is None:
                apply(connection)
                connection.execute(
                    text(
                        "INSERT INTO schema_migration(version, applied_at) "
                        "VALUES (:version, "
                        "strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
                    ),
                    {"version": version},
                )

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


def _create_engine(path: Path, expected_fd: Callable[[], int | None]) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(
        dbapi_connection: Any, connection_record: Any
    ) -> None:  # pragma: no cover - SQLAlchemy callback signature
        del connection_record
        database_fd = expected_fd()
        if database_fd is None:
            raise RuntimeError("database connections require secure initialization")
        _verify_connection_target(dbapi_connection, path, database_fd)
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
        _secure_existing_sidecars(path)

    return engine


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
    flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
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
    if stat.S_ISLNK(path_stat.st_mode) or (
        path_stat.st_dev,
        path_stat.st_ino,
    ) != (file_stat.st_dev, file_stat.st_ino):
        raise ValueError(f"database path changed while opening it: {path}")


def _secure_existing_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        try:
            descriptor = _open_owner_only_file(sidecar, create=False)
        except FileNotFoundError:
            continue
        else:
            os.close(descriptor)


def get_journal_mode(connection: Connection) -> str:
    return str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
