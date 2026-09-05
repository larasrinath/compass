# Compass · LinkedIn Dashboard

A local workspace for finding candidates, saving profile evidence, and comparing
people against a role brief. Compass uses a separately running
[linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) for
on-demand retrieval. Saved profiles, evidence review, and rescoring remain usable
when the connector is offline.

![Compass candidate results, showing evidence summaries and review actions](docs/screenshots/candidate-results.png)

*Screenshots show the actual app with fictional documentation data. They do not
contain real candidate records or represent live search results.*

## Working with Compass

1. **Role brief** — describe the role, then enter skills and keywords, credentials,
   nice-to-haves, locations, and minimum experience. Target titles, industries,
   network, and company preferences are in the optional section. The description
   is not automatically parsed; review the criteria before continuing.
2. **Find candidates** — run a search from the saved brief. Each location queues
   a separate search. Finding a person adds a reference; **Save profile** retrieves
   their profile text for review. Keep the current **Cards** view, or switch to
   **Ranked list** after reviewing the list to see rank, score, and confidence.
   Ranked results use highest-score-first order, with unscored profiles last.
3. **Check the candidate list** — inspect names, LinkedIn links, and repeated
   source searches. Add a note under **What did you check?** and choose
   **Confirm list & show ranking**. You stay in Find candidates, now in Ranked list.
4. **Compare matches** — inspect evidence summaries, select two or three people,
   then choose **View comparison**. **Review** opens the candidate drawer with the
   score first. Under **Review against your criteria**, open a quoted passage to
   read its highlighted saved source. Check whether it supports the criterion, then
   continue to career history and any missing information.
5. **Saved searches** — return to a previous run. Each card previews up to three
   saved profiles; **Open results** opens its full candidate pool.
6. **How it works** — explore interactive examples and guided tours. Exercises
   use fictional data and never modify saved work or send connector requests.

Matching is based on retrieved text and supported aliases. A match is not
independent verification, and missing evidence is not proof that someone lacks a
qualification. **Review score evidence** opens the source checks; opening a source
alone does not mark it verified or change the score. **Record source checks** on
Compare matches is an optional audit record requiring ten distinct current
passages and a note. Unrecorded checks clear on reload or changes to scoring inputs. Network and company search context do not affect
match scores.

### Role brief

Repeated locations, consistent pills, and always-visible optional preferences.

![Role brief with skills, multiple locations, minimum experience, and optional preferences](docs/screenshots/role-brief.png)

### Ranked list

An additional view in Find candidates, with the same saved-search and name filters.
Ranks stay stable while filtering by name and are scoped to the selected search.

![Find candidates in ranked list view, showing rank, score, confidence, and review actions](docs/screenshots/ranked-list.png)

### Candidate review

The score, uncertainty range, confidence, and individual signal results appear
at the top of the review drawer. Detailed verification is available below.

![Candidate review drawer with score and signal breakdown](docs/screenshots/candidate-review.png)

<details>
<summary>Saved searches and the interactive guide</summary>

### Saved searches

![Saved search with a three-person preview and Open results action](docs/screenshots/saved-searches.png)

### How it works

![Interactive guide organized into basics, working with results, and guided tours](docs/screenshots/how-it-works.png)

</details>

## Run locally

