# Automatic downloads after discovery

With automatic downloads enabled in Settings, running a search authorizes one initial profile-and-experience download for each
newly discovered person in that search, up to the saved batch limit (default 1,000). It does not authorize extra
sections or repeat retrievals of already requested profiles. The bundled connector patch follows additional result pages. The 1,000-request
allowance is shared by all pages of one search batch, not by the entire workspace.

## Durable flow

1. Search admission saves a `search_download` intent in the same transaction as
   its search run and queue job. Existing searches receive no intent on migration.
2. The search response is committed and projected into candidates and source
   memberships. Network F/S/O, location and company filters remain on the search.
3. Before claiming its next job, the worker dispatches pending intents. It selects
   only that run's candidates, excluding anyone with a previous profile request.
4. Initial jobs, fetch records, read reservations, an audit entry and the dispatch
   marker commit together. Repeating the action cannot duplicate that batch.
5. The worker runs up to the configured profile concurrency (1–2), capped by
   connector capacity. Other operations remain exclusive. Existing cooldown, retry,
   cancellation and pause/resume behavior applies. Retrieved evidence triggers
   the existing local scoring service.

Initial batch authorization expands the session's page-read budget only as needed
for the actual selected profiles, two reads each. The queue still reserves every
read atomically; retries and additional sections remain budgeted. Admission emits
one canonical snapshot instead of a per-profile burst of queue-position queries.

If local search projection fails after capture, the intent remains pending until
reconciliation succeeds. Recovery never repeats the completed search. Queued
profile jobs survive restart, while an interrupted active request retains the
queue's existing explicit recovery behavior.

## Interface and compatibility

- Find candidates omits `automatic_downloads` and `paginate`; the backend resolves
  saved Settings defaults. Both default to true on a fresh installation.
- API clients requiring discovery only must explicitly send
  `automatic_downloads: false`. Send `paginate: false` for one page.
- Download and page limits are captured when a batch is created. Changing Settings
  does not resize existing batches. See [Settings](../settings.md).
- Select an older run under Results from, then Download remaining profiles to
  request its initial batch. The endpoint is idempotent.
- Waiting/downloading counts accompany the pool. Numeric scores appear only when
  evidence supports calculation; unscored profiles remain at the end of ranking.
- How it works now demonstrates automatic download progression. README screenshots
  use fictional data and intercepted APIs; no live searches were used for tests.

## Verification

Tests cover all four network-filter choices, source membership, deduplication,
scoring after retrieval, explicit historical catch-up, failure without endless
requeueing, paused restart, local projection recovery, and 1,000-profile admission
with 2,000 reserved page reads. The 1,001-profile batch is rejected before work.
Queue and database regression checks retain the existing concurrency and budget
invariants. Frontend checks cover the search payload and run-scoped catch-up.

## Paginated discovery

The first-page limitation described above is superseded by the bundled connector patch and durable page jobs. See [pagination](search-pagination.md) for the current behavior and verification.
