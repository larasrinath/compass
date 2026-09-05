"""Durable, bounded people-search pagination: one queue job per results page."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select

from linkedin_dashboard.db.models import (
    AuditLog,
    CandidateSource,
    Job,
    SearchDownload,
    SearchPage,
    SearchPagination,
    SearchRun,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.queue.jobs import JobKind
from linkedin_dashboard.queue.worker import DurableJobQueue
from linkedin_dashboard.services.downloads import remaining_batch_downloads


class PaginationChanged(RuntimeError):
    pass


class SearchPaginationService:
    def __init__(self, database: Database, queue: DurableJobQueue):
        self.database = database
        self.queue = queue

    async def dispatch_pending(self) -> None:
        with self.database.sessions() as db:
            roots = list(
                db.scalars(
                    select(SearchPagination.root_run_id).where(
                        SearchPagination.stop_reason.is_(None)
                    )
                )
            )
        for root_id in roots:
            await self._advance(root_id)

    async def stop(self, root_id: str) -> None:
        with self.database.sessions.begin() as db:
            group = db.get(SearchPagination, root_id)
            if group is None:
                raise LookupError("paginated search does not exist")
            group.stop_reason = group.stop_reason or "stopped"
            jobs = list(
                db.scalars(
                    select(SearchRun.job_id)
                    .join(SearchPage, SearchPage.run_id == SearchRun.id)
                    .where(SearchPage.root_run_id == root_id)
                )
            )
        for job_id in jobs:
            # An in-flight page finishes normally; no later page will be admitted.
            await self.queue.cancel(job_id)

    async def _advance(self, root_id: str) -> None:
        with self.database.sessions() as db:
            group = db.get(SearchPagination, root_id)
            root = db.get(SearchRun, root_id)
            if group is None or group.stop_reason or root is None:
                return
            pages = list(
                db.scalars(
                    select(SearchPage)
                    .where(SearchPage.root_run_id == root_id)
                    .order_by(SearchPage.page_number)
                )
            )
            if not pages:
                raise RuntimeError("paginated search has no first page")
            last = pages[-1]
            run = db.get(SearchRun, last.run_id)
            if run is None:
                raise RuntimeError("search page run is missing")
            job = db.get(Job, run.job_id)
            if job is None or job.state in {"queued", "running", "pending"}:
                return
            if run.processed_at is None:
                raise RuntimeError("search projection is still pending")
            memberships = list(
                db.execute(
                    select(
                        CandidateSource.candidate_id, CandidateSource.search_run_id
                    ).where(
                        CandidateSource.search_run_id.in_(
                            [page.run_id for page in pages]
                        )
                    )
                )
            )
            previous = {
                candidate for candidate, source in memberships if source != last.run_id
            }
            current = {
                candidate for candidate, source in memberships if source == last.run_id
            }
            reason = None
            if job.state in {"cancelled", "interrupted"}:
                reason = "stopped"
            elif run.status == "rate_limited":
                reason = "rate_limited"
            elif run.status not in {"ok", "partial"}:
                reason = "failed"
            elif remaining_batch_downloads(db, root_id, group.profile_limit) == 0:
                reason = "download_limit"
            elif not current:
                reason = "exhausted"
            elif not current.difference(previous):
                reason = "repeated_page"
            elif last.page_number >= group.page_limit:
                reason = "page_limit"
            elif run.status == "partial":
                reason = "partial"
            session_id, brief_id = root.session_id, root.brief_id
            filters = dict(
                keywords=root.keywords,
                location=root.location,
                network=root.network,
                current_company=root.current_company,
            )
            next_page = last.page_number + 1
            auto = db.get(SearchDownload, root_id) is not None

        if reason:
            with self.database.sessions.begin() as db:
                group = db.get(SearchPagination, root_id)
                if group and not group.stop_reason:
                    group.stop_reason = reason
            return

        run_id = str(uuid4())

        def related(db: Any, job: Job) -> None:
            group = db.get(SearchPagination, root_id)
            latest = db.scalar(
                select(func.max(SearchPage.page_number)).where(
                    SearchPage.root_run_id == root_id
                )
            )
            if group is None or group.stop_reason or latest != next_page - 1:
                raise PaginationChanged("search pagination changed during admission")
            db.add(
                SearchRun(
                    id=run_id,
                    session_id=session_id,
                    brief_id=brief_id,
                    job_id=job.id,
                    created_at=job.queued_at,
                    **filters,
                    status="queued",
                    reference_count=0,
                    person_reference_count=0,
                )
            )
            db.flush()
            db.add(
                SearchPage(run_id=run_id, root_run_id=root_id, page_number=next_page)
            )
            if auto:
                db.add(
                    SearchDownload(
                        search_run_id=run_id,
                        profile_limit=group.profile_limit,
                        requested_at=job.queued_at,
                        queued_count=0,
                    )
                )
            db.add(
                AuditLog(
                    session_id=session_id,
                    at=datetime.now(UTC).isoformat(),
                    actor="system",
                    action="search.page_queued",
                    subject_type="search_run",
                    subject_id=root_id,
                    correlation_id=job.correlation_id,
                    detail={"page": next_page, "page_run_id": run_id},
                )
            )

        try:
            await self.queue.enqueue(
                session_id,
                JobKind.SEARCH_PEOPLE,
                {**filters, "page": next_page, "search_run_id": run_id},
                related_factory=related,
                authorize_search_page=True,
            )
        except PaginationChanged:
            return
