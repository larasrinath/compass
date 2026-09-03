# LinkedIn Dashboard

A local, single-operator sourcing workspace that consumes
[`linkedin-mcp-server`](https://github.com/stickerdaniel/linkedin-mcp-server)
over loopback streamable HTTP. The MCP server remains an unchanged sibling
service; this project never imports it, reads its browser profile, or manages
its process.

The implementation follows [PROJECT_PLAN.md](PROJECT_PLAN.md). M0 establishes
the loopback-only application shell, protected SQLite schema, append-only audit
log, and response privacy boundary.

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
`http://127.0.0.1:5173` and proxies `/api` to the backend. Both reject
non-loopback host configuration.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
cd frontend && npm run lint && npm run build
```

Copy `.env.example` to `.env` only when overriding defaults. The application
database is created with mode `0600` at `~/.linkedin-dashboard/session.db`.

