from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker

from linkedin_dashboard.db.migrations import v0001_constraints
from linkedin_dashboard.db.models import Base


class Database:
    """SQLite ownership boundary for the local dashboard."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.engine = _create_engine(self.path)
        self.sessions = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )

    def initialize(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE IF NOT EXISTS schema_migration "
                "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = connection.execute(
                text("SELECT 1 FROM schema_migration WHERE version = :version"),
                {"version": v0001_constraints.VERSION},
            ).scalar_one_or_none()
            if applied is None:
                v0001_constraints.apply(connection)
                connection.execute(
                    text(
                        "INSERT INTO schema_migration(version, applied_at) "
                        "VALUES (:version, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
                    ),
                    {"version": v0001_constraints.VERSION},
                )
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)

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

    return engine


def get_journal_mode(connection: Connection) -> str:
    return str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
