from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import SearchPage, SearchPagination

VERSION = "0031_search_pagination"
TRIGGER_NAMES = ()
INDEX_NAMES = ()


def apply(connection: Connection) -> None:
    for model in (SearchPagination, SearchPage):
        cast(Table, model.__table__).create(connection, checkfirst=True)
