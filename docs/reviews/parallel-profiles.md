# Parallel profile downloads

Compass previously serialized every profile read twice: its durable worker had
one running-job slot, and the connector serialized access to its shared tab.
Increasing the 1,000-profile batch allowance did not increase throughput.

The updated connector exposes `get_person_profile_parallel`. Each call owns a
separate tab and extractor in the existing authenticated browser context. The
middleware admits at most two isolated profile calls, while shared-tab tools
remain exclusive. Waiting shared-tab tools get priority, and the cross-process
browser-profile lease remains in place. Tabs close after success, failure or
cancellation and inherit the configured page timeout.

Compass discovers this capability before enabling its second profile lane. An
older/unavailable connector keeps one lane. Each profile still gets its own job,
attempt, navigation reservation, raw response, extraction and score. Migration
0032 allows at most two running profile jobs from the same owner and prevents
shared-browser jobs from overlapping either lane. Existing data is retained.

Pacing occurs before a job is claimed. Claiming, recording external-call entry,
and entering the executor have no intervening yield, so another lane cannot
pause the queue while an unstarted claim is sleeping. Authentication and
rate-limit pauses stop new claims; reads already in flight can finish. Shutdown
waits for both lanes and retains process ownership until cancelled work exits.

## Verification

- Database migration and queue lifecycle suite, including bounded shutdown,
  immutable attempt history, cross-process ownership and navigation budgets.
- New tests for two overlapping profile jobs, exclusive search jobs, old-tool
  fallback, and authentication/rate-limit pauses during pacing. Paused work has
  zero attempts and charges until resume, then executes once.
- Connector tests cover two-slot middleware admission, writer priority,
  cancelled waiters, distinct tabs, configured timeouts and tab cleanup.
- Mutation check: changing the connector to one slot makes its concurrency test
  fail, demonstrating that the test detects the original bottleneck.
- Automatic-download, pagination, enrichment and MCP-client regression suites.
- Frontend tests cover the “Downloading 2 profiles” summary; production build,
  lint and backend type checks pass.
- Independent review identified the pause-during-delay race and missing page
  timeout; both were fixed and rechecked with no remaining blockers.

## Synthetic prompt

> Enable two concurrent profile downloads end to end using isolated connector
> tabs and durable per-profile jobs. Keep shared-browser actions exclusive,
> preserve rate limits, budget accounting, cancellation and shutdown semantics,
> fall back for older connectors, and update the queue status and help content.

Generated with Codex (GPT-6).

## Live verification — September 5, 2026

After the existing queue became idle, the API and patched connector were
restarted. Migration 0032 applied and saved-data counts were unchanged (64
candidates, 15 search runs and 39 profile-fetch records).

A bounded read-only check requested two previously saved profiles concurrently.
Both acquired connector slots within 0.04 seconds and completed in 16.07 seconds
total. Each returned its own expected profile URL plus nonempty `main_profile`
and `experience` sections. This confirms real overlapping reads and identity
isolation; it is not a large-batch speed benchmark. The last 30 earlier completed
profile jobs had a median duration of 11.0 seconds, before queue pacing.
