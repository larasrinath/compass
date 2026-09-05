# Paginated discovery and automatic profile downloads

**Run search** queues a logical search with `paginate: true` and
`automatic_downloads: true`. Each result page preserves the original brief,
keywords, location, connection filter and company filter. The connector patch is
bundled under `integrations/linkedin-mcp-server` and required for pagination.

## Execution and limits

- One people-search page is one durable job and one reserved navigation.
- Newly discovered profiles queue overview and experience downloads. Those jobs
  run before the next page; each completed profile is scored with saved evidence.
- Each logical search authorizes up to 1,000 new profile-download requests across
  all its result pages. Saved candidates and search history have no lifetime count
  cap. Existing requests are reused and do not consume the new batch allowance;
  failed requests are not retried automatically.
- Discovery ends on an empty page, repeated identities with no new people, an
  incomplete/failed/rate-limited result, 1,000 new download requests, or 1,000 page requests.
  LinkedIn can return fewer results or pages than requested.
- **Stop discovery** records a durable stop and cancels queued search pages.
  An in-flight page and already queued profile downloads can finish.
- Existing delay, single-worker execution and rate-limit cooldowns remain active.
  Page admission, including the first page of a new batch, expands the navigation
  budget only for the next explicit read.
  This is a batch ceiling, not 1,000 concurrent requests.

Migration 0031 stores a logical root and page memberships. Admission of the next
page, its search record and its download intent happens in the same transaction
as the queue job. A restart resumes after the last projected page. Original
responses and physical page provenance remain accessible in Search history;
Saved searches displays one entry per logical search. Catch-up downloads include
all pages of a selected search. Old searches are never paginated on upgrade.

## Verification

- Mocked multi-page search → download → scoring passes for F, S, O and unrestricted
  searches, preserving company/location filters and deduplicating repeated people.
- Empty and repeated-page termination, operator stop, restart without replay,
  all-page catch-up and a library growing from 1,000 to 1,026 candidates are covered.
- A multi-page batch test verifies that saved profiles do not consume the allowance,
  a boundary page queues only its remaining allowance, and a later batch can download
  the leftovers. Admission rechecks the shared allowance inside its transaction.
- The existing 1,000-profile batch test verifies 1,000 durable jobs and exactly
  2,000 reserved profile reads, with no concurrent execution.
- Connector: 28 focused tests, including FastMCP dispatch and strict page validation.
  Mutations that hard-code page 1, break forwarding, or admit page 0 fail tests.
- Live bounded check on September 5, 2026: Anaplan / India / F+S returned 15
  people on page 1 and 15 on page 2, including 11 additional identities. No live
  profile downloads were triggered by this check. Counts are evidence for these
  two pages only, not a claim of exhaustive coverage.
- Existing frontend and browser regressions pass; the stop control has a rendered
  interaction test. README screenshots use fictional data.

To discover outside existing connections, choose 3rd-degree and beyond (O), or
an unrestricted network search. Only profiles LinkedIn exposes to the signed-in
account can be discovered. Network distance is a discovery filter, not a score.

## Existing workspaces

No candidate or search record is removed by this change. Old searches that already
stopped remain stopped; run a new search to discover and download more people.
The previous workspace-wide limits of 1,000 candidates and 200 searches are removed.
The 30-profile display pagination remains unchanged.
