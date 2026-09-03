from __future__ import annotations

from collections.abc import Iterator

import pytest
from linkedin_dashboard.db.session import Database


@pytest.fixture
def database(tmp_path) -> Iterator[Database]:
    instance = Database(tmp_path / "dashboard.db")
    instance.initialize()
    try:
        yield instance
    finally:
        instance.dispose()
