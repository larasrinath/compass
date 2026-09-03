from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from linkedin_dashboard.correlation import current_correlation_id
from linkedin_dashboard.db.models import Job
from linkedin_dashboard.queue.worker import DurableJobQueue, QueueEvent

router = APIRouter(tags=["jobs"])


def get_queue(request: Request) -> DurableJobQueue:
    return request.app.state.job_queue


class JobRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    kind: str
    state: str
    attempts: int
    max_attempts: int
    queued_at: str
    started_at: str | None
    finished_at: str | None
    error_class: str | None
    correlation_id: str
    position: int | None = None
    depth: int = 0

    @classmethod
    def safe(
        cls, job: Job, *, position: int | None = None, depth: int = 0
    ) -> JobRecord:
        return cls(
            id=job.id,
            session_id=job.session_id,
            kind=job.kind,
            state=job.state,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            queued_at=job.queued_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            error_class=job.error,
            correlation_id=job.correlation_id,
            position=position,
            depth=depth,
        )


class QueueStatus(BaseModel):
    state: str
    pause_reason: str | None
    resume_at: str | None
    counts: dict[str, int]
    jobs: list[dict[str, Any]]


class MCPStatus(BaseModel):
    reachable: bool
    tools: list[str]
    last_error_class: str | None
    correlation_id: str


@router.get("/jobs", response_model=list[JobRecord])
def list_jobs(
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[JobRecord]:
    records: list[JobRecord] = []
    for job in queue.list_jobs(limit=limit):
        position, depth = queue.queue_position(job.id)
        records.append(JobRecord.safe(job, position=position, depth=depth))
    return records


@router.post("/jobs/{job_id}/cancel", response_model=JobRecord)
async def cancel_job(
    job_id: str,
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
) -> JobRecord:
    if not await queue.cancel(job_id):
        raise HTTPException(409, "Only a queued job can be cancelled")
    job = next((row for row in queue.list_jobs(limit=200) if row.id == job_id), None)
    if job is None:  # pragma: no cover - guarded by successful CAS
        raise HTTPException(404, "Job not found")
    position, depth = queue.queue_position(job.id)
    return JobRecord.safe(job, position=position, depth=depth)


@router.get("/queue/status", response_model=QueueStatus)
def queue_status(
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
) -> QueueStatus:
    return QueueStatus.model_validate(queue.snapshot())


@router.post("/queue/resume", response_model=QueueStatus)
async def resume_queue(
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
) -> QueueStatus:
    await queue.resume()
    return QueueStatus.model_validate(queue.snapshot())


@router.get("/mcp/status", response_model=MCPStatus)
async def mcp_status(
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
) -> MCPStatus:
    correlation_id = current_correlation_id()
    job = await queue.probe_status(correlation_id)
    tools: list[str] = []
    if job.state == "done":
        attempts = queue.attempts_for(job.id)
        raw = attempts[-1].raw_response if attempts else None
        if isinstance(raw, dict) and isinstance(raw.get("tools"), list):
            tools = sorted(
                str(tool["name"])
                for tool in raw["tools"]
                if isinstance(tool, dict) and isinstance(tool.get("name"), str)
            )
    return MCPStatus(
        reachable=job.state == "done",
        tools=tools,
        last_error_class=job.error,
        correlation_id=correlation_id,
    )


def _encode_event(event: QueueEvent) -> bytes:
    payload = json.dumps(event.data, separators=(",", ":"), sort_keys=True)
    return f"event: {event.event}\ndata: {payload}\n\n".encode()


@router.get("/events")
async def events(
    queue: Annotated[DurableJobQueue, Depends(get_queue)],
) -> StreamingResponse:
    async def stream() -> Any:
        async with queue.events.subscribe() as subscriber:
            yield _encode_event(QueueEvent("snapshot", queue.snapshot()))
            while True:
                yield _encode_event(await subscriber.get())

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
