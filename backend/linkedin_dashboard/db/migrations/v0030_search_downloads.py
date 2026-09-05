from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import SearchDownload

VERSION = "0030_search_downloads"
TRIGGER_NAMES = ()
INDEX_NAMES = ()


def apply(connection: Connection) -> None:
    # No intents are created for historical searches during upgrade.
    cast(Table, SearchDownload.__table__).create(connection, checkfirst=True)
