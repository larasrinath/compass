from __future__ import annotations

import os
import stat
from errno import ELOOP
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from linkedin_dashboard.db.migrations import (
    v0001_constraints,
    v0002_integrity,
    v0003_send_invariants,
)
from linkedin_dashboard.db.models import Base
from linkedin_dashboard.settings import normalize_database_path


class Database:
    """SQLite ownership boundary for the local dashboard."""

    def __init__(self, path: Path) -> None:
        self.path = normalize_database_path(path)
        self.engine = _create_engine(self.path)
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def initialize(self) -> None:
        _create_private_directories(self.path.parent)
        database_fd = _open_owner_only_file(self.path, create=True)
        try:
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS schema_migration "
                    "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                for version, apply in (
                    (v0001_constraints.VERSION, v0001_constraints.apply),
                    (v0002_integrity.VERSION, v0002_integrity.apply),
                    (v0003_send_invariants.VERSION, v0003_send_invariants.apply),
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
            _require_same_file(self.path, database_fd)
            _secure_existing_sidecars(self.path)
        finally:
            os.close(database_fd)

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
        self.engine.dispose()


def _create_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(
        dbapi_connection: Any, connection_record: Any
    ) -> None:  # pragma: no cover - SQLAlchemy callback signature
        del connection_record
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()
        _secure_existing_sidecars(path)

    return engine


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
