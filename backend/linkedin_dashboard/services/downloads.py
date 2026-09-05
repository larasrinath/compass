"""Search-scoped download intent; the existing durable queue owns execution."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from linkedin_dashboard.db.models import (
    AuditLog,
    Candidate,
    CandidateSource,
    Job,
    ProfileFetch,
    SearchDownload,
    SearchRun,
)
from linkedin_dashboard.db.session import Database
from linkedin_dashboard.services.enrichment import EnrichmentService


class SearchDownloadService:
    def __init__(self, database: Database, enrichment: EnrichmentService) -> None:
        self.database = database
        self.enrichment = enrichment

    async def request(self, run_id: str) -> None:
        with self.database.sessions.begin() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            run = db.get(SearchRun, run_id)
            if run is None:
                raise LookupError("search does not exist")
            if run.processed_at is None or run.status not in {
                "ok",
                "partial",
                "rate_limited",
            }:
                raise ValueError("finish a search before downloading its results")
            if db.get(SearchDownload, run_id) is None:
                db.add(
                    SearchDownload(
                        search_run_id=run_id,
                        profile_limit=1000,
                        requested_at=datetime.now(UTC).isoformat(),
                        queued_count=0,
                    )
                )
        await self.dispatch_pending()

    async def dispatch_pending(self) -> None:
        with self.database.sessions() as db:
            run_ids = list(
                db.scalars(
                    select(SearchDownload.search_run_id)
                    .where(SearchDownload.dispatched_at.is_(None))
                    .order_by(SearchDownload.requested_at, SearchDownload.search_run_id)
                )
            )
        for run_id in run_ids:
            await self._dispatch(run_id)

    async def _dispatch(self, run_id: str) -> None:
        with self.database.sessions() as db:
            intent = db.get(SearchDownload, run_id)
            run = db.get(SearchRun, run_id)
            if intent is None or intent.dispatched_at or run is None:
                return
            job = db.get(Job, run.job_id)
            if job is None or job.state in {"pending", "queued", "running"}:
                return
            if run.processed_at is None:
                # A committed search response may still need local recovery.
                # Keep the intent pending until the queue reconciles that response.
                raise RuntimeError("search projection is still pending")
            session_id = run.session_id
            candidate_ids = []
            if (
                run.processed_at
                and run.status in {"ok", "partial", "rate_limited"}
                and job.state not in {"cancelled", "interrupted"}
            ):
                # Membership comes from this search, including its original network
                # filter. Never sweep the session's unrelated historical candidates.
                candidate_ids = list(
                    db.scalars(
                        select(Candidate.id)
                        .join(
                            CandidateSource,
                            CandidateSource.candidate_id == Candidate.id,
                        )
                        .where(
                            CandidateSource.search_run_id == run_id,
                            Candidate.session_id == session_id,
                            Candidate.stage == "discovered",
                            ~select(ProfileFetch.id)
                            .where(ProfileFetch.candidate_id == Candidate.id)
                            .exists(),
                        )
                        .order_by(Candidate.first_seen_at, Candidate.id)
                        .limit(intent.profile_limit)
                    )
                )

        def dispatched(db: Any) -> None:
            intent = db.get(SearchDownload, run_id)
            if intent is None or intent.dispatched_at:
                raise IntegrityError(
                    "download already dispatched", {}, ValueError("already dispatched")
                )
            intent.dispatched_at = datetime.now(UTC).isoformat()
            intent.queued_count = len(candidate_ids)
            db.add(
                AuditLog(
                    session_id=session_id,
                    at=intent.dispatched_at,
                    actor="system",
                    action="search.profiles_queued",
                    subject_type="search_run",
                    subject_id=run_id,
                    correlation_id=run_id,
                    detail={
                        "profile_count": len(candidate_ids),
                        "profile_limit": intent.profile_limit,
                        "authorized_page_reads": 2 * len(candidate_ids),
                    },
                )
            )

        try:
            if candidate_ids:
                await self.enrichment.enqueue_batch(
                    candidate_ids,
                    ["experience"],
                    transaction_callback=dispatched,
                    authorize_profile_reads=True,
                    new_only=True,
                )
            else:
                await self.enrichment.queue.enqueue_many(
                    session_id,
                    [],
                    transaction_callback=dispatched,
                )
        except RuntimeError as error:
            # A concurrent manual request won. Re-select on the next worker turn;
            # the transaction rolled back both jobs and the dispatch marker.
            if "active fetch" not in str(error):
                raise
