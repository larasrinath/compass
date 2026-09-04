# LinkedIn Dashboard

A local, single-operator sourcing workspace that consumes
[`linkedin-mcp-server`](https://github.com/stickerdaniel/linkedin-mcp-server)
over loopback streamable HTTP. The MCP server remains an unchanged sibling
service; this project never imports it, reads its browser profile, or manages
its process.

The implementation follows [PROJECT_PLAN.md](PROJECT_PLAN.md). M0 establishes
the loopback-only application shell, protected SQLite schema, append-only audit
log, and response privacy boundary. The M1 client boundary uses a fresh
FastMCP 3.4.4 streamable-HTTP session for each explicit operation, preserves
the full protocol response, and exposes typed wrappers for people search,
person/company profile retrieval, and the future manual-send transport. No API
or service invokes that send wrapper in M1, and `SEND_ENABLED` remains false.
The dashboard never starts, stops, imports, or authenticates to the sibling
server. M1's durable queue admits only the three read tools and `tools/list`,
claims at most one job across the database, writes each received envelope before
domain parsing, and marks orphaned work `interrupted` rather than replaying it.
The active worker holds an owner-only `flock` sidecar beside the database for
its full lifetime and fences every write with its durable claim token, so a
standby process cannot reclaim a live call as crash residue.
Timeout and browser-busy read jobs may make one explicit second attempt; no
message operation is admitted to this queue. Rate-limited profile work is held
until operator resume and continues only from the first missing section.
Decoded MCP envelopes are rejected above 16 MiB; this post-decode guard does
not cap the FastMCP SDK's transient transport-parsing memory for that one call.

M2 adds a versioned role brief and an unscored discovery workspace. Skills,
titles, and industries retain per-term aliases; protected sourcing criteria are
rejected before a new brief version is written. Each explicit search becomes
one durable `search_people` job, stores its complete MCP envelope before
parsing, and appends only `kind="person"` references to the session candidate
pool. Usernames are normalized and case-insensitively deduplicated while every
producing search remains linked as provenance. The UI shows the shared
15-reference cap, raw search text, reference-kind counts, sanitized partial
errors, and the serialized queue through SSE. Company URN lookup is also a
queued read. Discovery does not retrieve profiles, rank candidates, or expose
shortlist, drafting, or message controls.

M3 adds operator-triggered, staged profile enrichment. Stage 1 retrieves the
implicit main profile and experience; Stage 2 accepts at most three additional
sections in the MCP server's canonical order. Every call remains a durable,
read-only queue job. Its complete committed tool response is stored before any
section, reference, error, or parsed field is projected. A rate-limited result
is never replayed: the operator may explicitly resume only the missing suffix,
with the parent and continuation fetches linked in immutable history. Unknown
sections fail loudly and are not retried.
Admission reserves each job's complete navigation cost in the same transaction
as the job and fetch history; batch admission is all-or-nothing. Claiming moves
that reservation into `nav_used`. An interrupted delay refunds it only when the
durable attempt phase proves the executor was never entered; once entered, the
charge is retained conservatively even if no response arrives.

The six local parsers cover main profile, experience, skills, education,
projects, and certifications. Parsed values are exact substrings of an
immutable raw section and carry zero-based, half-open Unicode code-point spans
plus the exact section-history identifier. `NullProvider` is the only LLM
provider through M5; proposed spans from any future provider must pass exact
substring verification before becoming evidence. The candidate detail view
shows parsed fields beside the source sections and highlights verified spans.
For provenance responses, sensitive diagnostic runs are replaced by one BMP
mask character per original code point so preceding offsets remain stable. If
redaction overlaps evidence, the API withholds the value and offsets and the UI
shows a neutral “Provenance withheld” state. The frontend slices spans with
`Array.from`, preserving astral-character alignment, and renders source text
only as React text nodes.

Profile URNs are write-once routing hints, never scoring inputs. An exact,
fetch-bound immutable observation must be committed before the compare-and-set
that accepts the first non-null URN. Routing requires that accepted observation
and no divergent observation; the column alone never authorizes routing. A
later conflict or independently verified returned-profile URL
mismatch permanently quarantines routing while preserving an immutable
observation and audit record. Database attestations bind every projected
section, error, reference, and parsed span to the exact committed MCP envelope.
The eight-profile parser corpus under `tests/fixtures/profile_parsing` is
explicitly synthetic representative data. It provides a 16-field regression
denominator, not real-profile acceptance evidence; the ≥90% title/company
metric on manually annotated experience blocks from at least two consented,
non-private real profiles remains the sole M3 acceptance blocker. The operator
approved reducing that real-profile minimum from eight to two on 2026-09-03
for this one-time local activity; the ≥90% quality threshold did not change.

Sanitized live QA on build
`e3240dc42f0158b6f5a7dfb9cbe0cb2eaf42eaf3` passed 2/2 Stage-1 queue jobs,
stored 4/4 required sections verbatim, passed URN-if-present handling 2/2, and
recorded zero forbidden send, search, or draft operations. The evidence
artifact SHA-256 is
`284ee3635b3c2c28a67fe77350ab9c3e6dc9ed92f6ee76f7d0db925e5add5b61`;
profile names and raw data are intentionally omitted. M3 is not yet accepted:
the reported 89.81% was parser-output completeness, not manually annotated
accuracy, so the revised ≥90%-across-≥2-profiles gate still requires manual
annotation.

## Prerequisites

- Python 3.12.4–3.14 and [uv](https://docs.astral.sh/uv/)
- Node.js and npm
- A separately running `linkedin-mcp-server` when MCP-backed milestones begin

## Development

```bash
uv sync --group dev
uv run -m linkedin_dashboard
```

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

The API listens on `http://127.0.0.1:8787`; Vite listens on
`http://127.0.0.1:5173`. Vite derives its listener from the validated
`FRONTEND_HOST` and `FRONTEND_PORT` settings and its `/api` proxy from the
validated backend `HOST` and `PORT` settings. IPv6 authorities are bracketed.
Both processes reject non-loopback host configuration. Start the API only
through `uv run -m linkedin_dashboard`; there is intentionally no importable
module-level or zero-argument ASGI application that can be bound with an unsafe
Uvicorn CLI override. The API also verifies its real listening socket against
the configured host and port before initializing its database. Queue state is
available at `/api/jobs` and `/api/queue/status`; the frontend can consume
sanitized job/progress events from `/api/events`. A probe at `/api/mcp/status`
is itself serialized through the queue and returns only tool names and a safe
error class, never the configured MCP URL.
All three network settings (`HOST`, `FRONTEND_HOST`, and the host in `MCP_URL`)
must use numeric loopback literals. Hostnames such as `localhost` are rejected
so startup and runtime checks never depend on DNS; equivalent IPv6 loopback
spellings are canonicalized to `::1`. Scoped and IPv4-mapped IPv6 addresses are
rejected consistently by the API and Vite processes.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
cd frontend && npm test && npm run lint && npm run build
```

Copy `.env.example` to `.env` only when overriding defaults. The application
database is created with mode `0600` at `~/.linkedin-dashboard/session.db`.
Its parent directory is created with mode `0700`. A custom existing `DB_PATH`
parent must already be owned by the current user and grant no group or world
permissions; startup rejects an unsafe parent instead of changing its mode.
Initialization holds and verifies the database inode before SQLite performs a
write-capable operation, and schema migrations plus their version records are
committed atomically. Database and SQLite sidecar files with more than one hard
link are rejected before permission or SQLite operations and are revalidated,
along with the configured path and held inode, on every pooled connection
checkout. Recursive SQLite triggers are enforced so replacement statements
cannot bypass append-only audit and send-history guards. Send-attempt history
can be removed only as part of a full-session purge. The database also enforces
that `SENDING` is the only unfinished state and every outcome state is finished,
that confirmations and attempts agree with their referenced draft, and that a
referenced draft can be changed only by creating a new version. The final API
privacy boundary redacts filesystem diagnostics, credential-bearing URLs,
sensitive query parameters and labeled credentials from bodies, streams and
response headers.
Through M5, `LLM_PROVIDER` is locked to the literal value `null`.