Requires Python **3.12.4–3.14**, [uv](https://docs.astral.sh/uv/), npm, and a Node.js
version supported by Vite: **20.19+ on the 20.x line, or 22.12+**. Browser checks
and screenshot capture use installed Google Chrome.

Install dashboard dependencies once from this repository:

```bash
uv sync --group dev
cd frontend
npm ci
```

Run the three services in separate terminals. The connector is a separate checkout
with its own installation and authentication; Compass does not start it or access
its browser profile.

**1. Connector — from the `linkedin-mcp-server` checkout**

```bash
uv run -m linkedin_mcp_server --transport streamable-http --host 127.0.0.1 --port 8000 --no-auto-import
```

**2. Dashboard API — from this repository root**

```bash
MCP_URL=http://127.0.0.1:8000/mcp uv run -m linkedin_dashboard
```

**3. Frontend — from this repository root**

```bash
cd frontend
npm run dev
```

Open [Compass](http://127.0.0.1:5173/brief). The API defaults to
`http://127.0.0.1:8787`; the frontend proxies `/api` to it. Start the API through
`uv run -m linkedin_dashboard` so its loopback checks remain in effect.

The connector is needed for new searches and profile retrieval. To review already
saved work offline, keep the dashboard API and frontend running; use **Check
connection** when reconnecting. Downloads of already saved text are local.

### Configuration and local data

Copy [.env.example](.env.example) to `.env` only to override defaults. `HOST`,
`FRONTEND_HOST`, and the host in `MCP_URL` must be numeric loopback literals, such as
`127.0.0.1` or `::1`; `localhost` and non-loopback addresses are rejected.
Vite derives its listener and API proxy from the same validated settings.

The database defaults to `~/.linkedin-dashboard/session.db` with owner-only file
permissions (`0600`) inside an owner-only directory (`0700`). A custom `DB_PATH`
parent must already have safe ownership and permissions. Saved source sections and
score history retain provenance; evidence spans use Unicode code-point offsets.

Current delivery covers discovery, retrieval, local analysis, and comparison.
Shortlisting, drafting, and sending are outside this delivery. `SEND_ENABLED`
remains false and `LLM_PROVIDER` is `null`; matching uses the local implementation.

## Verification

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

The [4 September review](docs/reviews/2026-09-04-project-review.md) recorded
**1,403 backend tests passed, 3 skipped; 85 frontend tests passed; 15 browser tests
passed**, plus build, lint, and type checks. It distinguishes the full backend
baseline from the affected checks rerun after fixes.

### Known limitations

- Navigating away from the brief discards unsaved edits. Save with **Continue to
  search** before leaving.
- A first credential-only brief requires a positive credential weight. Its setup
  currently involves saving another criterion first and configuring scoring.
- LinkedIn location is a search preference, not a guaranteed geographic filter.
  Check the location in the downloaded profile.
- Comparison selection is temporary: it survives opening and closing a profile,
  but resets on browser reload.

## Documentation

| Document | Purpose |
| --- | --- |
| [Frontend guide](frontend/README.md) | Routes, components, API contracts, local guide, and browser checks |
| [Design system](frontend/DESIGN.md) | Compass layout, typography, controls, and interaction rules |
| [Review workflow](docs/reviews/review-workflow.md) | User questions, source checks, and verification boundaries |
| [Project review](docs/reviews/2026-09-04-project-review.md) | Findings, fixes, verification, and remaining issues |
| [Implementation history](docs/implementation-history.md) | Queue, parsing, privacy, persistence, and dated acceptance records |
| [Delivery plan](PROJECT_PLAN.md) | Current scope and retained historical roadmap |
| [Screenshot guide](docs/screenshots/README.md) | Reproduce the fictional documentation screenshots |

Refresh the README screenshots from `frontend/` with `npm run docs:screenshots`.
The script renders the working UI on isolated port **5195**, intercepts all API
requests, blocks external requests, and shuts down its temporary browser and server.

## Acknowledgments

Thank you to [Daniel Sticker (@stickerdaniel)](https://github.com/stickerdaniel),
the original author of
[linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server), and
its contributors for building the open-source LinkedIn connector that makes
Compass possible. Compass connects to that separately running MCP server for
search and profile retrieval.

## License

Compass is licensed under the [MIT License](LICENSE).

The upstream LinkedIn MCP server retains its own
[Apache-2.0 license](https://github.com/stickerdaniel/linkedin-mcp-server/blob/main/LICENSE).
Bundled fonts retain their SIL Open Font Licenses:
[Figtree](frontend/public/fonts/figtree-OFL.txt) and
[Plus Jakarta Sans](frontend/public/fonts/plusjakartasans-OFL.txt).
