from fastapi import APIRouter, Request

from linkedin_dashboard.configuration import Configuration, load_configuration
from linkedin_dashboard.db.models import AppConfiguration

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=Configuration)
def get_configuration(request: Request) -> Configuration:
    with request.app.state.database.sessions() as session:
        return load_configuration(
            session, delay=request.app.state.settings.inter_call_delay_seconds
        )


@router.put("/settings", response_model=Configuration)
async def save_configuration(payload: Configuration, request: Request) -> Configuration:
    with request.app.state.database.sessions.begin() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = session.get(AppConfiguration, 1)
        if row is None:
            session.add(AppConfiguration(id=1, values=payload.model_dump()))
        else:
            row.values = payload.model_dump()
    # Wake the existing scheduler without starting or resuming a paused connector.
    request.app.state.job_queue.configuration_changed()
    return payload
