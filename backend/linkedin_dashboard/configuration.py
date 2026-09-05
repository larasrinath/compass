"""Public operational preferences, separate from credentials and role criteria."""

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from linkedin_dashboard.db.models import AppConfiguration


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    profile_concurrency: int = Field(default=2, ge=1, le=2)
    inter_call_delay_seconds: float = Field(default=3.0, ge=0, le=60)
    download_batch_limit: int = Field(default=1000, ge=1, le=1000)
    search_page_limit: int = Field(default=1000, ge=1, le=1000)
    automatic_downloads: bool = True
    automatic_pagination: bool = True
    busy_retry_seconds: float = Field(default=30.0, ge=1, le=300)
    timeout_retry_seconds: float = Field(default=0.0, ge=0, le=300)


def load_configuration(session: Session, *, delay: float = 3.0) -> Configuration:
    row = session.get(AppConfiguration, 1)
    return (
        Configuration.model_validate(row.values)
        if row
        else Configuration(inter_call_delay_seconds=delay)
    )
