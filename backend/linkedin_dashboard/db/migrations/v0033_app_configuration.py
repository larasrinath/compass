from typing import cast

from sqlalchemy import Connection, Table

from linkedin_dashboard.db.models import AppConfiguration

VERSION = "0033_app_configuration"
TRIGGER_NAMES = ()
INDEX_NAMES = ()


def apply(connection: Connection) -> None:
    cast(Table, AppConfiguration.__table__).create(connection, checkfirst=True)
