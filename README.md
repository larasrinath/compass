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
Timeout and browser-busy read jobs may make one explicit second attempt; no
message operation is admitted to this queue. Rate-limited profile work is held
until operator resume and continues only from the first missing section.
Decoded MCP envelopes are rejected above 16 MiB; this post-decode guard does
not cap the FastMCP SDK's transient transport-parsing memory for that one call.

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
