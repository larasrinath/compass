# Compass · LinkedIn Dashboard

A local workspace for finding candidates, saving profile evidence, and comparing
people against a role brief. Compass uses a separately running
[linkedin-mcp-server](https://github.com/stickerdaniel/linkedin-mcp-server) for
on-demand retrieval. Saved profiles, evidence review, and rescoring remain usable
when the connector is offline.

![Compass candidate results, showing evidence summaries and review actions](docs/screenshots/candidate-results.png)

*Screenshots show the actual app with fictional documentation data. They do not
contain real candidate records or represent live search results.*

## Start Compass

Download or clone this repository, open its folder in a terminal, and run:

```sh
./compass
```

Compass prepares its dependencies, opens the app at
[127.0.0.1:8787](http://127.0.0.1:8787/brief), and opens a LinkedIn sign-in window
on first use. Sign in there, set up your role brief in Compass, then choose
**Run search**. Later launches reuse the saved login and installed dependencies.

You do not need to install Python, Node.js, or uv separately, apply connector
patches, or start multiple terminals. The launcher handles those steps. The first
run needs internet access and may take a few minutes. It supports macOS and desktop
Linux with Git and curl installed; Linux also needs Chromium's system libraries.
No administrator privileges are requested by the launcher.

Keep the terminal open while using Compass. **Ctrl+C** stops its services; saved
profiles and searches remain on disk. If sign-in is cancelled, use **Sign in to
LinkedIn** in Compass to try again. To replace an expired login, stop Compass and
run `./compass --login`. Passwords are entered only in LinkedIn's browser window.

The launcher uses a dedicated LinkedIn session under `~/.compass-linkedin/` and
does not import cookies from your everyday browser. Retrieval still opens LinkedIn
pages in that session; it is not a bulk API.

<details>
<summary>Startup options and developer setup</summary>

- `./compass --setup-only` installs dependencies without launching services or login.
- `./compass --no-open` starts Compass and prints its URL without opening the app tab.
- `./compass --port 8788 --connector-port 8001` selects different local ports.
- Startup details are saved in `.compass/connector.log`. If a port is already used,
  stop the previous process or select another port. Compass never kills an unrelated service.

For frontend hot reload, separate processes, and connector maintenance, see the
[development setup](docs/development.md). The normal launcher serves the built
interface from the API, so a separate Vite process is unnecessary.

</details>

## Automatic profile downloads

With **Download profiles automatically** enabled in Settings (the default),
**Run search** also authorizes downloading the people returned by that search,
up to the configured batch limit (default **1,000 new profile downloads**).
The network filter is forwarded to LinkedIn:
1st-degree (`F`), 2nd-degree (`S`), or 3rd-degree and beyond (`O`). Select all three,
or leave them all unchecked, to search across networks. Network distance does not
contribute to a match score. Broad skills-based searches can surface people an
exact-title search misses; no search guarantees complete LinkedIn coverage.

With **Continue through search pages** enabled (the default), Compass follows
people-search pages automatically, preserving the same keywords,
location, network and company filters. The launcher installs the compatible connector automatically.
[Connector integration notes](integrations/linkedin-mcp-server/README.md) explain the extensions.
Each page is a separate checkpointed queue job; all pages appear as one saved search.
Discovery stops at the configured download or page limit, an empty or repeated
page, a failed/incomplete page, or **Stop discovery**. Already queued profile downloads continue after stopping.
LinkedIn controls the results it exposes: the ceiling does **not** guarantee 1,000
matches. **Saved candidates and search history have no lifetime count cap.**
Previously downloaded profiles are reused and do not consume a new batch’s allowance.
For example, a workspace with 10,000 saved profiles can queue another 1,000 new
profile downloads. Starting a new search gets a fresh batch allowance; it does not
delete or reset previous records. A page crossing the batch boundary can leave a
few additional discovered candidates waiting for a later request.

Downloads use a durable queue with one or two isolated profile tabs, selected in
Settings and capped by the connector’s capacity (one with older connectors).
Retrieval opens LinkedIn pages in the signed-in browser; it is not a bulk data API.
Hidden browser tabs do not make profile visits anonymous. The queue uses its configured pause
between calls, rate-limit cooldowns and operator pause/resume controls. A batch
reserves two page reads per newly requested profile (overview and experience).
Its explicit authorization expands the session read budget only as needed for
those initial reads; extra sections and retries remain subject to the budget.
Already requested profiles are skipped, including failed/cancelled requests;
retry those individually after inspecting the failure. Restarting the app does
not turn old searches into new download requests. Select an older search under
**Results from**, then use **Download remaining profiles** to catch up.

For `POST /api/searches`, omitted or null `automatic_downloads` and `paginate`
flags now use the saved Settings defaults. Explicit booleans override them for
that request. API clients requiring discovery only must send
`automatic_downloads: false`; use `paginate: false` to request only the first page.
`POST /api/searches/{id}/stop` stops further
discovery pages. `POST /api/searches/{id}/downloads` requests an
idempotent catch-up batch for that search.

See [download behavior and verification](docs/reviews/automatic-downloads.md) for implementation details.

## Working with Compass

1. **Role brief** — describe the role, then enter skills and keywords, credentials,
   nice-to-haves, locations, and minimum experience. Target titles, industries,
   network, and company preferences are in the optional section. The description
   is not automatically parsed; review the criteria before continuing.
2. **Find candidates** — run a search from the saved brief. Each location queues
   a separate search. With the default settings, newly found profiles and experience download automatically,
   up to two people at a time, and scoring updates after each download. Repeated results
   reuse saved profiles. The activity summary shows the current task and waiting counts;
   **View tasks** opens ten tasks at a time with individual cancellation controls.
   Keep the current **Cards** view, or switch to
   **Ranked list** after reviewing the list to see rank, score, and confidence.
   Both views show at most 30 profiles per page. Filtering searches the full pool;
   ranked results keep highest-score-first order across pages, with unscored profiles last.
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
6. **Settings** — adjust download concurrency, pacing, batch limits, automatic
   retrieval, and retry timing. Save these with **Save settings**. Update signal
   priorities separately with **Save scoring weights**; saved evidence is rescored
   locally. Location equivalences are under **Location matching**.
7. **How it works** — explore interactive examples and guided tours. Exercises
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

### Settings

Operational controls are separate from role criteria. Scoring weights have their
own save action below the download settings.

![Settings with aligned download controls, search batch limits, and retry timing](docs/screenshots/settings.png)

<details>
<summary>Saved searches and the interactive guide</summary>

### Saved searches

![Saved search with a three-person preview and Open results action](docs/screenshots/saved-searches.png)

### How it works

![Interactive guide organized into basics, working with results, and guided tours](docs/screenshots/how-it-works.png)

</details>

### Configuration and local data

Open **Settings** in the sidebar to configure simultaneous profile downloads
(1–2 with the current connector), read pacing, automatic downloads, automatic
search pagination, per-batch download/page limits, and retry delays. Operational
settings are stored in the local database and survive restarts. Saved pacing
settings override `INTER_CALL_DELAY_SECONDS`; before the first save, its configured
value is used. Concurrency is capped by the connector's supported capacity.
Concurrency and pacing apply to upcoming reads; new batch limits apply to new searches, while
existing batches retain their original limits. Saving settings does not resume a
paused queue or start the browser connector.

Scoring weights and metro/region equivalences also live in **Settings** and have
their own save action, which recalculates scores locally. Role criteria, locations,
company and connection filters remain in **Role brief**.

Copy [.env.example](.env.example) to `.env` only to override startup defaults. `HOST`,
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
| [Development setup](docs/development.md) | Launcher internals and manual hot-reload setup |
| [Frontend guide](frontend/README.md) | Routes, components, API contracts, local guide, and browser checks |
| [Design system](frontend/DESIGN.md) | Compass layout, typography, controls, and interaction rules |
| [Settings reference](docs/settings.md) | Defaults, limits, persistence, and API behavior |
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
