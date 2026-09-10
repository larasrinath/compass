# Development setup

Normal startup is `./compass` on macOS/Linux or `.\compass` in Windows PowerShell,
from the repository root. These separate commands
are for contributors who need hot reload or a manually managed connector.

## Services

Requires Python 3.12.4–3.14, uv, and Node supported by Vite (20.19+ on 20.x,
22.12+ on 22.x, or 24+). Install dependencies with `uv sync --group dev` and
`npm ci --prefix frontend`.

Start each service in its own terminal:

1. Install the connector following the [maintainer instructions](../integrations/linkedin-mcp-server/README.md),
   then run from that checkout:

   ```sh
   uv run -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 8000 --no-auto-import
   ```

2. From the Compass root:

   ```sh
   MCP_URL=http://127.0.0.1:8000/mcp uv run -m linkedin_dashboard
   ```

3. From the Compass root:

   ```sh
   npm run dev --prefix frontend
   ```

Open `http://127.0.0.1:5173/brief`. Manual mode retains **Check connection** and
does not own or stop the separate connector. Do not run manual and managed modes
on the same ports simultaneously.

## Launcher implementation

The `compass` shell entry point bootstraps uv if needed, without modifying shell
profiles or requesting sudo. uv supplies Python 3.13 and the locked dashboard
dependencies in `.compass/dashboard-venv`, leaving the developer `.venv` alone.
Windows resolves `.\compass` to `compass.cmd`, which runs
`scripts/compass-windows.ps1` with a process-local execution policy. It installs
the same uv/Python versions locally and forwards all launcher options and exit codes.
`backend/linkedin_dashboard/launcher.py` then:

1. Locks this checkout and checks both loopback ports before setup.
2. Checks out connector revision `f410bfdc32569f8763fde11338b24ec6a0797f0d`
   in `.compass/`, validates and applies the two bundled extensions, and installs
   its locked dependencies in an isolated environment. No Git identity is needed.
3. Reuses a supported Node/npm installation or downloads Node 22.22.0 from
   nodejs.org, verifying its SHA-256 against the official release manifest.
4. Installs/builds the frontend when package or source fingerprints change.
5. Starts the API with a static SPA on the same origin, opens Compass, and starts
   the dedicated connector session. First login is interactive; cancelled login
   is retryable in the app. No searches are issued by the launcher itself.
6. Stops its owned connector process group when the app exits. Port conflicts
   cause an error, never termination of someone else's process.

Windows uses Job Objects to own connector descendants, portable byte-range
locks for exclusive ownership, and native ACLs for private storage. Restart uses
a request tied to the recorded process identity so the previous dashboard can
close its queue and connector before releasing its lock. Windows Node downloads
use the checked official ZIP; npm runs through Node directly to preserve paths
containing spaces. Git attributes retain LF endings in bundled patches.

The platform workflow runs launcher, storage, and queue tests on Windows, macOS,
and Linux, plus real `--setup-only` smoke checks. Windows command-resolution tests
exercise `.\compass` and `./compass` from a path with spaces and non-ASCII text.
These checks use local test processes; interactive LinkedIn sign-in is manual.

Operational settings and the existing database path still apply. Managed mode
sets HOST/FRONTEND_HOST to 127.0.0.1, FRONTEND_PORT to the API port, and MCP_URL to
the owned connector. Use launcher flags for those ports. The connector session is
outside the repo at `~/.compass-linkedin/profile`; deleting `.compass/` reinstalls
tools but does not delete login or candidate data.

The original connector remains Apache-2.0; its LICENSE and NOTICE travel with the
checkout. Compass remains MIT. The extensions are an internal compatibility step,
not an installation task for users.

## Verification without LinkedIn

`./compass --setup-only` checks dependency installation and frontend build. It
never launches login or the connector. `tests/unit/test_launcher.py` uses fake
processes to exercise login failure/retry, process cleanup, cached setup, port
conflicts, and SPA fallback. The documentation screenshot tool intercepts APIs.

Official installer references: [uv installer options](https://docs.astral.sh/uv/reference/installer/)
and [Node release checksums](https://nodejs.org/dist/v22.22.0/SHASUMS256.txt).

## Development checks

From the repository root:

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
```

From `frontend/`:

```bash
npm test
npm run lint
npm run build
npm run test:e2e
```

Browser tests run on isolated port **5194** with mocked APIs. Frontend tests also
bind temporary loopback listeners, so a restrictive sandbox may need to permit
those local test processes.

The [4 September review](reviews/2026-09-04-project-review.md) recorded
**1,403 backend tests passed, 3 skipped; 85 frontend tests passed; 15 browser tests
passed**, plus build, lint, and type checks. It distinguishes the full backend
baseline from the affected checks rerun after fixes.

## Contributor references

- [Frontend guide](../frontend/README.md): components, routes, and API contracts.
- [Design system](../frontend/DESIGN.md): shared UI conventions.
- [Review workflow](reviews/review-workflow.md): source checks and verification boundaries.
- [Project review](reviews/2026-09-04-project-review.md): dated findings and verification.
- [Implementation history](implementation-history.md): technical decisions and acceptance records.
- [Delivery plan](../PROJECT_PLAN.md): retained roadmap and implementation history.
- [Screenshot guide](screenshots/README.md): regenerate fictional screenshots with
  `npm run docs:screenshots` from `frontend/`, using isolated, intercepted APIs.

## Additional launcher options

- `./compass --setup-only`: install dependencies without starting services or login.
- `./compass --no-open`: start services and print the URL without opening the app tab.
- `./compass --port 8788 --connector-port 8001`: use different local ports.
