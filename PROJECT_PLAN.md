# PROJECT_PLAN.md — Local Sourcing Dashboard (companion to `linkedin-mcp-server`)

**Status:** Current delivery scope clarified 2026-09-04; original plan retained below · **Original date:** 2026-09-02
**Target repo under inspection:** `https://github.com/stickerdaniel/linkedin-mcp-server` @ `main` (`4.23.3`)
**Companion app codename:** `linkedin-dashboard`

> Every claim about the MCP server below is cited to a file and line in the
> inspected checkout. Anything I could not verify from the source is marked
> **[requires verification]** and never assumed true in a design decision.

---

## Current delivery scope — superseding decision, 2026-09-04

The operator has resumed this project as **on-demand LinkedIn MCP downloads → locally
saved data → local analysis, explainable rankings and an evidence dashboard**. Saved work
must remain usable with MCP disconnected and after the dashboard restarts. Retrieval needs
MCP availability; saved browsing and local rescoring do not require a persistent connection
or connectivity. The existing startup `tools/list` status probe may still attempt a connection;
a failed status probe must not block saved work.

**Shortlisting, drafting and sending are outside this delivery.** This decision supersedes
conflicting scope, sequencing and completion gates in the original executive summary,
requirements and M5–M8 outreach roadmap, including §20.5's live-send exercise and §25's
whole-project definition of done. Those records remain historical/future requirements, not
additional gates for finishing the current dashboard. Applicable security, privacy,
provenance, scoring, migration and data-integrity requirements remain in force.

### Current offline acceptance

These are required demonstrations, not a claim that acceptance has already passed:

1. Download through a fake MCP into a temporary persistent database, then restart the app
   against that same database with MCP unavailable. Saved sessions, search results, rankings,
   candidate details and raw evidence remain accessible, subject to the existing Gate A.
2. Record Gate A locally from a completed saved search and a non-empty operator review note
   while offline; verify that it survives restart and unlocks saved rankings. An already
   recorded Gate A must also survive restart. This is the existing saved-search review gate,
   not a requirement for a fresh live search or a live connection.
3. Edit local weights and scoring brief inputs, then rescore saved profiles without retrieval;
   preserve version history and evidence integrity. An unavailable MCP status must not block
   browsing, and an explicitly requested download that fails must preserve saved data.
4. Retain Gate B's implemented validation: Gate A plus ≥10 distinct, current, same-session
   exact profile-span evidence ids; reject coverage, missing metadata and search context.
   Exercise these checks locally; no live Gate A/B acceptance is claimed or required this run.

Live MCP integration is **not exercised in this run**. Fake/offline checks establish the local
workflow only; they do not establish real LinkedIn retrieval compatibility or live acceptance.

---

## 1. Executive summary

*Original broader roadmap; current delivery is governed by the superseding scope above.*

We build a **single-operator, local-only dashboard** that drives the existing LinkedIn MCP
server over loopback streamable HTTP to run one sourcing session: search → collect candidate
references → staged profile enrichment → transparent, evidence-backed ranking → manual
shortlist → editable personalized draft → **one deliberate, per-candidate send**.

Three properties define the design:

1. **The MCP server is the only thing that touches LinkedIn.** The dashboard has no browser,
   no cookies, no session directory. It speaks MCP and nothing else.
2. **The browser is a single serialized resource.** `SequentialToolExecutionMiddleware`
   (`linkedin_mcp_server/sequential_tool_middleware.py:22`) already forces one tool call at a
   time via an `asyncio.Lock` plus a cross-process profile lease. Our task queue therefore has
   concurrency exactly 1 — anything else is a lie the queue tells the UI.
3. **Sending is not a pipeline stage.** It is a terminal, human-pressed button behind a
   single-use confirmation token, an idempotency key, and a state machine that treats
   "we don't know if it sent" as its own state that is *never* auto-resolved.

The scoring model is deterministic and evidence-linked. An LLM is used for *proposals*
(candidate field spans, prose explanation, message drafting) and never for verdicts: a
proposed span becomes evidence only if it is found by exact substring match in the raw
retrieved text. `unknown` always renders as *"not found in the retrieved data"*.

Estimated effort: **8 milestones, ~5–7 working days** for one engineer to the smallest usable
MVP (M0–M5), with messaging (M6) gated behind explicit acceptance of M3/M4.

---

## 1a. Decision record — LOCKED

Approved on the dates recorded below. These are settled, not open questions. Each carries a **guard** — an
automated check that fails the build if the invariant is violated — so a locked decision cannot
be quietly undone by a later change. Reversing one requires editing this section first and
saying why; §29's checklist is run against this table at every milestone acceptance.

### Architecture decisions approved

**SCOPE-01 — approved 2026-09-04:** On-demand MCP retrieval with durable local saved data
and offline analysis is the current delivery boundary. No persistent MCP connection is
required. Shortlisting, drafting and sending are deferred outside this delivery. The current
offline acceptance above is the guard for this decision; historical live/outreach milestones
do not expand it. This changes scope, not the security or data-correctness invariants below.

| ID | Decision | Locked outcome |
|----|----------|----------------|
| **D-01** | Where the companion app lives | **Sibling repository `linkedin-dashboard/`**, created beside `linkedin-mcp-server`. The MCP server is an upstream dependency and the integration boundary; it is not vendored, forked, or modified. Guard: NFR-013 (`git diff --stat linkedin_mcp_server/` empty at every acceptance). |
| **D-08** | MCP server run mode | **Direct streamable HTTP on `127.0.0.1`.** No daemon owner, no bearer token, no lifecycle management of the server by the dashboard. Consistent with A-03 (`server.py:177-184`: only an owner authenticates). Guard: `mcp/client.py` constructs no auth header; a test asserts it. |
| **D-02** | LLM provider and data egress | **Local-first. `NullProvider` is the default and the only provider through M5.** Search, enrichment, deterministic scoring, evidence and shortlisting complete with **no profile text leaving the machine**. The provider interface is preserved so the hosting decision for message generation is made at M6, on its own merits. Guard: LD-08. |
| **D-03** | Whether any MCP server change is permitted | **None.** The existing read-only tools are sufficient; search pagination remains out of scope and in PL-1. Approved 2026-09-03. Guard: NFR-013 and §29 item 2. |
| **D-04** | Default network filter | **`["F","S"]`.** The search UI warns that only `F` reliably yields messageable candidates; network selection is search context only and never affects scoring. Approved 2026-09-03. Guard: the default-value UI test plus the S-7 zero-weight guard. |
| **D-05** | Retention window | **30 days**, with a visible countdown and manual purge. Approved 2026-09-03; implementation remains deliberately assigned to M8/T-8.1. Guard: the T-8.1 retention and purge tests. |
| **D-06** | Automatic Stage-2 promotion | **An operator-configured threshold is supported but defaults off.** Implementation remains deliberately assigned to M5/T-5.3. Approved 2026-09-03. Guard: no-implicit-promotion and explicit-threshold tests. |

### M3 acceptance decision and evidence

**Historical decision, approved by the operator 2026-09-03 and superseded
below:** for this one-time local activity, the minimum real-profile corpus for
T-3.3 was reduced from **≥8 profiles to ≥2 profiles** while retaining a
**≥90%** manually annotated both-correct title-and-company threshold. This
record is retained to explain the acceptance criteria used for the historical
results below; it is no longer the active M3 gate.

**Superseding decision, approved by the operator 2026-09-03:** the final M3
live parser gate must use **all three authorized real profiles** and pass only
if the manually annotated share of experience blocks with **both title and
company correct is strictly >85%**. Exactly 85% does not pass. The operator
approved this denominator and threshold for a one-time local activity. The
eight-profile synthetic regression corpus remains unchanged and does not count
toward the live denominator.

Sanitized live QA on exact build
`e3240dc42f0158b6f5a7dfb9cbe0cb2eaf42eaf3` established that 2/2 Stage-1
queue jobs passed, 4/4 required sections were stored verbatim, URN-if-present
handling passed 2/2, and forbidden send, search, and draft counts were all
zero. The evidence artifact SHA-256 is
`284ee3635b3c2c28a67fe77350ab9c3e6dc9ed92f6ee76f7d0db925e5add5b61`;
profile names and raw profile data are intentionally omitted here.

The manually annotated both-correct result was **70.5882%** on exact build
`e3240dc42f0158b6f5a7dfb9cbe0cb2eaf42eaf3` and **88.2353%** on build
`944cd55`; both are retained as historical failed gates because each was below
the then-current ≥90% threshold. These sanitized records disclose neither
profile identities nor raw profile text. The separately reported 89.81% was
parser-output completeness, not manually annotated accuracy.

**M3 accepted by the operator 2026-09-03.** Sanitized live acceptance on exact
tested build `28c2b8af922a74ffd53eccc6336a999103dfaa6a` covered all three
authorized real profiles. Manual annotation found **19/21 experience blocks
both-correct for title and company: 90.4762%**, which passes the strictly >85%
gate. Exactly one Stage-1 queue job ran for each profile (3/3); verbatim
`main_profile` plus `experience` storage passed for each profile (3/3); and
raw-before-parse ordering, provenance, URN-if-present handling, and exact-span
checks passed. Forbidden operation counts were: search=0, connection=0,
Stage 2=0, draft=0, dry-run=0, message=0, and send=0.
The raw acceptance database and artifacts were purged after recording the
sanitized result and evidence digest. The evidence artifact SHA-256 is
`12c0a5f0ed92fa7e8ad71c7ce21aa25b15b095336f00f15a46a6a1c084b9e6ce`.
Profile identities and raw profile text are intentionally omitted.

The two non-both-correct blocks were grouped-parent layouts. They remain a
known, non-blocking parser limitation and are included in the 21-block
denominator. M3 acceptance satisfies the M4 entry gate, so **M4 is unblocked
but has not started**.

### Invariants locked

| ID | Invariant | Implemented by | Guard test |
|----|-----------|----------------|------------|
| **LD-01** | `send_unavailable` maps to `AMBIGUOUS` — never a failure, never a success. **An `AMBIGUOUS` send attempt is immutable and cannot be retried.** It blocks further dashboard sends until the operator explicitly resolves it. If resolved as `confirmed_not_sent`, the operator may create a **new** send attempt through the complete review and confirmation flow. The original row is preserved as `state='AMBIGUOUS'` forever; the verdict lives in a separate `resolution` field. This makes every uncertain attempt permanently auditable without making the candidate permanently unsendable. | §9.4, §16, §17, FR-076–FR-079 | T-7.3 mutation: reclassify it as `FAILED_CONCLUSIVE` and a test must go red; DB trigger `send_attempt_is_immutable`; index `one_live_send_per_candidate` |
| **LD-02** | No retry, no polling, and no automatic state transition out of `AMBIGUOUS` — no timer, scheduler, watcher, backoff loop, or background reconciler may touch a send attempt. | FR-078, §16, §17 layer 6 | T-7.3 + CI grep: the send path contains no retry/backoff/poll construct; a test asserts an `AMBIGUOUS` row is byte-identical after 60 s of runtime |
| **LD-03** | After a partial or rate-limited profile response, only the **missing** sections are re-requested — never the whole set. | FR-024, A-09 (`extractor.py:2081-2087, 2134-2135`) | T-3.2: simulate the abort; assert exactly one follow-up job containing exactly the missing section names |
| **LD-04** | Candidate volume comes from **several narrow searches**, never from added pagination. The 15-reference cap (`link_metadata.py:93,108`) is surfaced to the operator, not worked around. | NG-9, FR-015, R-01 | §29 item 1; T-2.7 asserts the person-vs-total reference count is displayed |
| **LD-05** | `section_errors[*].runtime` and every other internal diagnostic (profile dirs, cookie paths, hostname, gist commands — `error_diagnostics.py:60`) are stripped before any frontend response. | NFR-002, T-0.5 | Global test: no API response body contains `.linkedin-mcp`, the operator's home path, or a `runtime` key |
| **LD-06** | `profile_urn` and any inferred messageability are **never scored**. They are displayed as a hint and carry zero weight. | FR-047, FR-048, §14.2 S-7 | T-4.7a/T-4.7b: assert no kernel or persistence definition references `profile_urn`, messageability, or compose-anchor presence as scoring input |
| **LD-07** | Every displayed match claim — score signal, evidence row, parsed field, and draft claim — resolves to an exact substring of stored raw text. No inferred, paraphrased, or model-generated claim is ever displayed as a match. | NFR-015, FR-031, FR-033, FR-041, FR-062 | T-4.3 integrity test re-reads every evidence span and compares byte-for-byte; T-6.3 for draft claims |
| **LD-08** | Through M5, no profile text leaves the machine. `NullProvider` is the default; the pipeline is complete and useful without any LLM. | D-02, T-3.4, T-6.1 | Test: the full M0–M5 integration suite passes with `LLM_PROVIDER=null`; a test asserts no module outside `llm/` imports a provider SDK |

**Still open** (none blocks M4–M5): D-07, D-09, D-10 — see §27.

---

## 2. Goals and measurable success criteria

| ID | Goal | Measure | Target |
|----|------|---------|--------|
| G-1 | Run one complete sourcing session end to end | Sessions completed from brief to at least one reviewed message | ≥ 1 |
| G-2 | Surface a usable candidate set from one search | Deduplicated `/in/` candidate references per `search_people` call | ≥ 8 (see §26 R-01) |
| G-3 | Minimize LinkedIn navigation | Page navigations per enriched candidate | ≤ 6 (main + experience + up to 4 promoted sections) |
| G-4 | Every score is explainable | Scored candidates whose every non-zero signal resolves to a quoted span in stored raw text | 100% |
| G-5 | No unintended send | Sends that did not originate from a `POST /api/candidates/{id}/send` carrying a valid single-use token | 0 |
| G-6 | No duplicate send | Distinct successful `send_message(confirm_send=true)` calls per candidate per session | ≤ 1 |
| G-7 | Partial data never blocks | Candidates that reach a score despite ≥ 1 `section_errors` entry | 100% of such candidates |
| G-8 | Zero MCP server modification for MVP | Lines changed under `linkedin_mcp_server/` | 0 |
| G-9 | Operator trust in drafts | Drafts sent without any edit *and* containing an unsupported claim | 0 |
| G-10 | Session is disposable | `DELETE /api/session` removes all profile text and leaves no residue outside the DB file | verified by test |

---

## 3. Assumptions

| ID | Assumption | Basis | If wrong |
|----|-----------|-------|----------|
| A-01 | A valid login profile exists at `~/.linkedin-mcp/profile/` before an operator requests LinkedIn retrieval; it is not a dashboard startup or saved-data prerequisite | `AGENTS.md` § Verifying Bug Reports; `DEFAULT_USER_DATA_DIR` `config/schema.py:82`; SCOPE-01 | Report retrieval unavailability with the `--login` guidance; never attempt login automatically. Saved browsing and local rescoring remain available |
| A-02 | The MCP server runs as a **direct** server on `127.0.0.1:8000/mcp` | `ServerConfig` defaults `config/schema.py:432-434` | Operator sets host/port in settings; nothing else changes |
| A-03 | A direct streamable-HTTP server requires **no bearer token** (settled by **D-08**, locked) | `create_mcp_server(..., auth_token=...)` refuses a token unless `role is ServerRole.OWNER` (`server.py:177-184`) | If a daemon owner is used instead, the client must present its token; see D-08 |
| A-04 | Host/Origin protection is on and loopback Hosts are always served | `mcp.run(..., host_origin_protection=True)` `cli_main.py:609`; `tests/test_transport_security.py:177` | Backend must send `Host: 127.0.0.1:8000` and no `Origin`, which a server-side HTTP client does by default |
| A-05 | One `search_people` call = one navigation = one results page | `extractor.search_people` builds one URL and calls `extract_page` once (`extractor.py:4455`) | Operator runs several narrower searches; we do not add pagination (§6 NG-9) |
| A-06 | `search_results` references are capped at 15 and person refs share that cap with other kinds | `_SEARCH_RESULTS_REFERENCE_CAP = 15` (`link_metadata.py:93`), `_REFERENCE_CAPS["search_results"]` (`link_metadata.py:108`) | See R-01; mitigated by multi-search, not by scope growth |
| A-07 | `get_person_profile` sections are a **comma-separated string**, and unknown names are reported, not fatal | `parse_person_sections` `scraping/fields.py:29`; `result["unknown_sections"]` `tools/person.py:96` | N/A — verified |
| A-08 | Sections are visited in `PERSON_SECTIONS` declaration order with a 2.0 s delay between them | `scrape_person` `extractor.py:2038-2040`; `_NAV_DELAY = 2.0` `extractor.py:71` | Timing estimates shift; behavior unchanged |
| A-09 | A rate limit **aborts the remaining sections** of that call and returns what was gathered | `extractor.py:2081-2087, 2134-2135` (`rate_limited = True; break`) | N/A — verified; drives our resume logic (FR-024) |
| A-10 | `profile_urn` is present only when a compose anchor exists in `<main>` | `_extract_profile_urn` `extractor.py:2587-2608` and its docstring | Messageability precheck falls back to the `confirm_send=false` dry run (FR-070); neither hint is scored (FR-047) |
| A-11 | `send_message(confirm_send=false)` navigates but never types or clicks send | `extractor.py:5010-5017` — the `if not confirm_send:` return precedes the `keyboard.type` at `extractor.py:5049` | The dry run would become a write; the whole validation step would have to be removed |
| A-12 | Default tool timeout is 180 s | `DEFAULT_TOOL_TIMEOUT_SECONDS` `config/schema.py:18` | Our client timeout must exceed it; see NFR-006 |
| A-13 | Tool errors reach the client as `ToolError` strings with `mask_error_details=True` | `server.py:215`; `error_handler.raise_tool_error` | Error classification is string/structure-based, not type-based; see §18 |
| A-14 | LinkedIn's per-message character limit **[requires verification]** | Not encoded anywhere in the repo | UI shows a live character count (required by the brief) and a soft warning at a configurable threshold; no hard client-side cap is invented |
| A-15 | Connection degree is **not** exposed by any read-only tool | No read-only tool returns `ConnectionState`; `detect_connection_state` (`scraping/connection.py:94`) is reached only from `connect_with_person` (`extractor.py:2384`) | The search's `network` filter is retained only as typed search context (§14 S-7), never profile evidence or a scoring input; we never call `connect_with_person` (NG-6) |

---

## 4. Functional requirements

### Role brief

| ID | Requirement |
|----|-------------|
| FR-001 | Operator creates a **role brief**: job description (free text), required skills, optional skills, target titles, location, industries, positive keywords, negative keywords, message tone, and optional `required_experience_months` (an integer ≥ 0). Empty scoring inputs are valid: no required skills disables S-1; no optional skills disables S-2; `required_experience_months` `null`/`0` disables S-3; no target titles disables S-4; no industries disables S-5; a blank/absent target location disables S-6; and no required credentials disables S-8. A positive-keyword-only brief is therefore valid and follows the all-inert no-score contract in FR-040. |
| FR-002 | A brief is versioned; editing any scoring input after scoring — including `required_experience_months`, required credentials or their aliases — creates a new version and marks existing scores `stale`. Removing the final credential also atomically creates the next scoring-config version with S-8 forced to 0 before one rescore per candidate. |
| FR-003 | Skills, titles, industries, and structured `required_credentials` accept per-term **aliases** (e.g. `k8s` ≡ `kubernetes`) stored with the brief. Activity is derived after trim, normalization and empty-term removal; an alias without a non-empty primary term cannot activate a signal. An empty credential list makes S-8 inert and forces its current weight to 0. |
| FR-004 | The brief editor refuses to save terms drawn from a protected-attribute blocklist (§14.6) and explains why. |
| FR-005 | Metro/region equivalences used by S-6 are an operator-editable, versioned scoring input stored alongside weights. An empty table means exact location matching only. Editing the table creates a new scoring-config version and marks prior scores `stale`. |

### Search & candidate collection

| ID | Requirement |
|----|-------------|
| FR-010 | Operator runs a search mapping 1:1 onto `search_people(keywords, location, network, current_company)`. |
| FR-011 | `network` is offered as a multi-select of exactly `F`/`S`/`O` with their meanings (`tools/person.py:126-130`) and defaults to `["F","S"]` (D-04). The UI warns that only `F` reliably yields messageable candidates. |
| FR-012 | `current_company` is entered as a **numeric URN id** only; the UI provides a lookup that calls `get_company_profile` and reads `references["about"]` entries with `kind: "company_urn"` (`tools/company.py:68-74`). Free-text company names are refused client-side with the server's own explanation (`extractor.py:4440-4446`). |
| FR-013 | The raw `sections["search_results"]` text and the full `references` payload are persisted verbatim before any parsing. |
| FR-014 | Candidate references are extracted from `references["search_results"]` where `kind == "person"`, normalized to a canonical `/in/<username>` username, and deduplicated across searches within the session. |
| FR-015 | Multiple searches accumulate into one candidate pool; each candidate records every search that produced it. The selected network tokens are typed, non-scoring search context only (§14.2 S-7). |
| FR-016 | `section_errors["search_results"]` is surfaced verbatim in the UI, distinguishing `rate_limit` (`extractor.py:204`) from a diagnostics payload. |

### Staged profile enrichment

| ID | Requirement |
|----|-------------|
| FR-020 | **Stage 1** retrieval calls `get_person_profile(linkedin_username, sections="experience")` — `main_profile` is implicit (`scraping/fields.py:40`). |
| FR-021 | **Stage 2** retrieval is per-candidate, operator- or rule-triggered, and calls `get_person_profile(linkedin_username, sections=<selected subset>)` for the promoted sections only. |
| FR-022 | Every retrieval is queued on a single-slot serialized queue; the UI shows queue position and live progress. |
| FR-023 | Raw section text is stored verbatim, per section, with a retrieval timestamp and the originating fetch id. |
| FR-024 | When a fetch returns `section_errors` with `error_type == "rate_limit"`, the remaining requested sections for that candidate are re-queued as a *separate later fetch* (never merged into the same call), and the session enters a cool-down. |
| FR-025 | `profile_urn`, when returned, is stored against the candidate. |
| FR-026 | `unknown_sections` in a response is treated as a client bug and logged loudly; the UI only ever offers names from `PERSON_SECTIONS`. |
| FR-027 | A candidate whose Stage-1 fetch fails entirely remains in the pool with status `retrieval_failed` and is never silently dropped. |

### Parsing & normalization

| ID | Requirement |
|----|-------------|
| FR-030 | A normalization layer converts raw section text into typed fields (name, headline, location, experience entries, skills, education, projects, certifications). |
| FR-031 | Every normalized field carries `(section_name, span_start, span_end, snippet, origin)` pointing back into the stored raw text. |
| FR-032 | `origin` is one of `deterministic` \| `llm_verified` \| `llm_unverified`. An `llm_unverified` field is displayed only, never scored. |
| FR-033 | An LLM-proposed span is promoted to `llm_verified` only when its `snippet` is found by exact substring match in the stored raw section text. |
| FR-034 | Raw text is never overwritten by normalization and remains viewable side-by-side with the parsed view. |

### Scoring

| ID | Requirement |
|----|-------------|
| FR-040 | A deterministic scoring function produces `score`, `score_lower`, `score_upper`, `confidence`, `confidence_band`, `calculation_status`, `active_signal_count`, and aggregate per-signal results with typed per-term/claim children (§14). S-1/S-2 may contain matched, not-matched and unknown children concurrently; scalar signals use one child. An all-inert brief is valid and persists a no-score result: nullable score/lower/upper are all `null`, confidence is `0`, band is `low`, status is `unknown`, active count is `0`, and no signal, claim, evidence, coverage or missing rows exist. |
| FR-041 | Each profile-derived claim has a verdict of `matched` \| `not_matched` \| `unknown` \| `contradicted` and exactly one mutually exclusive provenance kind. `matched`/`contradicted` require a non-empty set of exact `VerifiedSpan` evidence rows. `not_matched` requires audited deterministic coverage over every required section after all completed successfully (`profile_section_id`, immutable raw-content hash, searched normalized terms/aliases, matcher version), never a fabricated/empty span and never a match snippet. `unknown` requires typed missing-section metadata and no evidence span. DB constraints/triggers reject missing, mixed or verdict-incompatible provenance. |
| FR-042 | `unknown` renders as **"not found in the retrieved data"** and is visually distinct from `not_matched`. |
| FR-043 | Scores are marked `provisional` (Stage 1 only) or `enriched` (Stage 2 complete), and the badge is visible on every ranked row. |
| FR-044 | Re-scoring after enrichment preserves the previous score for comparison ("was 61 provisional → now 74 enriched"). |
| FR-045 | Scoring uses no protected or inferred sensitive characteristic (§14.6), enforced by an automated blocklist test. |
| FR-046 | The weights table and S-6 metro/region equivalence table are visible and editable as one immutable, versioned scoring configuration. An update requires the expected current version. When at least one input-active scoring signal exists, at least one such signal must have positive effective weight; otherwise `422` occurs before any version, staleness or rescore write. When every scoring signal is input-inert, weight/equivalence edits remain valid configuration versions and atomically stale/rescore each candidate once into the all-inert no-score form; no division or `0/0` is evaluated. S-7 and structurally invalid S-8 edits remain rejected. |
| FR-047 | **[LD-06]** `profile_urn` presence and any derived messageability signal carry zero weight and appear in no signal definition. They are displayed as a non-scoring hint, labelled as such. |
| FR-048 | S-7 network/connection information is typed **search context**, not profile evidence or a scoring signal. It has permanent weight 0 and is excluded from score, bounds, penalties, and confidence; configuration and persistence reject any non-zero S-7 weight. |
| FR-049 | A positive S-8 weight is rejected while required credentials are empty. Removing the final credential atomically writes the next brief and scoring-config versions with S-8=0, then stales/rescores once; re-adding credentials does not resurrect the old weight. If another input-active signal remains but the prospective positive effective weight sum is zero, the whole transition returns `422` with no writes. If removing S-8 makes the entire brief all-inert, the transition is valid and persists the FR-040 no-score form. |

### Review & shortlist

| ID | Requirement |
|----|-------------|
| FR-050 | Ranked candidate list with score, confidence band, stage badge, and top matched/missing signals. |
| FR-051 | Candidate detail view: parsed fields, evidence panel, and raw section text with the evidence span highlighted. |
| FR-052 | Operator can `shortlist`, `reject`, or `undecided` a candidate, with an optional note. Decisions are append-only with timestamps. |
| FR-053 | Ranking never auto-shortlists and never auto-promotes to Stage 2 without an explicit operator action or an operator-configured threshold they set themselves. |

### Message drafting

| ID | Requirement |
|----|-------------|
| FR-060 | A draft is generated only for a shortlisted candidate, only on explicit operator action, from the role brief plus that candidate's **retrieved evidence only**. |
| FR-061 | Drafting is refused for a candidate with no successfully retrieved `main_profile`. |
| FR-062 | Every draft passes a **grounding check**: named entities and factual claims in the draft must appear in the candidate's stored raw text or the brief. Failures are shown inline and block "mark ready" until edited or overridden with a recorded justification. |
| FR-063 | Drafts are freely editable in a plain textarea with a live character count; each save creates a new draft version. |
| FR-064 | The draft body is the exact string that will be sent — no server-side templating, trimming, or signature appending at send time. |

### Validation & sending

| ID | Requirement |
|----|-------------|
| FR-070 | Operator can run a **dry run** calling `send_message(linkedin_username, message, confirm_send=false, profile_urn=<stored or null>)` and see the returned `{status, message, recipient_selected, sent}` verbatim (`extractor.py:1042-1048`). |
| FR-071 | Dry-run outcome is stored and shown as a precondition badge; a `confirmation_required` status (`extractor.py:5014`) means "recipient resolved, composer reachable". |
| FR-072 | Opening the send confirmation modal calls `POST /api/candidates/{id}/send-confirmation`, which mints a **single-use token** bound to `(candidate_id, sha256(message_text))` with a 5-minute TTL. |
| FR-073 | The modal displays: candidate name, canonical LinkedIn profile URL, the exact message, character count, and an **"I reviewed this message"** checkbox. |
| FR-074 | "Send now" is disabled until the checkbox is checked **and** the displayed message hash equals the current stored draft hash. |
| FR-075 | "Send now" issues exactly one `POST /api/candidates/{id}/send` carrying the token and the message hash; the backend calls `send_message(..., confirm_send=true)` exactly once. |
| FR-076 | The result is recorded with the raw tool response, and the candidate's send state transitions per §16. |
| FR-077 | **[LD-01]** A finished `send_attempt` is **immutable**: its `state`, `body_sha256`, `idempotency_key` and tool response are never rewritten. A `SENT` attempt, and an `AMBIGUOUS` attempt whose `resolution` is `unresolved` or `confirmed_sent`, each block any further dashboard send for that candidate. Enforced by a DB trigger and a partial unique index, not by UI state. |
| FR-078 | **[LD-02]** No automatic retry, no polling, and no automatic state transition occurs for any send outcome, ever. No timer, scheduler, watcher, backoff loop or background reconciler may read or write a `send_attempt` row. The only writer after the initial call is an explicit operator action. |
| FR-079 | **[LD-01]** An `AMBIGUOUS` outcome offers a manual, operator-initiated verification action and a two-step resolution written to the attempt's `resolution` field only. `confirmed_sent` keeps the candidate blocked. `confirmed_not_sent` unlocks the creation of a **new** send attempt — a new attempt id, a new confirmation token, and a new idempotency key — which must pass the complete review and confirmation flow from the start. The original attempt is never reopened, reused, or rewritten to `DRAFT`. |
| FR-080 | **Fallback path:** "Copy message" (clipboard) and "Open LinkedIn" (opens the candidate's profile URL in the operator's own browser) so the operator can paste and press LinkedIn's native Send. Availability is **per send state**, not unconditional — see the matrix in §15. In short: both are offered wherever nothing has been sent; during `AMBIGUOUS` only "Open LinkedIn" is offered freely (for verification) and "Copy message" is guarded behind an explicit acknowledgement; after `SENT` neither is presented as a send fallback, because that is an invitation to send twice. |
| FR-081 | Sending is behind a global feature gate that is **off by default** and can only be switched on after phase gates A, B and C are recorded as accepted (§21). |

### Session, audit, privacy

| ID | Requirement |
|----|-------------|
| FR-090 | An append-only audit log records every MCP call (tool, arguments with message bodies hashed not stored twice, outcome, duration) and every operator decision. |
| FR-091 | `GET /api/session/export` produces a single JSON/CSV export of candidates, scores, evidence and decisions. |
| FR-092 | `DELETE /api/session` purges all candidate raw text and derived data; a separate `DELETE /api/session/raw` purges only raw text while keeping decisions and the audit log. |
| FR-093 | The dashboard displays the MCP server's reachability and last error at all times. |

---

## 5. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-001 | **Loopback only.** Backend binds `127.0.0.1`; the Vite dev server binds `127.0.0.1`. No `0.0.0.0` anywhere, enforced by a startup assertion. |
| NFR-002 | **No secrets to the frontend.** The frontend never receives MCP URL credentials, browser-profile or cookie paths, or any `runtime` diagnostics block. The final response boundary sanitizes JSON (including `application/*+json`) and SSE events across chunk boundaries, redacts credential-bearing URLs embedded in strings, and fails closed on malformed structured data. |
| NFR-003 | **Serialization fidelity.** At most one in-flight MCP tool call from this process. Enforced by a single asyncio worker, not by convention. |
| NFR-004 | **Politeness.** A configurable minimum inter-call delay (default 3 s) on top of the server's own `_NAV_DELAY` (`extractor.py:71`), plus an exponential session cool-down on `rate_limit`. |
| NFR-005 | **Budget ceiling.** A hard per-session navigation budget (default 120) after which the queue refuses new work until the operator raises it explicitly. |
| NFR-006 | **Timeouts.** The MCP client per-call timeout is 240 s, above the server's 180 s tool timeout (`config/schema.py:18`), so the server's own error surfaces rather than our transport's. |
| NFR-007 | **Durability.** SQLite in WAL mode; every MCP response is written to disk before it is parsed. A crash mid-session loses no retrieved text. |
| NFR-008 | **Reproducibility.** Given the same immutable profile snapshot, brief version and scoring-config version, scoring is byte-identical. Scoring imports no clock, no RNG, no network. |
| NFR-009 | **File permissions and path safety.** The DB, live `-wal`/`-shm` sidecars, and exports are `0600`; the DB lives under `~/.linkedin-dashboard/` by default, never inside the repo, and its final path may not be a symlink. Existing custom parent-directory permissions are not changed. |
| NFR-010 | **Observability.** Structured JSON logs with a correlation id per MCP call, viewable in-app. |
| NFR-011 | **Accessibility.** The confirmation modal is keyboard-operable, focus-trapped, and the "Send now" button is never the default-focused or Enter-activated element. |
| NFR-012 | **Startup safety.** If the send feature gate is on and the app cannot verify a completed phase gate C record, sending is disabled and the reason is displayed. |
| NFR-013 | **No MCP server change.** MVP ships with `git diff --stat linkedin_mcp_server/` empty. |
| NFR-014 | **Bounded memory.** Raw section text is streamed to SQLite and never held for more than one candidate at a time in the parser. |
| NFR-015 | **[LD-07] Exact raw-text provenance.** Every match claim the UI displays — score signal, evidence row, parsed field, draft claim — must resolve to an exact substring of a stored `profile_section.raw_text` at a recorded span. A claim that cannot is either not displayed or displayed under an explicit "unverified — not found in retrieved text" label that is visually distinct from a match. Enforced by the type system (`scoring/` accepts only `VerifiedSpan`) and by an integrity test that re-reads every span. |
| NFR-016 | **[D-02, LD-08] Local-first through M5.** With `LLM_PROVIDER=null` — the default — the entire pipeline from brief to shortlist works and no profile text leaves the machine. No module outside `llm/` imports a provider SDK. |

---

## 6. Explicit non-goals

| ID | Non-goal |
|----|----------|
| NG-1 | Bulk messaging, "send all", multi-recipient sending. |
| NG-2 | Scheduled, delayed, queued, or background sending of any kind. |
| NG-3 | Automatic connection requests. `connect_with_person` (`tools/person.py:194`) is never called. |
| NG-4 | Automated follow-ups or sequences. |
| NG-5 | Recurring monitoring, continuous scraping, cron, or watchers. |
| NG-6 | Any read of connection state via a write tool. |
| NG-7 | Campaign management, CRM, pipelines, stages beyond shortlist/reject. |
| NG-8 | Multi-user auth, team accounts, roles. |
| NG-9 | Search pagination beyond the one page `search_people` returns. |
| NG-10 | Cloud deployment, remote access, tunnels. |
| NG-11 | Browser fingerprint modification, proxy rotation, restriction circumvention. |
| NG-12 | Large-scale data collection; model training on collected profile data. |
| NG-13 | Editing, extending or forking the MCP server for the MVP. |
| NG-14 | Auto-retry of any send. |
| NG-15 | Inferring or scoring protected characteristics. |

---

## 7. Recommended architecture and component boundaries

```
┌────────────────────────────────────────────────────────────────────┐
│  Browser (operator)  ·  http://127.0.0.1:5173                      │
│  React + Vite + TypeScript · TanStack Query · Zustand              │
│  Knows: candidates, scores, evidence, drafts, send state           │
│  Never knows: MCP URL, profile paths, cookies, diagnostics runtime │
└──────────────────────────┬─────────────────────────────────────────┘
                           │  REST + SSE (loopback)
┌──────────────────────────▼─────────────────────────────────────────┐
│  FastAPI backend  ·  http://127.0.0.1:8787                         │
│                                                                    │
│  api/          thin HTTP layer, no business logic                  │
│  services/     brief · search · enrichment · scoring · drafting    │
│                · sending (the only module allowed confirm_send=T)  │
│  queue/        single-slot asyncio worker + job table              │
│  mcp/          MCP client, tool wrappers, response envelope        │
│  parsing/      raw text → typed fields + spans                     │
│  llm/          provider-independent protocol, no direct use elsewhere│
│  db/           SQLAlchemy models, migrations, retention            │
└──────────────────────────┬─────────────────────────────────────────┘
                           │  MCP streamable HTTP (loopback, no creds)
┌──────────────────────────▼─────────────────────────────────────────┐
│  linkedin-mcp-server  ·  http://127.0.0.1:8000/mcp   [UNCHANGED]   │
│  SequentialToolExecutionMiddleware  → one tool call at a time      │
│  LinkedInExtractor → Patchright Chromium → LinkedIn                │
└────────────────────────────────────────────────────────────────────┘
```

### Boundaries that must not be crossed

- **`mcp/` is the only module that constructs MCP arguments.** Services call typed wrappers
  (`search_people(...) -> SearchPeopleResult`), never raw dicts.
- **`services/sending.py` is the only module in the codebase permitted to pass
  `confirm_send=True`.** A CI grep asserts the literal appears in exactly one non-test file.
- **`llm/` returns proposals, never verdicts.** Its return types are `Proposal[T]`, and
  `scoring/` accepts only `VerifiedSpan`, which can only be constructed by the substring
  verifier in `parsing/verify.py`.
- **The M4 scoring kernel is pure.** `services/scoring/{types,matching,aggregate,signals/**}`
  accepts immutable brief, scoring-config and profile-snapshot values and returns an immutable,
  stably ordered calculation. It imports no DB, API, UI, MCP, network, clock or RNG code. All
  arithmetic is explicitly clamped and quantized; penalties are separate named outputs.
- **Profile evidence, absence coverage and search context are disjoint types.** Only a
  `VerifiedSpan` can construct profile evidence. Deterministic absence coverage can support
  `not_matched` but has no span/snippet; network provenance and messageability are display-only
  context and cannot enter a `score_claim`, aggregation, confidence or Gate B. A `score_signal`
  is only the numeric aggregate/rollup over its ordered claim children; it is not evidence.
- **`queue/` is the only caller of `mcp/`.** No request handler calls MCP inline; every MCP
  interaction is a job with a row in the DB, which is what makes it resumable and auditable.
  The one deliberate exception is the send itself (§16), which is synchronous by design so the
  operator's click and the tool call cannot become detached.
- **[D-01] The MCP server is upstream, not ours.** `linkedin-dashboard` is a sibling checkout
  that consumes the server across the MCP wire and nothing else. It does not import
  `linkedin_mcp_server`, vendor it, patch it, or manage its process. The one place the two
  couple is the Tier-1 contract test (T-8.3), which *reads* the server checkout to verify our
  assumptions and skips cleanly when it is absent. That is a test-time coupling, not a runtime one.
- **[D-08] The dashboard never authenticates to, or supervises, the MCP server.**
  `mcp/client.py` constructs no `Authorization` header and no `Origin` header, and the app does
  not start, stop, or restart the server — the operator runs it. A direct server takes no token
  (`server.py:177-184`); adding one would be building for the daemon-owner topology we did not choose.

### Stack recommendations and deviations

| Proposed | Recommendation | Rationale |
|---|---|---|
| React + Vite | **Keep**, add TypeScript + TanStack Query | Query gives cache/invalidation/polling for free; a hand-rolled fetch layer is where duplicate-send bugs breed. |
| FastAPI | **Keep** | Async, matches the async MCP client, Pydantic gives us the response filter of NFR-002 for free. |
| SQLite | **Keep**, WAL mode, SQLAlchemy 2.0 | One-time local session; anything else is unjustified. |
| Backend MCP client over loopback streamable HTTP | **Keep** | Preserves the integration boundary and the "frontend knows nothing" rule. |
| Serialized task queue | **Keep, but in-process** — `asyncio.Queue` + one worker task + a `job` table for durability | Celery/Redis/RQ would add two processes and a broker to serialize *one* browser that the server already serializes (`sequential_tool_middleware.py:22`). **Tradeoff:** we lose crash-resume of an in-flight job; we accept it because the `job` table records every job's state and a restarted worker re-queues anything left `running` as `interrupted` for the operator to re-run. That is strictly better than a broker for a single-user one-time tool. |
| Provider-independent LLM interface | **Keep**, with a `NullProvider` default | The MVP must be fully usable with **no LLM at all**: deterministic parsing and scoring work standalone, and message drafting falls back to a template the operator edits. This is the single biggest risk reducer in the plan. |

**MCP client library:** use `fastmcp`'s client (the server already depends on
`fastmcp>=3.4.4,<4`, which resolves to `fastmcp-slim[client,server]` — `uv.lock:567-573`), or
the official `mcp` SDK's `streamablehttp_client`. The exact class/transport names in fastmcp
3.4.4 are **[requires verification]** against the installed package; the wrapper in `mcp/`
exists precisely so that choice is swappable in one file (T-1.2).

---

## 8. End-to-end data flow

```
 (1) BRIEF
     operator ──► POST /api/briefs ──► role_brief(v1) ──► phase gate A unlocked

 (2) SEARCH                                          ┌── job row (queued)
     operator ──► POST /api/searches ────────────────┤
                                                     └── worker ──► MCP search_people
                                                                     (keywords, location,
                                                                      network[], current_company)
     MCP result {url, sections.search_results, references.search_results, section_errors?}
        │
        ├─► search_run.raw_response  (verbatim, before parsing)
        ├─► references[kind=="person"] ──► normalize username ──► dedupe ──► candidate rows
        └─► section_errors ──► surfaced in UI

 (3) STAGE-1 ENRICHMENT  (per candidate, serialized)
     worker ──► MCP get_person_profile(username, sections="experience")
     result {url, sections{main_profile, experience}, references?, section_errors?, profile_urn?}
        ├─► profile_section rows (raw_text verbatim, one per section)
        ├─► section_error rows
        └─► candidate.profile_urn

 (4) PARSE                              (5) SCORE (provisional)
     raw_text ──► deterministic matchers ──► parsed_field(+span)
              └─► LLM proposals ──► substring verifier ──► parsed_field(origin=llm_verified)
                                     │ (fails) ──► llm_unverified · display only
     immutable profile snapshot + brief + scoring-config
       ──► pure signals ──► score{score, lower, upper, confidence, stage=provisional}
                         └─► score_signal(aggregate rollup) ──► score_claim[]
                              ├─► matched/contradicted ──► evidence_set(VerifiedSpan[] → raw_text)
                              ├─► not_matched ──► coverage_set(section ids + hashes + searched terms)
                              └─► unknown ──► missing_set(section reasons)
     search network + profile_urn ──► typed non-scoring context/hints only

 (6) PROMOTE  (operator or operator-set threshold)
     ──► MCP get_person_profile(username, sections="skills,projects,certifications,education")
     ──► parse ──► (7) re-score {stage=enriched}, previous score retained for delta

 (8) REVIEW
     operator ──► shortlist | reject | undecided (append-only)   ──► phase gate B accepted

 (9) DRAFT
     brief + candidate evidence ──► LLM/template ──► grounding check ──► message_draft(v1)
     operator edits ──► message_draft(v2..n)                      ──► phase gate C accepted

(10) DRY RUN (optional but recommended)
     ──► MCP send_message(username, draft, confirm_send=FALSE, profile_urn?)
     ──► expect status "confirmation_required" ──► precondition badge

(11) CONFIRM
     operator opens modal ──► POST /send-confirmation ──► token(candidate, msg_hash, 5 min, single use)
     modal shows: name · profile URL · exact message · char count · [ ] I reviewed this message

(12) SEND  — the only path that reaches confirm_send=true
     click ──► POST /send {token, msg_hash} ──► token consumed atomically
            ──► send_attempt row (idempotency_key, state=SENDING) committed BEFORE the call
            ──► MCP send_message(username, draft, confirm_send=TRUE, profile_urn?)
            ──► classify status ──► SENT | FAILED_CONCLUSIVE | AMBIGUOUS
            ──► send control disabled (SENT/AMBIGUOUS) · re-enabled only on manual resolution

     FALLBACK (state-dependent, §15): [Copy message] [Open LinkedIn] — no MCP call at all
            offered freely while nothing is sent · guarded during AMBIGUOUS · withdrawn after SENT
```

---

## 9. MCP tool-to-feature mapping

Exact signatures as registered. `ctx` is injected by FastMCP and is not a client argument.
Every tool returns a JSON object; error paths raise `ToolError` with a masked message
(`server.py:215`).

### 9.1 `search_people` — FR-010…FR-016

**Registered at** `linkedin_mcp_server/tools/person.py:111`

| Arg | Type | We send |
|---|---|---|
| `keywords` | `str` (required) | Composed from brief target titles + positive keywords |
| `location` | `str \| None` | Brief location |
| `network` | `list[str] \| None` | Subset of `["F","S","O"]`; validated client-side against `_NETWORK_TOKENS` semantics (`extractor.py:4430-4436`) |
| `current_company` | `str \| None` | Numeric URN id only (`extractor.py:4438-4446`) |

**Returns** `{url, sections, references?, section_errors?}` (`extractor.py:4469-4477`).
- `sections["search_results"]`: raw innerText of the results page (absent when rate-limited).
- `references["search_results"]`: up to 15 `Reference` objects (`link_metadata.py:23`,
  cap at `link_metadata.py:93`). Each is `{kind, url, text?, context?, value?}`. We keep
  `kind == "person"`; `url` is a **site-relative** path such as `/in/alice/`.
- `section_errors["search_results"]`: `{"error_type": "rate_limit", "error_message": …}`
  (`extractor.py:203-206`) or a diagnostics dict (`error_diagnostics.py:77-97`).

**Failure surface:** a bad `network` token or a non-numeric `current_company` raises
`FilterValidationError` → re-raised as `ToolError` with the actionable text preserved
(`tools/person.py:165-169`).

### 9.2 `get_person_profile` — FR-020…FR-027

**Registered at** `linkedin_mcp_server/tools/person.py:38`

| Arg | Type | We send |
|---|---|---|
| `linkedin_username` | `str` (required) | Canonical username; the server also accepts a full URL (`normalize_person_identifier` `identifiers.py:307`) |
| `sections` | `str \| None` | **Stage 1:** `"experience"`. **Stage 2:** comma-joined subset of `skills,projects,certifications,education,honors,languages,interests,contact_info,posts` |
| `max_scrolls` | `int 1..50 \| None` | Omitted by default; raised to 20 only for a candidate whose section is visibly truncated (operator action) |

**Returns** `{url, sections, profile_urn?, references?, section_errors?, unknown_sections?}`
(`extractor.py:2141-2150`, `tools/person.py:91-92`).

**Cost model:** one navigation per requested section, in `PERSON_SECTIONS` order, with
`_NAV_DELAY = 2.0 s` between them (`extractor.py:2038-2040`, `extractor.py:71`). `main_profile`
is always included (`fields.py:40`). Stage 1 = 2 navigations; Stage 2 with 4 sections = 4 more.

**Rate-limit semantics we must honor:** on the sentinel, the section gets a
`rate_limit` error, remaining sections are **skipped**, and partial results are returned
(`extractor.py:2081-2087, 2134-2135`). Our resume logic (FR-024) diffs requested vs. returned sections.

### 9.3 `get_company_profile` — FR-012 (URN lookup only)

**Registered at** `linkedin_mcp_server/tools/company.py:44`. Called with
`company_name=<slug>` and default sections. We read `references["about"]` for
`kind == "company_urn"` and take `value` as the numeric id (`tools/company.py:68-74`).
Not present for the smallest companies — the UI says so and offers manual entry.

### 9.4 `send_message` — FR-070…FR-078

**Registered at** `linkedin_mcp_server/tools/messaging.py:221`

| Arg | Type | We send |
|---|---|---|
| `linkedin_username` | `str` (required) | Canonical username |
| `message` | `str` (required) | The exact stored draft text, byte for byte |
| `confirm_send` | `bool` (required) | `false` for dry run; `true` only from `services/sending.py` |
| `profile_urn` | `str \| None` | Stored `profile_urn` when present — the server calls this "more reliable" (`tools/messaging.py:240-244`) |

**Returns** `{url, status, message, recipient_selected, sent}` (`extractor.py:1042-1048`,
asserted by `tests/test_tools.py:898-904`).

**Status values, with their exact source lines and our classification:**

| `status` | Line | Reached | Our class |
|---|---|---|---|
| `message_unavailable` | `extractor.py:4933` | Before compose page opens | `FAILED_CONCLUSIVE` |
| `recipient_resolution_failed` | `extractor.py:4968`, `:5004` | Before composer is used | `FAILED_CONCLUSIVE` |
| `composer_unavailable` | `extractor.py:4983` | Before typing | `FAILED_CONCLUSIVE` |
| `confirmation_required` | `extractor.py:5014` | Only when `confirm_send=false` | `DRY_RUN_OK` |
| `compose_interact_failed` | `extractor.py:5044` | Focus failed, **before** `keyboard.type` at `:5033` | `FAILED_CONCLUSIVE` |
| `send_unavailable` | `extractor.py:5078` | **After** `keyboard.type` and the send click/Enter | **`AMBIGUOUS`** |
| `sent` (`sent: true`) | `extractor.py:5085` | Message text confirmed visible | `SENT` |

> `send_unavailable` is the one status that must never be treated as a failure. It is returned
> when `_message_text_visible(message)` is false *after* the text was typed and the send button
> clicked (`extractor.py:5074-5080`). That proves nothing about whether LinkedIn accepted it.

**Transport/timeout after `confirm_send=true`** (client timeout, connection drop, `ToolError`
with no structured status) is also `AMBIGUOUS` — for the same reason.

### 9.5 Tools we deliberately do not call

| Tool | Why not |
|---|---|
| `connect_with_person` (`tools/person.py:194`) | NG-3. Also `destructiveHint`. |
| `get_inbox`, `get_conversation`, `search_conversations` (`tools/messaging.py:36,86,168`) | Only from FR-079's manual verification, and only on operator command: `get_conversation` and `search_conversations` are **not** `readOnlyHint` because enumerating rows marks threads read (`tools/messaging.py:78-79`, `:182`). The UI states this before the click. |
| `get_sidebar_profiles`, `get_company_employees`, `search_companies`, feed/post/job tools | Parking lot (§28). Not mapped to any FR. |
| `close_session` (`server.py:308`) | Operator-only "release browser" button; harmless but not required. |

---

## 10. Proposed project directory structure

The companion app lives **outside** `linkedin-mcp-server` (see D-01) as a sibling checkout:

```
linkedin-dashboard/
├─ README.md
├─ PROJECT_PLAN.md                    ← this document
├─ pyproject.toml                     ← uv-managed, mirrors the server's tooling (ruff, pytest)
├─ .env.example                       ← MCP_URL, DB_PATH, LLM_PROVIDER, SEND_ENABLED=false
├─ backend/
│  └─ linkedin_dashboard/
│     ├─ __init__.py
│     ├─ main.py                      ← FastAPI app, loopback bind assertion (NFR-001)
│     ├─ settings.py                  ← pydantic-settings; never serialized to the UI
│     ├─ api/
│     │  ├─ briefs.py  searches.py  candidates.py  enrichment.py
│     │  ├─ scoring.py  drafts.py  sending.py  session.py  events.py (SSE)
│     ├─ services/
│     │  ├─ brief.py
│     │  ├─ search.py                 ← reference → candidate normalization
│     │  ├─ enrichment.py             ← stage planning, resume-after-rate-limit
│     │  ├─ scoring/
│     │  │  ├─ types.py  matching.py  aggregate.py
│     │  │  └─ signals/               ← pure; no DB/API/MCP/network/clock/RNG
│     │  ├─ scoring_persist.py         ← snapshots, history, evidence/coverage rows
│     │  ├─ drafting.py               ← grounding check
│     │  └─ sending.py                ← ONLY module with confirm_send=True
│     ├─ queue/
│     │  ├─ worker.py                 ← single asyncio task, concurrency 1
│     │  └─ jobs.py                   ← job types, retry policy (never for send)
│     ├─ mcp/
│     │  ├─ client.py                 ← transport, timeouts, reconnect
│     │  ├─ tools.py                  ← typed wrappers, one per tool used
│     │  ├─ envelope.py               ← {url, sections, references, section_errors} parsing
│     │  └─ errors.py                 ← ToolError string → ErrorClass mapping
│     ├─ parsing/
│     │  ├─ spans.py  verify.py       ← VerifiedSpan constructor lives here
│     │  ├─ main_profile.py  experience.py  skills.py  education.py
│     │  ├─ projects.py  certifications.py
│     │  └─ aliases.py
│     ├─ llm/
│     │  ├─ protocol.py               ← LLMProvider Protocol
│     │  ├─ null.py  anthropic.py  openai_compatible.py  ollama.py
│     │  └─ prompts/
│     ├─ db/
│     │  ├─ models.py  session.py  migrations/  retention.py
│     └─ audit.py
├─ frontend/
│  ├─ index.html  vite.config.ts  tsconfig.json
│  └─ src/
│     ├─ main.tsx  App.tsx  routes.tsx
│     ├─ api/client.ts  hooks/
│     ├─ pages/ BriefPage SearchPage CandidatesPage CandidateDetailPage
│     │        ShortlistPage OutreachPage OutreachDetailPage SettingsPage AuditPage
│     ├─ components/ CandidateRow ScoreBadge ConfidenceBand EvidencePanel
│     │             RawTextViewer SignalTable DraftEditor CharCount
│     │             SendConfirmationModal SendStateBadge FallbackActions
│     │             QueueStatus EmptyState ErrorState LoadingSkeleton PhaseGateBanner
│     └─ state/
└─ tests/
   ├─ unit/  contract/  integration/  ui/  e2e/
   └─ fixtures/            ← recorded MCP responses, redacted
```

---

## 11. Database schema

SQLite, WAL, file `~/.linkedin-dashboard/session.db`, mode `0600`. All timestamps UTC ISO-8601.

```sql
-- Session -------------------------------------------------------------------
session(id PK, created_at, label, purge_after, nav_budget, nav_used,
        send_enabled BOOL DEFAULT 0)

phase_gate(id PK, session_id FK, gate CHECK(gate IN ('A','B','C')),
           accepted_at, accepted_note,
           UNIQUE(session_id, gate))

phase_gate_evidence(phase_gate_id FK, evidence_id FK,
                    PRIMARY KEY(phase_gate_id, evidence_id))
-- Gate B only. Every linked row is revalidated as current, same-session,
-- profile-span evidence under a matched/contradicted score_claim when Gate B is inserted;
-- coverage/context cannot link.

-- Brief ---------------------------------------------------------------------
role_brief(id PK, session_id FK, version INT, created_at, superseded_at NULL,
           job_description TEXT, target_titles JSON, location TEXT,
           industries JSON, positive_keywords JSON, negative_keywords JSON,
           message_tone TEXT, required_experience_months INT NULL
              CHECK(required_experience_months >= 0),
           UNIQUE(session_id, version))

brief_skill(id PK, brief_id FK, term TEXT, kind CHECK(kind IN ('required','optional')),
            aliases JSON)

brief_credential(id PK, brief_id FK, term TEXT, aliases JSON)

scoring_config(id PK, session_id FK, version INT, created_at,
               weights JSON, metro_region_equivalences JSON,
               superseded_at NULL, UNIQUE(session_id, version))
-- Immutable after insert. S-7 is not a configurable weight; validation rejects
-- any S-7 key or any attempt to make search context contribute to scoring. Service and
-- insert-trigger validation reject positive S-8 with no current credentials. If the normalized
-- brief has >=1 input-active scoring signal, they also reject a prospective effective scorable
-- weight sum <= 0 before any version/staleness/rescore write. An all-inert brief is the deliberate
-- exception: its configuration may be versioned even though no weight is presently effective.

-- Search --------------------------------------------------------------------
search_run(id PK, session_id FK, brief_id FK, created_at,
           keywords TEXT, location TEXT NULL, network JSON NULL,
           current_company TEXT NULL,
           result_url TEXT, raw_response JSON,          -- verbatim tool result
           reference_count INT, person_reference_count INT,
           status CHECK(status IN ('ok','partial','rate_limited','failed')))

candidate_ref(id PK, search_run_id FK, kind TEXT, url TEXT,
              text TEXT NULL, context TEXT NULL, value TEXT NULL,
              position INT)                              -- order as returned

-- Candidate -----------------------------------------------------------------
candidate(id PK, session_id FK, username TEXT, profile_url TEXT,
          display_name TEXT NULL, profile_urn TEXT NULL,
          first_seen_at, stage CHECK(stage IN ('discovered','stage1','stage2')),
          retrieval_status CHECK(retrieval_status IN
             ('pending','ok','partial','rate_limited','failed')),
          UNIQUE(session_id, username))                  -- dedupe key (FR-014)

candidate_source(candidate_id FK, search_run_id FK, candidate_ref_id FK,
                 PRIMARY KEY(candidate_id, search_run_id))

-- Retrieval -----------------------------------------------------------------
profile_fetch(id PK, candidate_id FK, job_id FK, tool TEXT,
              requested_sections JSON, args JSON,
              started_at, finished_at, duration_ms INT,
              outcome CHECK(outcome IN ('ok','partial','rate_limited','error')),
              raw_response JSON,                          -- verbatim, before parsing
              returned_url TEXT NULL)

profile_section(id PK, candidate_id FK, fetch_id FK, section_name TEXT,
                raw_text TEXT, content_sha256 TEXT, retrieved_at, char_len INT,
                UNIQUE(candidate_id, section_name, fetch_id))
-- Latest row per (candidate, section) wins; older rows retained for provenance.
-- content_sha256 is computed from the exact stored bytes and immutable with raw_text.

section_error(id PK, candidate_id FK NULL, search_run_id FK NULL, fetch_id FK,
              section_name TEXT, error_type TEXT, error_message TEXT,
              extra JSON)          -- diagnostics MINUS the `runtime` block (NFR-002)

section_reference(id PK, candidate_id FK, section_name TEXT,
                  kind TEXT, url TEXT, text TEXT NULL, context TEXT NULL,
                  value TEXT NULL)

-- Normalization -------------------------------------------------------------
parsed_field(id PK, candidate_id FK, field_key TEXT, value TEXT,
             section_name TEXT, span_start INT, span_end INT, snippet TEXT,
             origin CHECK(origin IN ('deterministic','llm_verified','llm_unverified')),
             parser_version TEXT, created_at)
-- span_start/span_end index into profile_section.raw_text for section_name.

-- Scoring -------------------------------------------------------------------
score(id PK, candidate_id FK, brief_id FK, scoring_config_id FK,
      stage CHECK(stage IN ('provisional','enriched')),
      score REAL NULL, score_lower REAL NULL, score_upper REAL NULL,
      confidence REAL,
      confidence_band CHECK(confidence_band IN ('low','medium','high')) NULL,
      calculation_status CHECK(calculation_status IN ('scored','unknown')),
      active_signal_count INT CHECK(active_signal_count >= 0),
      all_inert_attested BOOL CHECK(all_inert_attested IN (0,1)),
      input_fingerprint TEXT, computed_at, superseded_at NULL, is_current BOOL,
      CHECK (
        (all_inert_attested = 1 AND active_signal_count = 0
          AND calculation_status = 'unknown'
          AND score IS NULL AND score_lower IS NULL AND score_upper IS NULL
          AND confidence = 0 AND confidence_band = 'low')
        OR
        (all_inert_attested = 0 AND active_signal_count > 0 AND (
          (calculation_status = 'unknown'
            AND score IS NULL AND score_lower IS NULL AND score_upper IS NULL
            AND confidence_band IS NULL AND confidence = 0)
          OR
          (calculation_status = 'scored'
            AND score IS NOT NULL AND score_lower IS NOT NULL AND score_upper IS NOT NULL
            AND 0 <= score_lower AND score_lower <= score
            AND score <= score_upper AND score_upper <= 100
            AND 0 < confidence AND confidence <= 1)))))
-- All-unknown profile availability has active signals but nullable score/bounds/band and
-- confidence 0. All-inert input is separately attested and uses the FR-040 low-band no-score form.

score_input_section(score_id FK, profile_section_id FK, content_sha256 TEXT,
                    PRIMARY KEY(score_id, profile_section_id))
-- Immutable source snapshot; hash must equal the linked profile_section hash.

score_signal(id PK, score_id FK,
             signal_id TEXT CHECK(signal_id IN ('S-1','S-2','S-3','S-4','S-5','S-6','S-8')),
             weight REAL CHECK(weight >= 0),
             rollup CHECK(rollup IN ('matched','not_matched','unknown','contradicted','mixed')),
             raw_subscore REAL, contribution REAL,
             availability REAL,          -- [0,1] → drives confidence
             note TEXT NULL)
-- signal_id is one of S-1..S-6 or S-8. S-7 is intentionally impossible here.

evidence_set(id PK, candidate_id FK)

evidence(id PK, evidence_set_id FK, parsed_field_id FK NULL,
         profile_section_id FK, span_start INT, span_end INT, snippet TEXT,
         matcher CHECK(matcher IN ('exact','alias','stem','llm_verified')),
         matched_term TEXT, polarity CHECK(polarity IN ('supporting','contradicting')))
-- Only exact profile-text spans. Insert triggers require 0 <= start < end <=
-- length(raw_text) and snippet == raw_text[start:end] on the same candidate/section.
-- Offsets are zero-based, half-open Unicode code-point indexes (the existing M3 contract),
-- and equality is also checked on the exact UTF-8 bytes to prevent normalization drift.

coverage_set(id PK, candidate_id FK, required_sections JSON)

signal_coverage(id PK, coverage_set_id FK, profile_section_id FK,
                content_sha256 TEXT, normalized_terms JSON, aliases JSON,
                matcher_version TEXT)
-- Used only for deterministic `not_matched`. Coverage requires every required
-- section to have completed successfully, stores no snippet, and never satisfies Gate B.

missing_set(id PK, candidate_id FK)

signal_missing_section(id PK, missing_set_id FK, section_name TEXT,
                       reason CHECK(reason IN ('not_requested','rate_limit','fetch_error','unparseable')),
                       section_error_id FK NULL)
-- Used only for `unknown`; stores availability provenance, never profile evidence.

score_claim(id PK, score_signal_id FK, claim_key TEXT, display_term TEXT,
            verdict CHECK(verdict IN ('matched','not_matched','unknown','contradicted')),
            evidence_set_id FK NULL, coverage_set_id FK NULL, missing_set_id FK NULL,
            CHECK ((evidence_set_id IS NOT NULL) + (coverage_set_id IS NOT NULL)
                   + (missing_set_id IS NOT NULL) = 1),
            CHECK ((verdict IN ('matched','contradicted') AND evidence_set_id IS NOT NULL)
                OR (verdict = 'not_matched' AND coverage_set_id IS NOT NULL)
                OR (verdict = 'unknown' AND missing_set_id IS NOT NULL)),
            UNIQUE(score_signal_id, claim_key))
-- Sets and their children are inserted first, then the claim. Claim-insert triggers require a
-- non-empty, same-candidate set of the verdict-compatible kind; coverage must contain every
-- required successfully retrieved section and matching hashes. Once referenced, a set and all
-- children are immutable. Before a score can become current, a trigger requires
-- count(score_signal) = active_signal_count and every persisted signal to have >=1 claim, then
-- requires rollup = the common verdict when count(DISTINCT verdict)=1, otherwise
-- rollup='mixed'. The only zero-signal/zero-claim exception requires `all_inert_attested=1`; the
-- same trigger re-derives all eight inert conditions from the linked immutable brief/config,
-- requires the exact FR-040 no-score fields, and rejects any score_input_section,
-- signal/provenance child or penalty contribution associated with that score. Claims are
-- returned by claim_key order. This makes missing, multi-kind and empty
-- provenance states impossible while permitting only the explicitly attested all-inert case.

-- Review --------------------------------------------------------------------
shortlist_decision(id PK, candidate_id FK, decision
                   CHECK(decision IN ('shortlist','reject','undecided')),
                   note TEXT NULL, decided_at)   -- append-only; latest row is current

-- Drafting ------------------------------------------------------------------
message_draft(id PK, candidate_id FK, version INT, body TEXT,
              body_sha256 TEXT, char_count INT,
              generator CHECK(generator IN ('llm','template','manual')),
              grounding_status CHECK(grounding_status IN ('pass','warn','overridden')),
              grounding_report JSON, created_at,
              UNIQUE(candidate_id, version))

draft_claim(id PK, draft_id FK, claim_text TEXT,
            evidence_id FK NULL, grounded BOOL)

-- Sending -------------------------------------------------------------------
send_confirmation(token PK, candidate_id FK, draft_id FK, body_sha256 TEXT,
                  created_at, expires_at, consumed_at NULL)
-- Single use: consumption is `UPDATE ... SET consumed_at=? WHERE token=? AND
-- consumed_at IS NULL` and must affect exactly 1 row.

send_attempt(id PK, candidate_id FK, draft_id FK,
             idempotency_key TEXT NOT NULL UNIQUE,
             body_sha256 TEXT, confirm_send BOOL,
             state CHECK(state IN ('SENDING','SENT','FAILED_CONCLUSIVE','AMBIGUOUS','DRY_RUN_OK','DRY_RUN_FAILED')),
             tool_status TEXT NULL, tool_sent BOOL NULL,
             tool_recipient_selected BOOL NULL, tool_url TEXT NULL,
             raw_response JSON NULL, error_class TEXT NULL, error_message TEXT NULL,
             started_at, finished_at NULL,
             resolution CHECK(resolution IN ('unresolved','confirmed_sent','confirmed_not_sent')) DEFAULT 'unresolved',
             resolved_at NULL, resolution_note TEXT NULL,
             -- A verdict only ever attaches to an uncertain attempt.
             CHECK (resolution = 'unresolved' OR state = 'AMBIGUOUS'),
             CHECK (state <> 'AMBIGUOUS' OR finished_at IS NOT NULL),
             CHECK ((resolution = 'unresolved' AND resolved_at IS NULL AND resolution_note IS NULL)
                 OR (resolution <> 'unresolved' AND resolved_at IS NOT NULL)))

-- Which rows block a further dashboard send for a candidate [LD-01].
-- `unresolved` and `confirmed_sent` both block; only `confirmed_not_sent` releases,
-- and it releases by allowing a NEW row, never by mutating this one.
--
-- Note the parenthesisation: AND binds tighter than OR in SQL, so the earlier
-- draft of this predicate silently dropped `confirm_send = 1` from the AMBIGUOUS
-- branch and would have let a dry run block a real send.
CREATE UNIQUE INDEX one_live_send_per_candidate
  ON send_attempt(candidate_id)
  WHERE confirm_send = 1
    AND (
          state IN ('SENDING', 'SENT')
       OR (state = 'AMBIGUOUS' AND resolution IN ('unresolved', 'confirmed_sent'))
        );
-- The DB, not the UI, is what makes FR-077 true.

-- Every row starts unresolved with null resolution metadata, and an AMBIGUOUS
-- row must already carry a finished_at timestamp.
CREATE TRIGGER send_attempt_insert_is_valid
BEFORE INSERT ON send_attempt
FOR EACH ROW
WHEN NEW.resolution <> 'unresolved'
  OR NEW.resolved_at IS NOT NULL
  OR NEW.resolution_note IS NOT NULL
  OR (NEW.state = 'AMBIGUOUS' AND NEW.finished_at IS NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid initial send_attempt resolution state');
END;

-- A finished or AMBIGUOUS attempt is a permanent record. Only the three
-- resolution columns may ever be written after the fact [LD-01]. `IS NOT`
-- comparisons cover nullable columns; the implementation enumerates every
-- non-resolution column, including id and candidate_id.
CREATE TRIGGER send_attempt_is_immutable
BEFORE UPDATE ON send_attempt
FOR EACH ROW
WHEN (OLD.finished_at IS NOT NULL OR OLD.state = 'AMBIGUOUS')
 AND (   NEW.id              IS NOT OLD.id
      OR NEW.candidate_id    IS NOT OLD.candidate_id
      OR NEW.draft_id        IS NOT OLD.draft_id
      OR NEW.idempotency_key IS NOT OLD.idempotency_key
      OR NEW.body_sha256     IS NOT OLD.body_sha256
      OR NEW.confirm_send    IS NOT OLD.confirm_send
      OR NEW.state           IS NOT OLD.state
      OR NEW.tool_status  IS NOT OLD.tool_status
      OR NEW.tool_sent    IS NOT OLD.tool_sent
      OR NEW.tool_recipient_selected IS NOT OLD.tool_recipient_selected
      OR NEW.tool_url      IS NOT OLD.tool_url
      OR NEW.raw_response IS NOT OLD.raw_response
      OR NEW.error_class  IS NOT OLD.error_class
      OR NEW.error_message IS NOT OLD.error_message
      OR NEW.started_at   IS NOT OLD.started_at
      OR NEW.finished_at  IS NOT OLD.finished_at)
BEGIN
  SELECT RAISE(ABORT,
    'send_attempt is immutable once finished; only resolution/resolved_at/resolution_note may be written');
END;

-- A resolution is one atomic transition on an already-finished AMBIGUOUS row;
-- the companion finality trigger rejects later changes to any resolution field.
CREATE TRIGGER send_resolution_transition_is_valid
BEFORE UPDATE ON send_attempt
FOR EACH ROW
WHEN OLD.resolution = 'unresolved'
 AND (NEW.resolution IS NOT OLD.resolution
      OR NEW.resolved_at IS NOT OLD.resolved_at
      OR NEW.resolution_note IS NOT OLD.resolution_note)
 AND NOT (OLD.state = 'AMBIGUOUS' AND OLD.finished_at IS NOT NULL
          AND OLD.resolution = 'unresolved'
          AND NEW.resolution IN ('confirmed_sent','confirmed_not_sent')
          AND NEW.resolved_at IS NOT NULL)
BEGIN
  SELECT RAISE(ABORT, 'invalid send_attempt resolution transition');
END;

CREATE TRIGGER send_resolution_is_final
BEFORE UPDATE ON send_attempt
FOR EACH ROW
WHEN OLD.resolution <> 'unresolved'
 AND (NEW.resolution IS NOT OLD.resolution
      OR NEW.resolved_at IS NOT OLD.resolved_at
      OR NEW.resolution_note IS NOT OLD.resolution_note)
BEGIN
  SELECT RAISE(ABORT, 'send_attempt resolution is final');
END;

-- Infrastructure -------------------------------------------------------------
job(id PK, session_id FK, kind TEXT, payload JSON,
    state CHECK(state IN ('queued','running','done','failed','interrupted','cancelled')),
    attempts INT DEFAULT 0, max_attempts INT DEFAULT 1,
    queued_at, started_at NULL, finished_at NULL, error TEXT NULL,
    correlation_id TEXT)

audit_log(id PK, session_id FK, at, actor CHECK(actor IN ('operator','system')),
          action TEXT, subject_type TEXT, subject_id TEXT,
          detail JSON, correlation_id TEXT)   -- append-only, no UPDATE/DELETE
```

### Relationships

`session` 1—N `role_brief` 1—N `brief_skill` / `brief_credential`; `session` 1—N
`scoring_config`; `session` 1—N `search_run` 1—N `candidate_ref`;
`candidate` N—N `search_run` via `candidate_source`; `candidate` 1—N `profile_fetch` 1—N
`profile_section` / `section_error`; `candidate` 1—N `parsed_field`; `candidate` 1—N `score`
1—N `score_input_section`; `score` 1—N `score_signal` 1—N `score_claim`; every claim points to
exactly one of `evidence_set`, `coverage_set` or `missing_set`, whose children are respectively
`evidence`, `signal_coverage` or `signal_missing_section`; `evidence` N—1 `profile_section` and
optionally N—1 `parsed_field`;
`phase_gate` N—N exact profile-span `evidence` via `phase_gate_evidence` for Gate B only;
`candidate` 1—N `message_draft` 1—N `send_confirmation` / `send_attempt`.

### Retention rules

| Data | Default | Rule |
|---|---|---|
| `profile_section.raw_text`, `search_run.raw_response`, `profile_fetch.raw_response` | 30 days | Purged by `retention.py` on startup when `now > session.purge_after`, or on `DELETE /api/session/raw` |
| `parsed_field`, `evidence_set`/`evidence.snippet`, `coverage_set`/`signal_coverage`, `missing_set` details | 30 days | Purged with raw text — snippets and provenance-to-section chains are profile-derived |
| `score`, `score_signal`, `score_claim` (numeric and typed history; no snippets) | Kept | Survive raw purge; claim provenance becomes "raw text purged" and cannot later satisfy Gate B |
| `shortlist_decision`, `send_attempt`, `audit_log` | Kept until `DELETE /api/session` | The record that a message was sent is the one thing worth keeping |
| `send_confirmation` | 24 h | Tokens are swept regardless of consumption |
| `job.payload` | 7 days | Payload nulled; state kept |

A visible banner shows days remaining until purge. `DELETE /api/session` drops everything and
`VACUUM`s the file.

---

## 12. Backend API endpoints

Every path shown below is relative to the frozen base `http://127.0.0.1:8787/api`, including
`/health` (therefore health is `/api/health`). All responses pass through the NFR-002 filter. M4 may add
fields but must not rename these routes or change their stated request/response semantics.

### Session & health
| Method | Path | Responsibility |
|---|---|---|
| `GET` | `/health` | Backend liveness; DB writable; send gate state |
| `GET` | `/mcp/status` | MCP reachability: performs `tools/list`, returns tool names + last error class. **Never** exposes the MCP URL. |
| `GET` | `/session` | Current session, budget used/remaining, purge date, phase gates |
| `POST` | `/session/gates/A` | Body `{note}`. Record Gate A only when the session has a completed search; the operator note must be non-empty. |
| `POST` | `/session/gates/B` | Body `{evidence_ids:[...], note?}`. Record Gate B only after Gate A and with ≥10 distinct evidence ids from current scores in the same session. The transaction revalidates every id as exact profile-span evidence under a matched/contradicted claim; coverage, missing and search-context ids are invalid. |
| `POST` | `/session/gates/C` | Record Gate C under the M6/M7 preconditions. |
| `PATCH` | `/session/settings` | Nav budget, inter-call delay, promotion threshold, `send_enabled` (requires gate C) |
| `GET` | `/session/export` | JSON/CSV export (FR-091) |
| `DELETE` | `/session/raw` | Purge raw text only (FR-092) |
| `DELETE` | `/session` | Purge everything (FR-092) |
| `GET` | `/events` | SSE stream: job state changes, queue depth, cool-downs, errors |

### Brief
| Method | Path | Responsibility |
|---|---|---|
| `POST` | `/briefs` | Create v1. Validates against the protected-attribute blocklist (FR-004) → `422` listing offending terms. Empty scoring inputs and a positive-keyword-only brief are valid; candidates use the all-inert no-score lifecycle until a scoring criterion is added. |
| `GET` | `/briefs/current` | Current version + skills |
| `PUT` | `/briefs/current` | Create next version; marks current scores stale and returns the count so the UI can warn. Includes optional nonnegative `required_experience_months` and structured `required_credentials:[{term, aliases[]}]`. Removing the final credential also creates the next config with S-8=0 in the same transaction. A zero prospective effective weight sum returns `422` with no writes only when at least one normalized scoring input remains active; an all-inert result is valid and re-scores to the attested no-score form. |

### Scoring configuration
| Method | Path | Responsibility |
|---|---|---|
| `GET` | `/weights` | Return the immutable current scoring-config version, configured weights, derived `active_signal_ids`, typed `inert_reasons`, and metro/region equivalence table. S-7 is absent from weights and returned only as non-scoring context. Configured weights for inert S-1–S-6 remain visible but do not enter a score. |
| `PUT` | `/weights/current` | Body includes `expected_version`, weights, and metro/region equivalences. Optimistic-version mismatch → `409`; S-7, protected terms, positive S-8 with empty credentials, or no positive effective scorable weight while any scoring input is active → `422`, with no new version/staleness/rescore. If all scoring inputs are inert, the update succeeds, versions configuration, stales prior scores and re-scores each candidate exactly once into no-score without dividing. |

### Search
| Method | Path | Responsibility |
|---|---|---|
| `POST` | `/searches` | Enqueue a `search_people` job. Body: `{keywords, location?, network?[], current_company?}`. Validates `network ⊆ {F,S,O}` and `current_company` numeric **before** enqueueing → `422` with the server's own wording. Returns `{job_id, search_run_id}` |
| `GET` | `/searches` | List runs with counts and status |
| `GET` | `/searches/{id}` | One run: params, raw text, references, errors, candidates produced |
| `POST` | `/companies/urn-lookup` | Body `{slug}`. Enqueues `get_company_profile`, returns `{urn_id?, candidates:[{urn_id,text}], note}` |

### Candidates & enrichment
| Method | Path | Responsibility |
|---|---|---|
| `GET` | `/candidate-pool?session_id={id}` | Ungated discovery/enrichment pool. This is the only M4 list route usable before Gate A. |
| `GET` | `/candidates` | Ranked list, gated by Gate A. Query: `stage`, `decision`, `min_score`, `confidence`, `sort`. Returns nullable score/bounds, band, `calculation_status`, `active_signal_count`, confidence, stage, config version, delta and top signals — **not** raw text. All-inert returns `score/lower/upper:null`, `confidence:0`, `confidence_band:"low"`, `calculation_status:"unknown"`, `active_signal_count:0`, `top_signals:[]`; all-unknown profile availability retains its distinct null-band form. Stable default order: numeric score descending, confidence descending, candidate id ascending; both null-score forms sort last by candidate id. |
| `GET` | `/candidates/{id}` | Detail: parsed fields; aggregate signals with `rollup` and ordered `claims[]`; each claim's one typed profile-evidence/absence-coverage/missing-section provenance; typed non-scoring search context/messageability hints; section availability, score history and fetch history. An all-inert result returns `signals:[]`, `active_signal_count:0` and exact empty-state copy from §14.5, never fabricated unknown claims. |
| `GET` | `/candidates/{id}/sections/{name}` | Raw text for one section + parsed/profile-evidence spans to highlight. Coverage/context rows are not highlight spans. |
| `POST` | `/candidates/{id}/enrich` | Body `{sections:[...]}`. Validates against `PERSON_SECTIONS`; enqueues one `get_person_profile` job; returns `{job_id, estimated_navigations}`. `409` if a fetch for this candidate is already queued/running |
| `POST` | `/candidates/enrich-batch` | Body `{candidate_ids, sections}` — enqueues N jobs, still one at a time. Refuses if it would exceed the nav budget → `409` with the shortfall |
| `POST` | `/candidates/{id}/decision` | `{decision, note?}` → appends `shortlist_decision` |
| `POST` | `/candidates/{id}/rescore` | Re-run scoring from one immutable brief/config/source snapshot; preserves history and makes no MCP call. |

### Drafting
| Method | Path | Responsibility |
|---|---|---|
| `POST` | `/candidates/{id}/drafts` | Generate v1 (or next) from brief + evidence. `409` if not shortlisted; `409` if no `main_profile` (FR-061). Returns draft + grounding report |
| `PUT` | `/candidates/{id}/drafts/current` | Save operator edit → new version, recompute `body_sha256`, re-run grounding check |
| `GET` | `/candidates/{id}/drafts` | Version history |
| `POST` | `/candidates/{id}/drafts/current/override-grounding` | Records a justification, sets `grounding_status='overridden'` |

### Validation & sending
| Method | Path | Responsibility |
|---|---|---|
| `POST` | `/candidates/{id}/dry-run` | Calls `send_message(..., confirm_send=false)`. Writes a `send_attempt` with `confirm_send=0`. Returns the tool result verbatim |
| `POST` | `/candidates/{id}/send-confirmation` | Mints a single-use token bound to `(candidate_id, draft.body_sha256)`, TTL 300 s. `409` if a live send attempt exists. Returns `{token, expires_at, name, profile_url, body, char_count, body_sha256}` — the modal renders **only** from this response |
| `POST` | `/candidates/{id}/send` | **The only endpoint that can send.** Headers: `Idempotency-Key`. Body: `{token, body_sha256, reviewed: true}`. See §16/§17 for the full contract |
| `POST` | `/candidates/{id}/send/resolve` | Body `{resolution: 'confirmed_sent'\|'confirmed_not_sent', note}`. Only valid on an `AMBIGUOUS` attempt whose `resolution` is still `unresolved` (FR-079). Writes **only** `resolution`, `resolved_at`, `resolution_note` — never `state`. Does not create a new attempt; `confirmed_not_sent` merely permits one |
| `POST` | `/candidates/{id}/verify-thread` | Operator-initiated only. Calls `get_conversation(linkedin_username=…)`. Response includes the standing warning that this may mark threads read (`tools/messaging.py:78-79`) |

### Audit
| Method | Path | Responsibility |
|---|---|---|
| `GET` | `/audit` | Filterable append-only log |
| `GET` | `/jobs` | Queue view: queued/running/done/failed/interrupted |
| `POST` | `/jobs/{id}/cancel` | Cancels a **queued** job only. A running job is never cancelled mid-flight (it holds the browser lease) |

---

## 13. Candidate parsing and normalization strategy

### 13.1 Identity normalization (deterministic, no LLM)

1. Take `reference.url` from `references["search_results"]` where `kind == "person"` — a
   site-relative path like `/in/alice/`.
2. Extract the segment after `/in/`, apply the same rules the server applies
   (`normalize_person_identifier` `identifiers.py:307`): one percent-decode, refuse a second
   layer, refuse dot segments, refuse `me`.
3. Lowercase **only for the dedupe key**; send the original casing to the server, which
   preserves case deliberately (`identifiers.py` `_linkedin_segments` docstring).
4. `candidate.username` is `UNIQUE(session_id, username)` — that constraint *is* FR-014.
5. `reference.text` seeds `display_name`; the authoritative name comes from parsed
   `main_profile` once Stage 1 lands.

**We re-implement normalization rather than import it** so the companion app stays dependency-free
of the server package (D-01). A contract test (T-2.6) asserts our normalizer agrees with the
server's on a table of ~40 inputs lifted from `tests/test_identifiers.py`.

### 13.2 Section parsing

Each section gets a parser producing `(field_key, value, span)` tuples. All parsers are
**line-oriented over the raw innerText**, because that is what the server returns
(`tools/person.py:63-67`: "The LLM should parse the raw text in each section").

| Section | Fields | Approach |
|---|---|---|
| `main_profile` | `name`, `headline`, `location`, `about`, `current_title`, `current_company`, `connection_hint` | First non-empty line = name; the headline is the line below it; the location line is identified structurally by position, not by a country list. Ambiguity → `unknown`, never a guess. |
| `experience` | repeated `{title, company, date_range, duration, location, description}` | Split into blocks on the repeated title/company pattern; parse date ranges with a locale-tolerant matcher; derive `months` from ranges only when both endpoints parse. |
| `skills` | `skill[]` | One per line; endorsement counts stripped. |
| `education` | `{institution, degree, field, dates}` | Block split, same shape as experience. |
| `certifications` | `{name, issuer, issued, expires}` | Block split. |
| `projects` | `{name, dates, description}` | Block split. |

Every parser is **total**: it never raises. Reliably parsed content with no relevant value
produces zero relevant fields, keeps its raw text, receives full retrieved availability, and is
eligible for deterministic `not_matched`. Content marked unreliable by a `parse_note` also
produces zero fields and keeps its raw text, but produces canonical `unparseable` missing
provenance and reduced availability. It never produces absence coverage or `not_matched`, and
is never coerced to `fetch_error` (§14.4).

### 13.3 LLM-assisted extraction (optional, never authoritative)

When a provider is configured, a second pass asks the LLM to propose fields for text the
deterministic parsers left unclaimed. The prompt requires each proposal to include the exact
substring it came from. Then:

```
proposal.snippet in profile_section.raw_text  ?
  yes → parsed_field(origin='llm_verified'),  span computed from str.find
  no  → parsed_field(origin='llm_unverified'), display only, EXCLUDED from scoring
```

This is the single rule that makes "do not treat LLM output as verified profile data"
mechanically true rather than aspirational. `VerifiedSpan` has a private constructor reachable
only through `parsing/verify.py`, and `scoring/` accepts nothing else.

### 13.4 Matching terms to text

For a brief term `t` with aliases `A(t)`, against section text `s`:

1. `exact` — case-insensitive whole-token match of `t` or any `a ∈ A(t)`.
2. `alias` — same, recorded with the alias that hit.
3. `stem` — light suffix stripping (`-ing`, `-ed`, `-s`) plus a small hand-maintained
   equivalence table. **No fuzzy/edit-distance matching**: it produces evidence a human reads
   as wrong, which destroys the trust the evidence panel exists to build.
4. Word-boundary aware and Unicode aware (the server's own reference filter is Unicode-aware
   for the same reason — `link_metadata.py:120-126`).

Every hit stores `(section_name, span_start, span_end, snippet, matcher, matched_term)`.

---

## 14. Candidate scoring algorithm

### 14.1 Signals, inputs and weights (`scoring_config.version = 1`)

| ID | Signal | Weight | Sections required | Stage-1 available? |
|----|--------|--------|-------------------|--------------------|
| S-1 | Required-skill coverage | 30 | `skills` ∪ `experience` ∪ `main_profile` | Partial (0.5) |
| S-2 | Optional-skill coverage | 10 | same | Partial (0.5) |
| S-3 | Relevant experience depth | 20 | `experience` | Yes (1.0) |
| S-4 | Title / function similarity | 15 | `main_profile`, `experience` | Yes (1.0) |
| S-5 | Industry / domain relevance | 10 | `experience`, `main_profile` | Yes (1.0) |
| S-6 | Location fit | 8 | `main_profile` | Yes (1.0) |
| S-7 | Network / connection search context | **0 permanently** | *search parameters* (not a section) | Context only; excluded |
| S-8 | Credential requirement | 0 by default; configurable only when credentials exist | `education`, `certifications` | No (0.0) |

Signal activity is derived only from the normalized current brief, before availability or profile
text is examined. Trimmed empty primary terms are removed before the counts below; aliases do not
activate a missing primary term.

| Signal | Input-active condition | Input-inert condition and disposition |
|--------|------------------------|--------------------------------------|
| S-1 | At least one normalized required-skill term | No required skills: excluded from `I`, `W`, aggregation, confidence, persistence and claim/provenance requirements |
| S-2 | At least one normalized optional-skill term | No optional skills: same exclusion |
| S-3 | `required_experience_months > 0` | `null` or `0`: same exclusion |
| S-4 | At least one normalized target title | No target titles: same exclusion |
| S-5 | At least one normalized industry | No industries: same exclusion |
| S-6 | `location.strip()` is non-empty | Missing or blank target location: same exclusion; the metro table alone cannot activate it |
| S-7 | Never input-active | Always non-scoring search context; absent from weights, `I`, `W`, signals, claims, penalties, confidence and Gate B |
| S-8 | At least one normalized required credential | No required credentials: same exclusion and its configured weight is forced to 0 |

**Executable input-activity gate.** Start from one fixture with non-empty inputs for
S-1/S-2/S-4/S-5/S-6/S-8, positive S-3 months and at least one positive configured weight. Eight
independent cases then assert: empty required skills removes only S-1; empty optional skills only
S-2; both `null` and `0` months remove only S-3; empty titles only S-4; empty industries only
S-5; both absent and whitespace-only location remove only S-6; S-7 is absent in every case and
rejects any persisted/configured weight; empty credentials remove only S-8 and force its weight
to zero. Each case asserts the removed signal is absent from `I`, `W`, confidence math,
`score_signal`, claims/provenance and source-hash fingerprint membership while its canonical
empty input and config version remain in the fingerprint payload.

The terminal case uses the valid brief `{positive_keywords:["distributed systems"],
required_skills:[], optional_skills:[], required_experience_months:null, target_titles:[],
industries:[], location:"   ", required_credentials:[]}` with otherwise valid metadata and
default configured weights. It must save, derive `I=∅`, persist/return the exact FR-040
no-score form and create zero associated source/signal/claim/provenance rows. Repeating it with
S-3=`0` is byte-identical after canonicalization except for explicitly versioned identity.

Positive keywords are search terms, not a scoring signal, and negative keywords are penalties,
not an activation condition. Consequently a positive-keyword-only brief has every scoring signal
input-inert and is valid. No synthetic default term or signal is created to make it scoreable.

The versioned brief supplies S-3's optional nonnegative `required_experience_months` and S-8's
structured `required_credentials[{term, aliases[]}]`. With at least one credential the operator
may assign S-8 a nonnegative weight, but version 1 does not silently redistribute S-7's former
seven points. Education is never a general-purpose ranking signal (§14.6).

Let `I` be the input-active scorable signals from the table (S-1–S-6/S-8), including any whose
configured weight is zero. Every inert signal is absent from `I` and produces no `score_signal`,
`score_claim`, evidence, coverage or missing-section row. When `I` is non-empty, the prospective
brief/config pair must give at least one member a positive effective weight (`W > 0`); otherwise
the API and service return `422` before writing a brief/config version, staleness marker or
rescore. When `I` is empty, that guard is deliberately not applied: the brief and later weight or
equivalence edits are valid, are versioned, and produce the all-inert no-score result without
division. A positive S-8 weight with no credentials remains a structural `422`.

Removing the final credential is one transaction: create the new brief, create the new config
with S-8 forced to 0, stale prior scores and rescore each candidate once. Re-adding a credential
leaves S-8 at 0 until the operator explicitly sets a new weight; an old weight never resurrects.
The prospective-positive-weight check runs after the forced S-8 transition. It rolls back the
entire transaction only when another input-active signal remains and `W=0`; if the transition
makes `I` empty, it commits and produces the all-inert no-score result.

S-6 reads the metro/region equivalence table from the same immutable, versioned scoring
configuration as the weights. An empty table means exact-only matching. Editing the brief,
weights, or equivalence table changes a versioned input, marks earlier scores stale, and makes
the next calculation use a new input fingerprint.

Penalties (applied after normalization, not weighted):

| ID | Penalty | Range |
|----|---------|-------|
| P-1 | Negative-keyword hit in retrieved text | −3 per distinct term, capped at −15 |
| P-2 | Contradictory evidence (e.g. brief requires ≥5 yrs, parsed experience totals 1 yr with full date coverage) | −5 per contradiction, capped at −10 |

### 14.2 Sub-scores

- **S-1 / S-2:** when input-active, `matched_terms / total_terms`. Each brief term produces one ordered
  `score_claim`: a hit in any retrieved section is `matched` with exact evidence; no hit is
  `not_matched` only when every section that could contain it completed successfully, with
  complete coverage; otherwise it is `unknown` with the missing-section reasons. One aggregate
  signal may therefore contain matched, not-matched and unknown claims concurrently.
- **S-3:** when `required_experience_months > 0`,
  `min(1.0, relevant_months / required_experience_months)`, where a role counts as relevant when
  its title or description matches a normalized target-title or required-skill term. If there
  are no normalized target-title or required-skill terms, every parsed role is relevant; an
  empty relevance-filter term set never creates absence coverage. Optional skills, positive
  keywords and job-description prose are not S-3 relevance filters. Only relevant roles whose
  duration does not parse contribute to `unparsed_roles` and reduce availability; an
  unparseable duration on an irrelevant role does neither. If no roles parse reliably, the S-3
  claim is `unknown` with canonical `unparseable` missing provenance, never absence coverage or
  `not_matched`. When the input is `null` or `0`, S-3 is inactive and excluded from every
  denominator.
- **S-4:** when input-active, best-match over target titles using token overlap of head nouns (e.g. "Staff Backend
  Engineer" vs "Backend Engineer" → 0.8). Exact 1.0, no match 0.0.
- **S-5:** when input-active, fraction of brief industries evidenced by employer names or description text.
- **S-6:** when input-active, 1.0 exact location match, 0.6 same metro/region under the current versioned
  operator-editable equivalence table, 0.0 otherwise, `unknown` when the location line did not
  parse. An empty equivalence table permits exact matches only.
- **S-7:** there is no S-7 sub-score. The `F`/`S`/`O` filters from every producing search are
  returned as typed **search context** with provenance to `search_run`; they are not evidence,
  never receive a verdict or availability, and are excluded from aggregation, bounds, penalties
  and confidence. `profile_urn` presence is a separate non-scoring messageability hint (A-10).
  Both are labelled as hints/context and guarded against scoring by T-4.7a/T-4.7b
  (FR-047/FR-048).
- **S-8:** when input-active, fraction of required credential terms with exact/alias
  `VerifiedSpan` evidence. It is inert, absent from persistence and forced to configured weight 0
  when no required credentials exist.

### 14.3 Aggregation

```
I = input-active scorable signals under the §14.1 matrix (S-7 and every inert signal excluded)
w_i >= 0,  a_i in [0,1],  s_i in [0,1]
active_signal_count = |I|

when I = ∅:                              # valid all-inert brief; stop before W or P
  score = score_lower = score_upper = null
  confidence = 0
  confidence_band = low
  calculation_status = unknown
  active_signal_count = 0
  persist no score_signal/score_claim/evidence/coverage/missing rows
  evaluate and persist no penalty contribution

otherwise:
  W = Σ_{i∈I} w_i                        # prospective guard requires W > 0
  if W = 0: return 422 atomically              # no version/stale/rescore write; never divide
  x = Σ_{i∈I} w_i · a_i · s_i
  y = Σ_{i∈I} w_i · a_i
  u = Σ_{i∈I} w_i · (a_i · s_i + 1 - a_i)
  P = sum of the explicit nonnegative penalties
  C(z) = min(100, max(0, z))
  Q(z) = Decimal(z).quantize(0.000001, ROUND_HALF_EVEN)  # shared monotone quantizer

  when y > 0:
    score       = Q(C(100 · x / y - P))
    score_lower = Q(C(100 · x / W - P))
    score_upper = Q(C(100 · u / W - P))
    confidence  = Q(y / W)
    confidence_band = low | medium | high under §14.4
    calculation_status = scored

  when y = 0:
    score = score_lower = score_upper = confidence_band = null
    confidence = 0
    calculation_status = unknown
```

The `I=∅` branch precedes construction of `W`, `x`, `y`, `u` and `P`; no code path can evaluate
`0/0`. Negative-keyword and contradiction penalties are neither applied nor persisted as ranking
contributions when there is no active scoring signal. The no-score row itself is still persisted
to make lifecycle and API behavior deterministic, but its database attestation must prove the
eight §14.1 dispositions and the absence of every signal/provenance child.

For `I≠∅`, every input-active signal has one `score_signal` row even when its configured weight
is zero; at least one member has positive weight because `W=0` was rejected. Every numeric result
is deterministically quantized only after applying the same `P` and `C` to all three score forms.
The headline normalizes over available weight; the lower bound credits only observed contribution
against all effective weight; the upper bound additionally treats each unavailable fraction
`(1-a_i)` as a possible full match. Thus fractional availability is bounded correctly.

The invariant follows before penalty/clamp: `0 <= x <= y <= W`, so `x/W <= x/y`. Also
`u=x+W-y`, and `u/W-x/y = (W-y)(y-x)/(Wy) >= 0`; therefore
`x/W <= x/y <= u/W`. Subtracting the same penalty and applying monotone `C` then `Q` preserves the order.
Property tests cover the earlier counterexample (`w=[1,1]`, `a=[0.5,1]`, `s=[1,0]` gives
`25 <= 33.333… <= 50` before penalties) plus random fractional availabilities and penalty/clamp
boundaries. All-unknown profile availability remains nullable rather than a misleading zero and
is distinguished from all-inert by `active_signal_count>0`, `all_inert_attested=0` and a null
band. All-inert uses the explicit low-band no-score form requested in FR-040. Removing S-7
redistributes nothing because W is simply the sum of the remaining input-active weights.

**Fingerprint and deterministic lifecycle.** `input_fingerprint` is SHA-256 over canonical JSON
containing the algorithm version, candidate id, only the ordered source-section ids/hashes
consumed by members of `I`, brief id/version, normalized values for **every** signal input
(including canonical empty arrays, S-3 `null`/`0`, blank S-6 and empty S-8), scoring-config
id/version, every configured weight and the normalized metro table, plus the derived ordered `I`.
An inert signal contributes no source hash, runtime subscore, availability, claim or provenance
material; its normalized input/config state and version identity are retained so brief/config
versions never alias by accident. When `I=∅`, both the fingerprint source list and
`score_input_section` set are empty. Replay of the same immutable inputs is byte-identical.

Every accepted brief or config edit creates its immutable next version, stales the old current
score rows, and performs exactly one local rescore per candidate. This includes all-inert →
all-inert edits and weight/equivalence edits while `I=∅`: each produces a new fingerprint and
a new attested no-score row, without signal or penalty rows. All-inert → active derives `I` from
the new brief and requires `W>0`; active → all-inert is allowed and emits no-score; active →
active with `W=0` is rejected atomically. Enrichment/source changes schedule a rescore only when
a changed section is consumed by current `I`; with `I=∅` they cannot alter the fingerprint or
create duplicate no-score history. No transition invokes MCP.

### 14.4 Confidence

```
confidence = y / W        ∈ [0,1]
band = low (<0.5) | medium (0.5–0.8) | high (≥0.8)
```

`availability_i` is `1.0` if every section the signal needs was retrieved successfully and can
be parsed reliably, `0.5` if some were, `0.0` if none. A section that was retrieved and parsed
reliably but contained no relevant value counts as retrieved (availability 1.0) with sub-score 0
and a `not_matched` scalar claim — because we *did* look and did not find it. A section that
errored, or that was retrieved but cannot be parsed reliably, contributes missing-section
provenance (`fetch_error` or `unparseable`, respectively) and reduces availability. An
`unparseable` section is never coerced to `fetch_error` or `not_matched`.

When `I≠∅` and `y=0` because no effective signal has retrieved availability, confidence is
exactly 0 and `confidence_band` is null. When `I=∅`, confidence is also 0 but the explicit API/UI
band is `low`; `active_signal_count` and the attestation distinguish the states.
Stage 1 is `provisional`; Stage 2
with its selected sections complete is `enriched`.

### 14.5 Evidence model

```
ScoreSignal {
  signal_id      : "S-1"
  rollup         : matched | not_matched | unknown | contradicted | mixed
  raw_subscore   : 0.5
  availability  : 0.5
  claims         : ScoreClaim[]
}

ScoreClaim {
  claim_key      : "required-skill:kubernetes"
  display_term   : "kubernetes"
  verdict        : matched | not_matched | unknown | contradicted
  provenance     : exactly one of EvidenceSet | CoverageSet | MissingSet
}

ProfileEvidence {
  matched_term   : "kubernetes"
  matcher        : exact | alias | stem | llm_verified
  profile_section_id : 42
  content_sha256 : "…"
  span           : (1204, 1214)
  snippet        : "…migrated the platform to Kubernetes across three…"
  polarity       : supporting | contradicting
}

AbsenceCoverage {
  verdict        : not_matched
  profile_section_id : 42
  content_sha256 : "…"
  normalized_terms : ["kubernetes"]
  aliases        : ["k8s"]
  matcher_version: "v1"
}

MissingSection {
  verdict        : unknown
  section_name   : "skills"
  reason         : not_requested | rate_limit | fetch_error | unparseable
  section_error_id : 7 | null
}
```

S-1/S-2 (and S-8 when active) emit one claim per ordered brief term; scalar S-3/S-4/S-5/S-6
emit one claim. Signal `rollup` is the common child verdict when all claims agree and `mixed`
otherwise; numeric subscore/contribution never derives from the rollup label. This is the exact
rule that represents simultaneous matched/not-matched/unknown terms without discarding them.

The provenance types cannot substitute for one another. A `matched` or `contradicted` claim
references exactly one non-empty `EvidenceSet` of `ProfileEvidence` rows constructed from exact
`VerifiedSpan` values. A `not_matched` claim references exactly one `CoverageSet`, valid only
after every section required by that claim completed successfully; it contains one
`AbsenceCoverage` row per searched section, has no span/snippet and is never presented as a
match. An `unknown` claim references exactly one non-empty `MissingSet`. Schema checks encode the
verdict-to-set mapping; triggers validate set content/candidate lineage and make referenced sets
immutable. Search context and messageability hints remain outside signals and claims.

The candidate detail view renders, for every signal:

- **What matched/contradicted** — per-claim term, quoted snippet, and the section it came from.
- **Where it was found** — clicking the snippet scrolls the raw-text viewer to the span and
  highlights it. The link never breaks: spans index into stored raw text, and if that text was
  purged the UI says *"raw text purged on {date}"*.
- **What did not match** — deterministic absence results, listed with the exact successfully
  retrieved sections and searched normalized terms/aliases; never as quoted snippets.
- **What was unavailable** — claims with verdict `unknown`, each naming the section that was
  unavailable for reliable scoring and *why*: not requested, rate limited, fetch failed, or
  **"retrieved, but could not be parsed reliably"** (`unparseable`).
- **Provisional or enriched** — the stage badge, plus "N of 6 sections retrieved".

Copy rule, enforced in one shared component: `unknown` always renders as
**"not found in the retrieved data"** with a tooltip *"This does not mean the candidate lacks
this qualification."* No other string is permitted for that verdict (asserted by a UI test).

An all-inert result contains no claim verdict, so it does not fabricate an `unknown` claim or use
the FR-042 claim copy. The ranked row renders **"Not scored — no active scoring criteria"** and
the detail/evidence empty state renders exactly **"Add a required or optional skill, experience
minimum, target title, industry, target location, or required credential to calculate a score."**
It also renders **"Low confidence (0%)"** from the explicit low band and labels positive keywords
**"Search only — not a scoring criterion."** The weights screen leaves configured S-1–S-6 values
editable and labels each input-inert row **"Saved, not currently applied: brief input is empty."**
S-8 remains disabled/forced to zero until a credential exists; S-7 is context and has no weight
control. These strings and `signals:[]` are frozen UI/API fixtures.

### 14.6 Protected-attribute exclusion

Never used as a signal, never requested from the LLM, never displayed as a ranking factor:
race, ethnicity, national origin, immigration status, gender, gender identity, sexual
orientation, age or birth year, graduation years used as an age proxy, disability, health,
pregnancy or family status, marital status, religion, political affiliation, union membership,
criminal history, photographs.

Enforcement:
- A `PROTECTED_TERMS` blocklist rejects brief terms at save time (FR-004).
- `interests` and `honors` are available for retrieval but excluded from scoring by default —
  they are the sections most likely to carry protected signals — and any weight assigned to
  them requires an explicit operator override with a recorded justification.
- Profile photos are never fetched or displayed.
- Pure-kernel and boundary tests (T-4.7a/T-4.7b) assert no signal definition, alias table, or LLM prompt contains a
  `PROTECTED_TERMS` entry, and that `education` contributes weight only via `S-8` with an
  explicit brief credential.

---

## 15. UI routes, screens, components and states

| Route | Screen | Purpose |
|---|---|---|
| `/` | Session dashboard | Gates, budget, queue, MCP status, purge countdown |
| `/brief` | Role brief editor | FR-001…FR-004 |
| `/search` | Search runner | Params, run history, raw results, references, errors |
| `/candidates` | Ranked pool | FR-050; filter/sort; bulk-enrich selection |
| `/candidates/:id` | Candidate detail | FR-051; evidence panel + raw viewer + fetch history |
| `/shortlist` | Shortlist board | Shortlisted / rejected / undecided columns |
| `/outreach` | Outreach list | Shortlisted candidates with draft + send state |
| `/outreach/:id` | Draft workspace | Editor, grounding report, dry run, send, fallback |
| `/settings` | Settings | Delays, budget, weights, LLM provider, send gate, retention |
| `/audit` | Audit & jobs | Append-only log, queue, exports, purge controls |

### Key components

`PhaseGateBanner` · `QueueStatus` (position, current job, cool-down countdown) ·
`CandidateRow` (name, headline, score, `ScoreBand`, `ConfidenceBand`, stage badge, top 3 signals) ·
`SignalTable` · `EvidencePanel` · `RawTextViewer` (span highlight, purge notice) ·
`SectionAvailabilityMap` (6 chips: retrieved / errored / not requested) ·
`DraftEditor` + `CharCount` · `GroundingReport` · `DryRunBadge` ·
`SendConfirmationModal` · `SendStateBadge` · `FallbackActions` (Copy message / Open LinkedIn,
state-gated per the matrix below) ·
`EmptyState` / `LoadingSkeleton` / `ErrorState`.

### State coverage (every list and detail screen)

| State | Treatment |
|---|---|
| **Empty — no brief** | `/candidates`, `/outreach` show "Create a role brief first" + CTA. Search is disabled. |
| **Empty — no search yet** | "No searches run" + the search form inline. |
| **Empty — search returned no person references** | Shows the raw `search_results` text and the reference count by kind, so the operator can see *why* (e.g. the 15-cap was spent on non-person refs — R-01). |
| **Loading — queued** | Skeleton rows + "Queued, position N of M". Never a spinner with no position: the queue is serialized and waits are long. |
| **Loading — running** | Live per-section progress from the SSE stream (the server reports progress per section — `extractor.py:2117-2119`). |
| **Partial** | Candidate renders with a `SectionAvailabilityMap` showing which sections errored, and a "Retry missing sections" action that enqueues only those. |
| **Rate-limited** | Amber session banner with the server's own message (`extractor.py:92`), a cool-down countdown, and the queue paused. No auto-resume without a click. |
| **Auth expired** | Full-width blocking banner: "The MCP server has no valid LinkedIn session" + the exact `--login` command + a "Recheck" button. No dashboard action can fix it (§18). |
| **MCP unreachable** | Same treatment, with the connection error class only — never the URL. |
| **Error — tool error** | Inline card with the masked message; "Show details" reveals `error_type`/`error_message` but never the `runtime` block. |
| **Send states** | Six distinct badges (§16), each with its own explanatory copy and available actions. |
| **Budget exhausted** | Enrichment buttons disabled with "Navigation budget spent (120/120) — raise it in Settings". |

### The confirmation modal (FR-072…FR-075)

```
┌─ Send message ─────────────────────────────────────────────┐
│  To:      Alice Rivera                                     │
│  Profile: https://www.linkedin.com/in/alicerivera/         │
│                                                            │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Hi Alice — I saw you led the Kubernetes migration    │  │
│  │ at Acme (from your Experience section). We're …      │  │
│  └──────────────────────────────────────────────────────┘  │
│  742 characters                                            │
│                                                            │
│  Dry run: recipient resolved (confirmation_required) ✓     │
│                                                            │
│  [ ] I reviewed this message                               │
│                                                            │
│           [ Cancel ]        [ Send now ]  ← disabled       │
└────────────────────────────────────────────────────────────┘
```

Rules: rendered **only** from the `send-confirmation` response, never from client cache.
`Send now` enables on checkbox **and** hash equality. Enter never activates it. If the draft
changes in another tab, the hash mismatch closes the modal with "the message changed —
reopen to review it". Focus starts on Cancel.

### Fallback availability by send state (FR-080, LD-01)

The fallback is a *substitute for sending*, so it is offered exactly where sending has not
happened. Offering it after a send is an invitation to send twice; withholding it while a send is
uncertain would remove the operator's only way to check. Hence:

| Send state | Copy message | Open LinkedIn | Framing |
|---|---|---|---|
| `NO_DRAFT` | — (nothing to copy) | ✅ | "View profile" — neutral, not a fallback |
| `DRAFT` · `DRY_RUN_OK` · `DRY_RUN_FAILED` · `READY` | ✅ | ✅ | "Send this yourself instead" — the genuine fallback |
| `SENDING` | ❌ disabled | ✅ | A send is in flight; copying now can only cause a duplicate |
| `FAILED_CONCLUSIVE` | ✅ | ✅ | "Nothing was sent — send it yourself instead" |
| `AMBIGUOUS` (`unresolved`) | 🔒 **guarded** | ✅ **prominent** | "We could not confirm this send. Check LinkedIn." Open LinkedIn is the primary action. Copy is collapsed behind an explicit "I have verified nothing was sent — reveal the message" acknowledgement, which is logged to the audit trail |
| `AMBIGUOUS` (`confirmed_sent`) | ❌ not rendered | ✅ | "View conversation" — neutral. The message went out |
| `AMBIGUOUS` (`confirmed_not_sent`) | ✅ | ✅ | Fallback restored, alongside the unlocked "Send from dashboard" path |
| `SENT` | ❌ not rendered | ✅ | "View conversation" — neutral, **never** labelled as a fallback or a send action |

Two rules the component enforces regardless of state: the fallback never issues an MCP call and
never mutates a `send_attempt` row; and the string "send" appears in a fallback control's label
only in the rows above where sending has not occurred.

---

## 16. Manual message-send state machine

```
                         ┌─────────────┐
                         │  NO_DRAFT   │
                         └──────┬──────┘
                    generate draft (FR-060)
                                ▼
                         ┌─────────────┐   edit    ┌─────────────┐
                         │   DRAFT     │◄─────────►│  DRAFT(v+1) │
                         └──────┬──────┘           └─────────────┘
                 dry run (confirm_send=false, optional)
                                ▼
              ┌──────────────────────────────────────┐
              │  DRY_RUN_OK  (confirmation_required) │
              │  DRY_RUN_FAILED (any other status)   │──► advisory only,
              └──────────────────┬───────────────────┘    never blocks
                open confirmation modal → token minted
                                ▼
                         ┌─────────────┐
                         │ READY       │  (checkbox checked, hash matches)
                         └──────┬──────┘
                click "Send now" → token consumed atomically
                                ▼
                         ┌─────────────┐
                         │  SENDING    │  (row committed BEFORE the MCP call)
                         └──────┬──────┘
        ┌───────────────────────┼────────────────────────────┐
        ▼                       ▼                            ▼
  ┌──────────┐        ┌──────────────────┐          ┌──────────────────┐
  │  SENT    │        │ FAILED_CONCLUSIVE│          │    AMBIGUOUS     │
  │ terminal │        │ nothing was sent │          │  IMMUTABLE row.  │
  │ immutable│        │ row is immutable │          │  Never retried,  │
  └──────────┘        └────────┬─────────┘          │  never reopened, │
                               │                    │  never rewritten │
                      new confirmation token        └────────┬─────────┘
                      → a NEW attempt row                    │
                                                 operator writes `resolution`
                                                 (the row's state stays AMBIGUOUS)
                                                             │
                                              ┌──────────────┴──────────────┐
                                              ▼                             ▼
                                  resolution=confirmed_sent    resolution=confirmed_not_sent
                                  still BLOCKS a new send      UNLOCKS creating a new attempt
                                              │                             │
                                              ▼                             ▼
                                      (terminal for the         a NEW attempt row: new id,
                                       candidate)               new token, new idempotency key
                                                                → re-enters at DRAFT/READY and
                                                                  runs the FULL review flow
```

The three terminal boxes are **rows**, not UI states. Nothing above ever mutates one. A
subsequent send is always a *new row*; the history of every uncertain attempt survives intact.
That is the trade this design makes: the candidate is not permanently unsendable, but every
uncertain attempt is permanently auditable.

### Transition table

| From | Trigger | To | Guards |
|---|---|---|---|
| `NO_DRAFT` | `POST /drafts` | `DRAFT` | shortlisted; `main_profile` present |
| `DRAFT` | `PUT /drafts/current` | `DRAFT` (v+1) | recomputes `body_sha256`; invalidates any unconsumed token |
| `DRAFT` | `POST /dry-run` | `DRY_RUN_OK` \| `DRY_RUN_FAILED` | `confirm_send=false` hard-coded |
| `DRAFT` | `POST /send-confirmation` | `READY` (token exists) | no live attempt; send gate on; gate C accepted |
| `READY` | token expiry / draft edit | `DRAFT` | token invalidated |
| `READY` | `POST /send` | `SENDING` | token valid+unconsumed, hash matches, `reviewed=true`, no row violating `one_live_send_per_candidate` |
| `SENDING` | `status=="sent" && sent==true` | `SENT` | — |
| `SENDING` | `status ∈ {message_unavailable, recipient_resolution_failed, composer_unavailable, compose_interact_failed}` | `FAILED_CONCLUSIVE` | all return **before** `keyboard.type` (`extractor.py:5049`) |
| `SENDING` | `status == "send_unavailable"` | `AMBIGUOUS` | returned **after** typing + send click (`extractor.py:5074-5080`) |
| `SENDING` | timeout / transport error / `ToolError` with no status | `AMBIGUOUS` | we cannot know |
| `SENDING` | process crash (row left `SENDING` on restart) | `AMBIGUOUS` | swept at startup |
| `AMBIGUOUS` | `POST /send/resolve {confirmed_sent}` | **row unchanged** (`state` stays `AMBIGUOUS`); `resolution` set | two-step confirm; still blocks a new send via `one_live_send_per_candidate` |
| `AMBIGUOUS` | `POST /send/resolve {confirmed_not_sent}` | **row unchanged**; `resolution` set | two-step confirm; requires the operator to have opened "Open LinkedIn" or run the thread check first. Releases the index predicate, which permits a *new* attempt — it does not create one |
| `AMBIGUOUS` (resolved `confirmed_not_sent`) | new `POST /send-confirmation` | a **new** attempt: `DRAFT`/`READY` → … | full review flow again: new token, new `idempotency_key`, checkbox re-checked. The old row is untouched |
| `FAILED_CONCLUSIVE` | new `POST /send-confirmation` | a **new** attempt at `READY` | unchanged draft or a new version; the failed row is untouched |
| any | Copy message / Open LinkedIn | unchanged | fallback never touches state or MCP; availability is state-dependent (§15) |

**There is no automatic transition out of `AMBIGUOUS`, and no transition of any kind out of the
row itself.** No timer, no poll, no retry, no reopen. The only write a resolved attempt ever
receives is its one-time `resolution`, `resolved_at` and `resolution_note` — enforced by the
`send_attempt_is_immutable` and `send_resolution_is_final` triggers in §11, so this holds against
application bugs, not merely against intent.

---

## 17. Duplicate-send and idempotency protections

Seven independent layers. Any one of them alone would be a bug waiting to happen.

1. **Single-use confirmation token.** Minted per modal open, bound to
   `(candidate_id, body_sha256)`, TTL 300 s. Consumed by
   `UPDATE send_confirmation SET consumed_at=? WHERE token=? AND consumed_at IS NULL`
   — proceed only if `rowcount == 1`. A double-click therefore loses the second race inside
   SQLite, not inside JavaScript.

2. **Idempotency key.** `Idempotency-Key: sha256(candidate_id | body_sha256 | token)`, stored
   `UNIQUE` on `send_attempt`. A replay with the same key returns the **stored** result with
   `200` and never re-invokes the tool. The `token` term is what makes a *deliberate* second
   attempt (after `confirmed_not_sent`) distinguishable from a *replay* of the first: a new modal
   open mints a new token, so the key differs even when the message body is byte-identical
   [LD-01]. An unchanged body therefore does not collide with its own history.

3. **Partial unique index.** `one_live_send_per_candidate` (§11) makes a second live send for
   the same candidate a database error, independent of application logic. `unresolved` and
   `confirmed_sent` are inside the predicate; only `confirmed_not_sent` falls outside it.

4. **Write-ahead attempt row.** The `SENDING` row is committed **before** the MCP call. A crash
   mid-call leaves evidence; the startup sweep turns it into `AMBIGUOUS`. Without this, a crash
   would look like "never attempted" and invite a duplicate.

4b. **Immutability triggers.** `send_attempt_is_immutable` and `send_resolution_is_final` (§11)
   make "never rewrite the original attempt" a database guarantee rather than a code review
   convention. An attempt cannot be walked back to `DRAFT`, have its key reused, or have its
   verdict revised — the paths by which an audit trail usually rots.

5. **Hash equality at click time.** The modal posts the hash it displayed; the backend compares
   it to the current draft. Any drift → `409 Conflict`, modal closes.

6. **No retry, anywhere.** `job.max_attempts` for send jobs is `1` and the send path does not go
   through the queue's retry logic at all. A CI test asserts the send code path contains no
   retry/backoff construct.

7. **UI disable + optimistic lock.** The button disables on first click and TanStack Query's
   mutation is keyed so a second is dropped. This is the *least* important layer and is treated
   as cosmetic — the guarantees live in the database.

**Explicitly not idempotent, and honestly so:** LinkedIn itself. If layer 4 recorded `SENDING`
and the message actually went out, no key of ours can undo it. That is exactly why `AMBIGUOUS`
exists and why resolving it is a human decision.

**What these layers do and do not promise.** They guarantee the dashboard sends at most once per
*approved attempt*, and that every attempt is permanently recorded. They do not guarantee the
candidate receives at most one message ever — an operator who resolves `confirmed_not_sent`
incorrectly, or who uses the copy/paste fallback after a real send, can still produce a duplicate.
That residual risk is human and is addressed by evidence (§15's guarded fallback, the verification
action) rather than by a lock, because a lock strong enough to prevent it would also strand a
candidate whose message genuinely never arrived.

---

## 18. Authentication, expired session, partial results, timeouts, rate limits

### Error classification

The server masks details (`server.py:215`) and hands us `ToolError` strings shaped by
`raise_tool_error` (`error_handler.py:73`). We classify by structure first, string second:

| Class | Detection | UI treatment | Queue effect |
|---|---|---|---|
| `AUTH_REQUIRED` | Tool error text matching the session-expired/login guidance (`exceptions.py:22-31`), or `DockerHostLoginRequiredError` wording | Blocking banner + `uv run -m linkedin_mcp_server --login` | **Pause queue.** Every subsequent job would fail identically. |
| `BROWSER_BUSY` | `BrowserBusyError` wording from the middleware (`sequential_tool_middleware.py:113`) | Info toast | Re-queue this job once after a 30 s delay (not a *send* — sends never retry) |
| `BROWSER_SETUP` | `BrowserSetupInProgressError` / `BrowserSetupFailedError` (`exceptions.py:34-39`) | "Server is still installing Chromium" | Pause 60 s, then resume |
| `RATE_LIMIT` | `section_errors[*].error_type == "rate_limit"` (`extractor.py:204`) | Amber banner + cool-down countdown | Pause; exponential cool-down 5 → 15 → 45 min; operator can resume early |
| `INVALID_REFERENCE` | `InvalidReferenceError` wording (`core/exceptions.py:10`) | Mark candidate `unusable` with the server's correction text | Skip candidate, continue |
| `PROFILE_NOT_FOUND` | `ProfileNotFoundError` wording | Mark candidate `not_found` | Skip, continue |
| `TIMEOUT` | Our 240 s client timeout, or the server's 180 s tool timeout | Job `failed` with "timed out" | Retryable once for reads; **never** for sends |
| `TRANSPORT` | Connection refused/reset | MCP-unreachable banner | Pause, poll `/mcp/status` every 15 s |
| `UNKNOWN` | Anything else | Show masked message + "Report" (points at the server's issue flow) | Job `failed`, continue |

The mapping table lives in `mcp/errors.py` with the exact source strings and a test
(T-2.5) that pins each one to its originating exception class. **[requires verification]**:
the precise masked wording for each class must be captured from a live server once and stored
as fixtures — string matching is inherently brittle and this is where it will bite first.

### Expired session

The dashboard **never** attempts to log in. `handle_auth_error` (`dependencies.py:63`) may
trigger an interactive re-login on the *server* side; from our side that surfaces as an auth
error or a long-running call. We: pause the queue, show the banner with the exact command,
offer "Recheck" (which calls `tools/list` + a cheap read), and resume only on success. All
partial data already retrieved stays.

### Partial results

Partial is the **normal** case, not an exception path:
- A response with `sections` for some requested names and `section_errors` for others produces
  `profile_fetch.outcome = 'partial'` and one `profile_section` row per successful section.
- Scoring runs regardless; missing sections lower confidence (§14.4).
- The `SectionAvailabilityMap` makes the gap visible on every screen that shows a score.
- "Retry missing sections" enqueues a *new* fetch for only the missing names — respecting
  A-09's abort semantics rather than re-requesting the whole set.

### Timeouts

Client timeout 240 s > server tool timeout 180 s (A-12) so the server's own message wins.
A Stage-2 fetch of 4 sections at ~2 s nav delay plus load time can approach the server's
timeout; the UI therefore **defaults Stage 2 to at most 4 sections per call** and offers a
"split into two calls" toggle for heavy profiles (which the server itself recommends for heavy
sections — `tools/person.py:61-63`).

### Rate limits

On any `rate_limit`: pause the queue, start the cool-down, and record the remaining sections as
a follow-up job in `pending` (not `queued`) so nothing runs until the operator resumes.
The politeness delay (NFR-004) is additive to the server's own `_NAV_DELAY`.

---

## 19. Privacy and local-data handling

1. **Data stays on the machine.** SQLite at `~/.linkedin-dashboard/session.db`; the main DB and
   live WAL/SHM files are `0600`, never inside the repo, and never reached through a final-path
   symlink. A custom pre-existing parent keeps its mode. Exports go to a path the operator picks.
2. **The frontend gets no infrastructure detail.** NFR-002's response filter strips
   `section_errors[*].runtime`, which contains `source_profile_dir`, `portable_cookie_path`,
   `hostname` and `suggested_gist_command` (`error_diagnostics.py:60-76`). The same final filter
   sanitizes JSON and SSE, including split events and credential-bearing URLs inside arbitrary
   strings. Tests assert no API response body contains `.linkedin-mcp`, the operator's home path,
   a `runtime` block, or URL userinfo.
3. **The LLM boundary is the real privacy decision.** Sending profile text to a hosted model
   is a disclosure of third-party personal data. Therefore:
   - The default provider is `NullProvider` — **nothing leaves the machine**.
   - Enabling a hosted provider requires an explicit settings toggle with a plain-language
     warning naming what is sent (profile text excerpts, the brief).
   - A local provider (Ollama) is offered as the recommended middle path.
   - Whatever the provider, only the **sections needed for the task** are sent, never the whole
     candidate record, and never `contact_info`.
   - Every LLM call is audit-logged with provider, model, and a hash of the payload.
   The through-M5 outcome is locked by **D-02**; provider choice for M6 remains deferred.
4. **`contact_info` is opt-in per candidate**, never fetched in bulk, and excluded from scoring
   and from every LLM prompt. It is the most sensitive section available.
5. **Purpose limitation.** The stored data supports one sourcing session. No training, no
   analytics, no telemetry, no crash reporting to any external service.
6. **Deletion is real.** `DELETE /api/session` removes rows and runs `VACUUM`, so the file does
   not retain purged text in free pages. Tested (T-7.5).
7. **Audit retains decisions, not people.** After a raw purge, the audit log holds usernames,
   timestamps and outcomes — not profile text.
8. **Operator notice.** A first-run screen states plainly: this tool reads profiles the operator
   can already see with their own LinkedIn account, stores them locally, and sends nothing
   anywhere except the messages the operator personally sends.

---

## 20. Testing strategy

### 20.1 Unit tests

| Area | What is asserted |
|---|---|
| `parsing/` | Each section parser against recorded raw text fixtures; spans point at the exact substring; parsers never raise; unparseable input yields zero fields + a note |
| `parsing/verify.py` | An LLM proposal whose snippet is absent from raw text cannot produce a `VerifiedSpan` |
| `services/scoring/` | Exact §14.3 fractional-availability bounds and proof counterexample; all-unknown null score/confidence 0; mixed per-term claims/rollups; 100-run determinism and shuffled-input stability; the eight-row input-activity matrix; positive-keyword-only/all-inert no-score with low band/status unknown/count 0/no penalties; S-3/S-6/S-8 versioned inputs |
| Evidence persistence | Every claim has exactly one verdict-compatible provenance kind; exact code-point spans survive repeated substrings/astral Unicode; cross-candidate, wrong-section, off-by-one, empty, purged and stale spans fail; coverage/context cannot satisfy Gate B; zero signals/claims is accepted only for the database-attested all-inert no-score form |
| Protected/non-scoring/config guards | No signal/alias/prompt contains a `PROTECTED_TERMS` entry; S-7/network/`profile_urn`/messageability cannot affect scoring; invalid S-8 and zero-effective-weight transitions with active input return `422` without writes; all-inert weight edits version and rescore without `0/0` |
| Identity normalization | Agreement with the server's rules on a ported table from `tests/test_identifiers.py` |
| Idempotency | Token consumption is single-winner under concurrent calls; duplicate `Idempotency-Key` returns the stored result without a second tool call |
| Send classification | Each of the 7 statuses maps to the state in §16's table |

**Mutation discipline** (mirroring `AGENTS.md` § Tests): before committing any test, mutate the
code it covers and watch it fail. Specifically — flip `send_unavailable` from `AMBIGUOUS` to
`FAILED_CONCLUSIVE` and confirm a test fails; make the substring verifier always return true and
confirm a test fails; drop the partial unique index and confirm a test fails.

### 20.2 MCP contract tests

Two tiers.

**Tier 1 — static, always runs in CI.** Asserts our client's assumptions against the *source of
truth in the server repo*, so a server upgrade that breaks us is caught by a red test rather
than by a failed send:
- Tool names we call exist and their parameter names/types match: `search_people`
  (`tools/person.py:111`), `get_person_profile` (`tools/person.py:38`), `send_message`
  (`tools/messaging.py:221`), `get_company_profile` (`tools/company.py:44`).
- `PERSON_SECTIONS` keys (`scraping/fields.py:8`) are a superset of every section name our UI
  offers.
- The `send_message` result keys are exactly `{url, status, message, recipient_selected, sent}`.
- The seven `status` literals we branch on all appear in `extractor.py`.
- `_SEARCH_RESULTS_REFERENCE_CAP` is still 15 (`link_metadata.py:93`) — our UI states this
  number to the operator.

Implemented as a test that reads the server checkout (path from an env var) and skips with a
clear message when it is absent.

**Tier 2 — live `tools/list`, opt-in.** Against a running server, asserts tool names and input
schemas match our wrappers. Run before each milestone acceptance, not in CI.

### 20.3 Mocked integration tests

A `FakeLinkedInMCP` implementing the four tools we call, driven by recorded fixtures. Scenarios:

1. Happy path: search → 12 candidates → Stage 1 all → score → promote 5 → Stage 2 → score.
2. Search returns `section_errors.rate_limit` only.
3. Search returns 15 references of which 6 are `kind: "person"` (the R-01 case).
4. Stage-1 fetch returns `main_profile` OK, `experience` rate-limited → partial, resume works.
5. Profile with no `profile_urn` → dry run still attempted, messageability hint absent.
6. Dry run returns `confirmation_required` → badge set.
7. Send returns `sent: true` → `SENT`, control disabled.
8. Send returns `send_unavailable` → `AMBIGUOUS`, no auto-retry; the row is byte-identical after
   60 s of runtime; the resolve flow writes only `resolution`/`resolved_at`/`resolution_note`.
9. Send raises `ToolError` after `SENDING` committed → `AMBIGUOUS`.
10. Backend killed mid-send → restart sweep marks the row `AMBIGUOUS`.
11. Auth error mid-enrichment → queue pauses, retrieved data intact, resume after "Recheck".
12. Two concurrent `POST /send` with the same token → exactly one MCP call.
13. Draft edited between modal open and click → `409`, no MCP call.
14. `AMBIGUOUS` resolved `confirmed_sent` → a further `POST /send-confirmation` is refused `409`;
    the index still blocks; no second attempt row can be created.
15. `AMBIGUOUS` resolved `confirmed_not_sent` → a **new** attempt is created with a new id, a new
    token and a different `idempotency_key` **even though the message body is unchanged**; the
    original row is still present, still `state='AMBIGUOUS'`, still carrying its original
    `idempotency_key` and tool response.
16. Direct `UPDATE` attempting to rewrite a finished attempt's `state` back to `DRAFT`, reuse its
    `idempotency_key`, or revise a set `resolution` → all three abort at the DB trigger.

### 20.4 UI tests

Vitest + React Testing Library for components; Playwright for flows (against the mocked backend):
- "Send now" is disabled without the checkbox, and enabled with it.
- Enter in the modal does not send.
- The modal shows name, URL, exact body, and a character count equal to `body.length`.
- Every `unknown` verdict renders the exact FR-042 string.
- One mixed S-1/S-2 aggregate renders matched, not-matched and unknown claim children without
  collapsing or fabricating snippets.
- Clicking an evidence snippet highlights the right span in the raw viewer.
- Each empty/loading/error state renders for its trigger.
- After `SENT`, the send control is disabled and **no** send-fallback control is rendered — only a
  neutral "View conversation" link (one test per row of the §15 fallback matrix).
- During `AMBIGUOUS`, "Open LinkedIn" is prominent and "Copy message" is hidden until the
  acknowledgement is given.

### 20.5 One controlled end-to-end test

**Exactly one**, run manually, recorded in the plan's acceptance record.

- Preconditions: a real logged-in profile; a **consenting recipient** (a colleague or the
  operator's own second account) who knows the message is a test; `send_enabled = true`; gates
  A/B/C accepted.
- Steps: brief → one `search_people` → pick the consenting recipient's profile (by direct
  username entry if not in results) → Stage 1 → score → shortlist → draft → **dry run**
  (expect `confirmation_required`) → modal → checkbox → Send now.
- Assertions: exactly one `send_message(confirm_send=true)` in the audit log; `status == "sent"`;
  the recipient confirms the message arrived with the exact text; a second click is refused;
  the `send_attempt` row is unique.
- Then: `DELETE /api/session` and verify the DB holds no profile text.

No other live send happens during development. Every other live interaction uses read-only
tools against public profiles.

---

## 21. Ordered implementation milestones

*Historical broader roadmap. SCOPE-01 controls current delivery completion; M5–M8 outreach
and live acceptance below are not current completion gates.*

| # | Milestone | Gate to enter | Outcome |
|---|-----------|---------------|---------|
| **M0** | Foundations | — | Repo, tooling, DB, health, MCP reachability |
| **M1** | MCP client & queue | M0 accepted | Serialized job execution with durable records |
| **M2** | Brief & search | M1 accepted | Candidate pool from real searches |
| **M3** | Retrieval & parsing | M2 accepted | Raw sections stored, fields parsed with spans |
| **M4** | Scoring & evidence | M3 accepted | Ranked list with full evidence — **Phase gate A + B** |
| **M5** | Review & shortlist | M4 accepted | Operator decisions recorded |
| **M6** | Drafting | **Phase gate B accepted** | Editable, grounded drafts. Sending still off |
| **M7** | Manual send | **Phase gate C accepted** | Confirmation modal, idempotent single send, fallback |
| **M8** | Hardening & E2E | M7 accepted | Retention, export, audit, the one live E2E |

**Phase gates are hard blocks, enforced in code (FR-081, NFR-012):**
- **Gate A — discovery accepted.** Operator confirms candidate extraction and dedupe are correct
  on a completed saved search and supplies a non-empty note. The review and its persisted
  acceptance work offline (SCOPE-01). Until recorded, `/api/candidates`
  scoring UI is hidden; `/api/candidate-pool?session_id=…` remains available.
- **Gate B — matching accepted.** Operator spot-checks ≥ 10 evidence links and confirms each
  points at exact profile text. The request names ≥10 distinct evidence ids, and the insert
  transaction revalidates that each belongs to a current score in the same session and resolves
  byte-for-byte into its linked `profile_section`. Coverage, missing-section metadata, search
  context and messageability hints never count. Until recorded, `POST /drafts` returns `409`.
- **Gate C — drafting accepted.** Operator confirms ≥ 3 drafts are accurate and the grounding
  check catches a deliberately false claim. Until recorded, `send_enabled` cannot be set true
  and `POST /send` returns `409`.

---

## 22. Detailed tasks

Legend for the last column: **[D]** = companion dashboard only · **[S]** = would change the MCP
server. **Every task in this plan is [D].**

### M0 — Foundations

**Implementation status (2026-09-02): COMPLETE.** T-0.1 through T-0.5 pass their
automated acceptance checks and live loopback smoke tests. M1 remains gated on
operator acceptance of this milestone.

**T-0.1 · Repo scaffold and tooling**
- *Purpose:* one command runs the app; linting/typing match the server's conventions.
- *Files:* `pyproject.toml`, `.env.example`, `backend/linkedin_dashboard/main.py`, `frontend/` scaffold, `README.md`
- *Depends on:* —
- *Output:* `uv run -m linkedin_dashboard` serves FastAPI on 127.0.0.1:8787; `npm run dev` serves Vite on 127.0.0.1:5173 with a proxy to the backend.
- *Acceptance:* both start clean; `ruff check` and `ty check` pass.
- *Tests:* smoke test hitting `/api/health`.
- *Scope:* **[D]**

**T-0.2 · Loopback binding assertions**
- *Purpose:* NFR-001 cannot be violated by a config typo.
- *Files:* `main.py`, `settings.py`
- *Depends on:* T-0.1
- *Output:* startup raises if host is not a loopback address.
- *Acceptance:* setting `HOST=0.0.0.0` fails to start with a clear message.
- *Tests:* unit test over loopback/non-loopback hosts.
- *Scope:* **[D]**

**T-0.3 · Database, models and migrations**
- *Purpose:* the schema of §11 exists, with the partial unique index.
- *Files:* `db/models.py`, `db/migrations/`, `db/session.py`
- *Depends on:* T-0.1
- *Output:* DB created at `~/.linkedin-dashboard/session.db`, WAL, `0600`.
- *Acceptance:* migration runs on a fresh machine; `one_live_send_per_candidate` exists and rejects a second live send row.
- *Tests:* migration test; index-violation test.
- *Scope:* **[D]**

**T-0.4 · Audit log and correlation ids**
- *Purpose:* FR-090; every later task writes to it.
- *Files:* `audit.py`, `api/audit.py`
- *Depends on:* T-0.3
- *Output:* append-only writer; `GET /api/audit`.
- *Acceptance:* an UPDATE or DELETE against `audit_log` raises.
- *Tests:* append-only test; correlation id propagation.
- *Scope:* **[D]**

**T-0.5 · Response privacy filter**
- *Purpose:* NFR-002.
- *Files:* `api/_filters.py`, wired into every router
- *Depends on:* T-0.1
- *Output:* `runtime` blocks and absolute home paths stripped from all responses.
- *Acceptance:* a crafted `section_errors` with a `runtime` block emerges without it.
- *Tests:* unit test; a global test asserting no response body contains `.linkedin-mcp`.
- *Scope:* **[D]**

### M1 — MCP client and serialized queue

**T-1.1 · MCP transport client**
- *Purpose:* connect to the server over loopback streamable HTTP.
- *Files:* `mcp/client.py`
- *Depends on:* T-0.1
- *Output:* connect, `tools/list`, `tools/call` with a 240 s timeout; auto-reconnect; no `Origin` header (A-04).
- *Acceptance:* `GET /api/mcp/status` lists the server's tools against a live server.
- *Tests:* mocked transport unit tests; opt-in live test.
- *Scope:* **[D]** · **[requires verification]** exact fastmcp 3.4.4 client API.

**T-1.2 · Typed tool wrappers and response envelope**
- *Purpose:* one place that knows the `{url, sections, references, section_errors}` contract.
- *Files:* `mcp/tools.py`, `mcp/envelope.py`
- *Depends on:* T-1.1
- *Output:* `search_people`, `get_person_profile`, `get_company_profile`, `send_message` wrappers with Pydantic result models; unknown keys preserved, never dropped.
- *Acceptance:* fixtures from the server's own tests (`tests/test_tools.py:249-254`, `:898-904`) parse without loss.
- *Tests:* envelope parsing incl. `unknown_sections`, `profile_urn`, `section_errors`.
- *Scope:* **[D]**

**T-1.3 · Error classification**
- *Purpose:* §18's table.
- *Files:* `mcp/errors.py`
- *Depends on:* T-1.2
- *Output:* `classify(exc_or_result) -> ErrorClass`.
- *Acceptance:* every class in §18 is produced by at least one fixture.
- *Tests:* one test per class, pinned to fixtures captured from a live server.
- *Scope:* **[D]**

**T-1.4 · Single-slot durable job queue**
- *Purpose:* NFR-003; every MCP interaction is a recorded job.
- *Files:* `queue/worker.py`, `queue/jobs.py`, `api/jobs.py`
- *Depends on:* T-0.3, T-1.3
- *Output:* one worker task; job rows; startup sweep marking orphaned `running` jobs `interrupted`; politeness delay; cool-down; budget enforcement.
- *Acceptance:* 10 jobs enqueued at once execute strictly sequentially (asserted by overlapping timestamps being impossible); a killed process leaves an `interrupted` row.
- *Tests:* concurrency test; sweep test; budget-exhaustion test.
- *Scope:* **[D]**

**T-1.5 · SSE event stream**
- *Purpose:* live queue/progress in the UI without polling.
- *Files:* `api/events.py`, `frontend/src/hooks/useEvents.ts`
- *Depends on:* T-1.4
- *Output:* `GET /api/events` emitting job and session events.
- *Acceptance:* the UI shows position and progress during a real fetch.
- *Tests:* integration test asserting event ordering.
- *Scope:* **[D]**

### M2 — Brief and search

**T-2.1 · Role brief model, API and blocklist**
- *Purpose:* FR-001…FR-004.
- *Files:* `services/brief.py`, `api/briefs.py`, `db/models.py`
- *Depends on:* T-0.3
- *Output:* versioned brief; `PROTECTED_TERMS` rejection with a 422 naming the terms.
- *Acceptance:* a brief containing a protected term cannot be saved; editing marks scores stale.
- *Tests:* blocklist test; versioning test.
- *Scope:* **[D]**

**T-2.2 · Brief editor UI**
- *Purpose:* FR-001.
- *Files:* `pages/BriefPage.tsx`, alias editor components
- *Depends on:* T-2.1
- *Output:* full brief form with alias entry and the blocklist error surface.
- *Acceptance:* a complete brief round-trips.
- *Tests:* RTL form tests.
- *Scope:* **[D]**

**T-2.3 · Search job and persistence**
- *Purpose:* FR-010…FR-013, FR-016.
- *Files:* `services/search.py`, `api/searches.py`
- *Depends on:* T-1.4, T-2.1
- *Output:* enqueue `search_people`; store raw response verbatim; surface `section_errors`.
- *Acceptance:* against a live server, a search stores the raw text and reference list unchanged.
- *Tests:* mocked job test; validation-rejection tests mirroring `extractor.py:4430-4446`.
- *Scope:* **[D]**

**T-2.4 · Company URN lookup**
- *Purpose:* FR-012.
- *Files:* `services/search.py`, `api/searches.py`, `pages/SearchPage.tsx`
- *Depends on:* T-2.3
- *Output:* slug → `references["about"]` → `kind: "company_urn"` → `value`.
- *Acceptance:* a known slug returns its numeric id; a small company returns "not exposed" with manual entry offered.
- *Tests:* fixture test for present/absent URN.
- *Scope:* **[D]**

**T-2.5 · Candidate extraction, normalization, dedupe**
- *Purpose:* FR-014, FR-015.
- *Files:* `services/search.py`, `parsing/identity.py`
- *Depends on:* T-2.3
- *Output:* `kind=="person"` references → canonical usernames → `candidate` rows with `candidate_source` provenance.
- *Acceptance:* the same person from two searches yields one candidate with two sources.
- *Tests:* dedupe tests incl. URL/username/case variants.
- *Scope:* **[D]**

**T-2.6 · Identifier parity test vs. the server**
- *Purpose:* our normalizer must not diverge from `identifiers.py`.
- *Files:* `tests/contract/test_identifier_parity.py`
- *Depends on:* T-2.5
- *Output:* table-driven comparison against cases ported from `tests/test_identifiers.py`.
- *Acceptance:* all cases agree, including refusals (`me`, dot segments, double encoding).
- *Tests:* the test is the deliverable.
- *Scope:* **[D]**

**T-2.7 · Search UI**
- *Purpose:* FR-010, FR-011, FR-016 and the empty/rate-limited states.
- *Files:* `pages/SearchPage.tsx`, `components/QueueStatus.tsx`
- *Depends on:* T-2.3, T-1.5
- *Output:* search form, run history, raw-text viewer, reference breakdown by kind, error banners.
- *Acceptance:* the "6 of 15 references were people" case is legible (R-01).
- *Tests:* Playwright against the mocked backend.
- *Scope:* **[D]**

### M3 — Retrieval and parsing

**Acceptance status (2026-09-03): ACCEPTED.** Exact tested build
`28c2b8af922a74ffd53eccc6336a999103dfaa6a` passed the authorized three-profile
live gate with 19/21 manually annotated experience blocks both-correct for
title and company (90.4762%, strictly >85%). The two grouped-parent misses are
a recorded non-blocking limitation. M4 is unblocked but has not started (see
§1a).

**T-3.1 · Stage-1 enrichment**
- *Purpose:* FR-020, FR-022, FR-023, FR-025, FR-027.
- *Files:* `services/enrichment.py`, `api/enrichment.py`
- *Depends on:* T-1.4, T-2.5
- *Output:* per-candidate `get_person_profile(sections="experience")` jobs storing sections, errors, references and `profile_urn`.
- *Acceptance:* against a live server, a real candidate's two sections land verbatim.
- *Tests:* mocked job tests for ok/partial/rate-limited/failed.
- *Scope:* **[D]**

**T-3.2 · Stage-2 promotion and resume**
- *Purpose:* FR-021, FR-024.
- *Files:* `services/enrichment.py`
- *Depends on:* T-3.1
- *Output:* promoted-section fetches; a rate-limited fetch records only the sections actually returned and queues the remainder as `pending`.
- *Acceptance:* simulating A-09's abort produces exactly one follow-up job containing exactly the missing sections.
- *Tests:* resume test; the "≤4 sections per call" split test.
- *Scope:* **[D]**

**T-3.3 · Section parsers**
- *Purpose:* FR-030, FR-031, FR-034.
- *Files:* `parsing/*.py`
- *Depends on:* T-3.1
- *Output:* six parsers emitting fields with spans; totality guaranteed.
- *Acceptance:* across all 3 authorized real profiles, strictly > 85 % of manually annotated experience blocks must have both title and company correct, and no parser raises on any fixture. Exactly 85 % fails.
- *Tests:* per-parser fixture tests; a fuzz test feeding random text to every parser.
- *Scope:* **[D]**

**T-3.4 · Span verifier and LLM proposal path**
- *Purpose:* FR-032, FR-033.
- *Files:* `parsing/verify.py`, `llm/protocol.py`, `llm/null.py`
- *Depends on:* T-3.3
- *Output:* `VerifiedSpan` with a constructor reachable only via substring verification; `NullProvider` default.
- *Acceptance:* with `NullProvider`, the whole pipeline works; a fabricated proposal is demoted to `llm_unverified`.
- *Tests:* verifier tests incl. a proposal that is *almost* present.
- *Scope:* **[D]**

**T-3.5 · Candidate detail UI (raw + parsed)**
- *Purpose:* FR-034, FR-051 (parsed half).
- *Files:* `pages/CandidateDetailPage.tsx`, `components/RawTextViewer.tsx`, `SectionAvailabilityMap.tsx`
- *Depends on:* T-3.3
- *Output:* side-by-side parsed fields and raw text with span highlighting.
- *Acceptance:* clicking a field highlights its span.
- *Tests:* RTL + Playwright.
- *Scope:* **[D]**

### M4 — Scoring and evidence  → **Phase gates A and B**

M4 is split into three non-overlapping work packages. **WP1 (T-4.1, T-4.2 and T-4.7a)** owns
only the pure scoring package `services/scoring/{__init__,types,matching,aggregate,signals/**}`,
`tests/unit/test_scoring_{signals,aggregate}.py` and
`tests/unit/test_scoring_kernel_guards.py`. **WP2 (T-4.5/T-4.6)** owns every frontend change
and develops against frozen API mocks. **WP3 (T-4.3/T-4.4 and T-4.7b)** starts only from a
reviewed WP1 head and exclusively owns DB models, migration `v0023`, persistence/services,
lifecycle hooks, scoring/gate APIs and `tests/unit/test_scoring_boundary_guards.py`. WP1
must not access DB/API/UI/MCP/network/clock/RNG; WP2 must not edit backend code; WP3 must not
change kernel behavior or make MCP calls. The API routes and semantics frozen in §12 are the
WP2/WP3 integration boundary. No file or named test target is shared between work packages.

**T-4.1 · Signal implementations**
- *Purpose:* §14.1/14.2.
- *Files:* `services/scoring/{__init__,types,matching,signals/**}`
- *Depends on:* T-3.3, T-3.4, T-2.1
- *Output:* immutable brief/config/snapshot inputs and stably ordered S-1…S-6/S-8 signal
  aggregates plus ordered per-term/claim children. Profile-derived `matched`/`contradicted`
  claims accept only `VerifiedSpan`; `not_matched` returns typed complete-section coverage;
  `unknown` returns typed missing-section metadata. S-7 is typed search context outside the
  calculation, permanently weight 0. The §14.1 activity derivation runs before signal creation;
  every inert signal emits no aggregate, claim or provenance child.
- *Acceptance:* each active signal has fixtures for every valid verdict; S-3 covers
  `required_experience_months` null/0/positive and a months-only brief with no normalized target
  titles or required skills, where every parsed role is relevant. Its mutation test must fail if
  optional skills, positive keywords or job-description prose become relevance filters, if an
  irrelevant role's unparseable duration reduces availability, if an empty relevance-filter term
  set creates absence coverage, or if no parseable roles yields anything except
  `unknown`/`unparseable`. S-6 covers empty and populated equivalence tables, and S-8 covers empty
  credentials plus exact/alias requirements. S-1/S-2 fixtures
  produce matched + not-matched + unknown claims in one aggregate whose rollup is `mixed`;
  scalar signals emit exactly one claim. An executable matrix independently empties S-1 required
  skills, S-2 optional skills, S-3 months, S-4 titles, S-5 industries, S-6 location and S-8
  credentials and proves each disappears; S-7 never appears. Stable ordering holds after input
  shuffling.
- *Tests:* per-signal mutation-checked tests; the eight inert cases; a currently valid
  positive-keyword-only/all-inert brief; attempts to give S-7, `profile_urn` or messageability a
  score/weight must fail.
- *Scope:* **[D]**

**T-4.2 · Aggregation, confidence, bands**
- *Purpose:* §14.3/14.4, FR-040, FR-043.
- *Files:* `services/scoring/aggregate.py`
- *Depends on:* T-4.1
- *Output:* explicitly penalized, clamped and quantized `score`, bounds, confidence, band,
  calculation status, active count and stage from immutable WP1 values, including the
  pre-denominator `I=∅` no-score branch.
- *Acceptance:* the exact §14.3 `x/y`, `x/W` and `u/W` formulas hold; `lower ≤ score ≤ upper`
  after identical penalty/clamp for every numeric result, including fractional availability.
  An all-unknown candidate has `score:null`, null bounds/band and confidence 0, never zero
  points. An all-inert brief returns null score/bounds, confidence 0, low band, status unknown,
  active count 0 and applies/persists no penalty; S-7 and all inert signals are absent from every
  denominator; shuffled inputs return byte-identical output.
- *Tests:* the named §14.3 counterexample, randomized fractional-availability bounds,
  penalty/clamp boundaries, 100-run determinism, shuffled-input stability, active `y=0`, `I=∅`,
  active `W=0` rejection and an assertion that no evaluated branch divides by zero.
- *Scope:* **[D]**

**T-4.3 · Evidence persistence and links**
- *Purpose:* FR-041, §14.5.
- *Files:* `db/models.py`, `db/migrations/v0023*`, `services/scoring_persist.py`
- *Depends on:* T-4.2, T-0.3
- *Output:* append-only score/source-snapshot/signal history and ordered `score_claim` children.
  Each claim references exactly one immutable, non-empty `evidence_set`, `coverage_set` or
  `missing_set`; their child rows are disjoint. Evidence points directly to a `profile_section`;
  coverage stores section id/hash/terms/aliases/matcher version and no span.
- *Acceptance:* DB guards re-read the linked raw text and reject any non-exact, empty,
  cross-candidate, wrong-section, off-by-one, purged or stale profile span. Missing, empty,
  multiple-kind or verdict-incompatible claim provenance is rejected. `not_matched` is rejected
  unless all required sections completed successfully and its hashes match. Neither coverage
  nor search context can satisfy Gate B. Persisted numeric scores must satisfy
  `0 ≤ lower ≤ headline ≤ upper ≤ 100`; the active all-null score/bounds/band form requires
  confidence 0. A score cannot become current with a missing claim or an incorrect
  common/`mixed` rollup. Zero signals/claims can become current only with a transactionally
  re-derived all-inert attestation, exact no-score fields and no provenance/penalty children;
  its `score_input_section` set is also empty, and forging the flag or attaching any child is
  rejected.
- *Tests:* byte-exact repeated-substring and astral-Unicode spans; every rejection case above;
  migration `v0023` succeeds on populated and blank databases.
- *Scope:* **[D]**

**T-4.4 · Re-scoring and staleness**
- *Purpose:* FR-002, FR-044, FR-046, FR-049.
- *Files:* `services/scoring_persist.py`, brief/enrichment/search lifecycle services,
  `api/{scoring,briefs,candidates,session}.py`
- *Depends on:* T-4.3
- *Output:* immutable scoring-config versions, input fingerprint/source snapshot, score history
  and delta; the frozen candidate/rescore/weights/gate endpoints in §12. Brief or config edits
  stale old scores. `PUT /api/weights/current` uses optimistic versioning and performs one
  atomic rescore per candidate.
- *Acceptance:* changing a brief, weight or metro equivalence preserves history, stales the old
  score and creates a result with the new versions/fingerprint. Concurrent stale config updates
  fail `409`; no rescore path calls MCP. Gate A requires a completed search and note; Gate B
  requires Gate A plus ≥10 distinct, current, same-session exact profile-evidence ids revalidated
  inside the insert transaction. Positive S-8 with empty credentials and a zero-effective-weight
  proposal while any input is active return `422` with no writes. All-inert creation and edits
  are accepted; all-inert weight/equivalence edits create a config version and exactly one
  no-score rescore per candidate without signal/penalty rows or division. Removing the final
  credential atomically creates the next config with S-8=0 and exactly one rescore per candidate;
  re-adding it leaves S-8=0. Active↔all-inert transitions follow §14.3 and never call MCP.
- *Tests:* history/delta, input fingerprint, brief/config staleness, one-rescore-per-candidate,
  optimistic conflict, S-8 empty/removal/re-add transitions, active all-zero no-write cases,
  all-inert creation/edit/transition cases, canonical fingerprint distinction, ranked stable
  sort/null-last, Gate A/B negative cases, and explicit rejection of coverage/context/
  cross-session/stale evidence.
- *Scope:* **[D]**

**T-4.5 · Ranked list UI**
- *Purpose:* FR-050, FR-043.
- *Files:* `pages/CandidatesPage.tsx`, `components/CandidateRow.tsx`, `ScoreBadge`, `ConfidenceBand`
- *Depends on:* T-4.2
- *Output:* sortable/filterable ranked list against frozen mocks: score/bounds/band or unknown,
  confidence, calculation status, active count, stage, config version, delta, top signals and
  labelled non-scoring hints.
- *Acceptance:* provisional/enriched and numeric/unknown candidates are distinguishable without
  color alone; default order matches §12 and the permanent footer reads “Scores rank retrieved
  evidence, not people.” The search screen defaults network to `["F","S"]` and warns that only
  `F` is reliably messageable, without implying either token affects score. The all-inert fixture
  renders the exact §14.5 copy, low confidence, no top signals, and distinguishes itself from an
  active all-unknown profile without color alone.
- *Tests:* RTL + Playwright, including keyboard access, non-color state cues and D-04 default/copy.
- *Scope:* **[D]**

**T-4.6 · Evidence panel UI**
- *Purpose:* FR-041, FR-042, FR-051.
- *Files:* `components/EvidencePanel.tsx`, `SignalTable.tsx`
- *Depends on:* T-4.3, T-3.5
- *Output:* aggregate signal rollups (including `mixed`) with ordered per-claim
  matched/contradicted evidence, deterministic absence coverage and missing-section reasons;
  section highlights, score history and non-scoring search/messageability context.
- *Acceptance:* every `unknown` renders exact lowercase “not found in the retrieved data”; every
  profile-evidence click highlights the exact span; coverage/context never renders as a match
  snippet or a Gate-B-selectable evidence link. All-inert renders the exact empty-state copy and
  an empty signal list, not an `unknown` claim or Gate-B-selectable row.
- *Tests:* exact-copy, repeated/astral span highlighting, keyboard/non-color accessibility and
  frozen-response rendering tests.
- *Scope:* **[D]**

**T-4.7a · Pure-kernel protected/non-scoring guards (WP1)**
- *Purpose:* FR-045, FR-047, FR-048, §14.6.
- *Files:* `tests/unit/test_scoring_kernel_guards.py`
- *Depends on:* T-4.1
- *Output:* pure-package tests scanning signal definitions and aliases, plus type/aggregation
  guards excluding every §14.1 input-inert signal, S-7, `profile_urn` and messageability from
  kernel inputs, persistence outputs and denominators.
- *Acceptance:* adding a protected term turns the protected scan red; assigning S-7 any nonzero
  weight, emitting an inert signal, or letting any non-scoring hint affect score/bounds/confidence
  turns a mutation test red.
- *Tests:* the test is the deliverable; mutation-checked.
- *Scope:* **[D]**

**T-4.7b · Persistence/API boundary guards (WP3)**
- *Purpose:* FR-041, FR-045–FR-049, Gate B.
- *Files:* `tests/unit/test_scoring_boundary_guards.py`
- *Depends on:* T-4.3, T-4.4, T-4.7a
- *Output:* DB/API mutation tests for claim-provenance exclusivity, exact Gate-B evidence,
  protected inputs, S-7/S-8 validity, conditional positive effective weight and the all-inert
  attestation exception.
- *Acceptance:* coverage/context can never count for Gate B; incompatible/multiple/empty claim
  provenance and positive empty-credential S-8 fail before version/staleness/rescore writes.
  All-zero effective weights fail atomically when any normalized scoring input is active; they do
  not fail when every input is inert. Zero claims are accepted only for the attested all-inert
  no-score fields, and forged attestation or any attached signal/provenance/penalty child fails.
- *Tests:* the test is the deliverable; mutation-checked independently of WP1's file.
- *Scope:* **[D]**

**M4 combined gates before live acceptance:** WP1/WP3 backend tests (including the §14.3 proof
counterexample, executable eight-row inert matrix, positive-keyword-only no-score, mixed-claim
provenance, S-8 transitions, active all-zero no-write and all-inert version/rescore cases), WP2 UI
tests, full lint, type-check and build, the complete suite with `LLM_PROVIDER=null`, and migration
`v0023` against both populated and blank databases. **M4 live gate work — G-A.1/G-B.1:** an operator session on
a real search records Gate A (dedupe correct) and then Gate B via the frozen §12 endpoints. Gate
B counts ≥10 distinct exact profile-span evidence links from current same-session scores, each
manually verified and transactionally revalidated; absence coverage, missing-section metadata,
search context and messageability hints never count.

*The live G-A.1/G-B.1 exercise above is retained as historical planning, not accepted by this
scope change and not required this run. Current delivery uses SCOPE-01's offline acceptance;
the implemented Gate A restriction and Gate B integrity checks remain unchanged.*

### M5 — Review and shortlist

**T-5.1 · Decision model and API** — FR-052. Append-only `shortlist_decision`; latest wins.
Files `services/review.py`, `api/candidates.py`. Depends T-4.3. Acceptance: history preserved.
Tests: append-only + latest-wins. **[D]**

**T-5.2 · Shortlist UI** — FR-052, FR-053. `pages/ShortlistPage.tsx`. Depends T-5.1, T-4.5.
Acceptance: no action anywhere auto-shortlists. Tests: Playwright asserting no implicit decision
is written during navigation or scoring. **[D]**

**T-5.3 · Operator-set promotion threshold** — FR-053. Settings-driven auto-promotion to Stage 2,
default **off**, and when on it enqueues fetches only (never decisions). Files
`services/enrichment.py`, `pages/SettingsPage.tsx`. Depends T-3.2. Acceptance: with the threshold
off, no Stage-2 fetch occurs without a click. Tests: integration. **[D]**

### M6 — Drafting  *(entry requires Gate B accepted)*

**T-6.1 · LLM provider implementations**
- *Purpose:* provider independence with a safe default.
- *Files:* `llm/anthropic.py`, `llm/openai_compatible.py`, `llm/ollama.py`
- *Depends on:* T-3.4
- *Output:* three providers behind `LLMProvider`; provider selection in settings with the privacy warning (§19.3).
- *Acceptance:* switching providers changes nothing outside `llm/`; `NullProvider` remains the default.
- *Tests:* protocol conformance tests with recorded responses; a test asserting no module outside `llm/` imports a provider SDK.
- *Scope:* **[D]** · gated on the M6 half of **D-02** (local Ollama vs. hosted). `NullProvider` remains the default regardless (LD-08); this task adds providers, it does not switch the default.

**T-6.2 · Draft generation**
- *Purpose:* FR-060, FR-061, FR-064.
- *Files:* `services/drafting.py`, `api/drafts.py`, `llm/prompts/`
- *Depends on:* T-6.1, T-5.1
- *Output:* a draft built from the brief + this candidate's evidence only; a template fallback when no provider is configured.
- *Acceptance:* a candidate with no `main_profile` returns 409; the prompt contains no other candidate's data.
- *Tests:* prompt-composition test; 409 test.
- *Scope:* **[D]**

**T-6.3 · Grounding check**
- *Purpose:* FR-062; "no unsupported claims".
- *Files:* `services/drafting/grounding.py`
- *Depends on:* T-6.2
- *Output:* claim extraction → each claim matched to an `evidence` row or brief text → `pass`/`warn` with a per-claim report; override with recorded justification.
- *Acceptance:* a draft with a deliberately invented employer is flagged; a draft citing only real evidence passes.
- *Tests:* positive and negative fixtures; mutation-checked by disabling the matcher.
- *Scope:* **[D]**

**T-6.4 · Draft editor UI**
- *Purpose:* FR-063, FR-062.
- *Files:* `pages/OutreachDetailPage.tsx`, `components/DraftEditor.tsx`, `CharCount.tsx`, `GroundingReport.tsx`
- *Depends on:* T-6.3
- *Output:* plain textarea, live char count, version history, inline grounding warnings.
- *Acceptance:* editing creates a new version and re-runs grounding.
- *Tests:* RTL + Playwright.
- *Scope:* **[D]**

**M6 gate work — G-C.1:** operator reviews ≥3 drafts and confirms the grounding check catches a
planted false claim; records Gate C.

### M7 — Manual send  *(entry requires Gate C accepted)*

**T-7.1 · Dry-run endpoint**
- *Purpose:* FR-070, FR-071.
- *Files:* `services/sending.py`, `api/sending.py`
- *Depends on:* T-6.4, T-1.2
- *Output:* `send_message(..., confirm_send=false)` with the result stored and rendered verbatim.
- *Acceptance:* a `confirmation_required` result sets the badge; `confirm_send` is literally `False` at the call site.
- *Tests:* mocked test asserting the argument value; a CI grep asserting `confirm_send=True` appears in exactly one non-test file.
- *Scope:* **[D]**

**T-7.2 · Confirmation token minting and consumption**
- *Purpose:* FR-072, §17 layers 1 and 5.
- *Files:* `services/sending.py`, `api/sending.py`
- *Depends on:* T-0.3
- *Output:* single-use token bound to `(candidate, body_sha256)`, TTL 300 s, atomic consumption.
- *Acceptance:* two concurrent consumptions → exactly one winner.
- *Tests:* concurrency test; expiry test; hash-mismatch 409 test.
- *Scope:* **[D]**

**T-7.3 · Send endpoint and state machine**
- *Purpose:* FR-075…FR-078, §16, §17 layers 2–4, 6.
- *Files:* `services/sending.py`, `db/models.py`
- *Depends on:* T-7.2, T-1.3
- *Output:* write-ahead `SENDING` row, one MCP call, status classification, immutable terminal rows (`send_attempt_is_immutable`, `send_resolution_is_final`), the `one_live_send_per_candidate` predicate including the `unresolved`/`confirmed_sent` block, resolution writes confined to three columns, new-attempt creation after `confirmed_not_sent`, no retry, startup sweep.
- *Acceptance:* each of the seven statuses maps correctly; a killed process leaves `AMBIGUOUS`; a replayed idempotency key makes no second call; a finished row cannot be mutated by any code path; a second attempt after `confirmed_not_sent` gets a fresh id and key while the original row is unchanged.
- *Tests:* the full §20.3 scenarios 7–13, mutation-checked on the `send_unavailable` classification.
- *Scope:* **[D]**

**T-7.4 · Confirmation modal UI**
- *Purpose:* FR-073, FR-074, NFR-011.
- *Files:* `components/SendConfirmationModal.tsx`
- *Depends on:* T-7.2
- *Output:* the exact modal of §15, rendered only from the server response.
- *Acceptance:* button disabled without the checkbox; Enter does not send; focus starts on Cancel.
- *Tests:* RTL keyboard tests; Playwright flow.
- *Scope:* **[D]**

**T-7.5 · Send state UI and fallback**
- *Purpose:* FR-077, FR-079, FR-080.
- *Files:* `components/SendStateBadge.tsx`, `FallbackActions.tsx`, `pages/OutreachPage.tsx`
- *Depends on:* T-7.3
- *Output:* badges for every state in §16, including the three `AMBIGUOUS` resolution variants; `FallbackActions` gated by the §15 matrix; the two-step `AMBIGUOUS` resolution; the thread-check action with its "may mark threads read" warning; the guarded reveal for Copy message during `AMBIGUOUS`, written to the audit log.
- *Acceptance:* the fallback matches the §15 matrix in every row; after `SENT` no send-fallback control is rendered; resolving `confirmed_not_sent` requires two distinct confirmations and unlocks only the *creation* of a new attempt, never a resend of the old one.
- *Tests:* Playwright per state.
- *Scope:* **[D]**

**T-7.6 · Send feature gate**
- *Purpose:* FR-081, NFR-012.
- *Files:* `settings.py`, `api/session.py`, `services/sending.py`
- *Depends on:* T-7.3
- *Output:* `SEND_ENABLED=false` default; toggle requires gates A+B+C recorded; `POST /send` returns 409 otherwise.
- *Acceptance:* with any gate missing, sending is impossible via the API even with a valid token.
- *Tests:* one test per missing gate.
- *Scope:* **[D]**

### M8 — Hardening, retention, E2E

**T-8.1 · Retention and purge** — FR-092, §11 retention, §19.6. Files `db/retention.py`,
`api/session.py`. Depends T-0.3. Acceptance: `DELETE /api/session` leaves no profile text
recoverable from the file (post-`VACUUM` byte scan). Tests: purge + VACUUM test. **[D]**

**T-8.2 · Export** — FR-091. Files `api/session.py`, `services/export.py`. Acceptance: export
round-trips candidates, scores, evidence and decisions. Tests: schema test. **[D]**

**T-8.3 · Contract test suite (Tier 1 + Tier 2)** — §20.2. Files `tests/contract/`. Depends
T-1.2. Acceptance: changing a tool name in a copy of the server checkout turns Tier 1 red.
Tests: the suite is the deliverable. **[D]**

**T-8.4 · Full mocked integration suite** — §20.3 scenarios 1–13. Files `tests/integration/`.
Depends T-7.3. Acceptance: all 13 pass; each has a mutation that fails it. **[D]**

**T-8.5 · The one controlled E2E** — §20.5. Manual, recorded, with a consenting recipient.
Acceptance: exactly one `confirm_send=true` call in the audit log; recipient confirms receipt;
second attempt refused. **[D]**

**T-8.6 · Operator documentation** — README covering prerequisites (`--login`, running the
server with `--transport streamable-http`), the phase gates, the fallback path, and the privacy
notice of §19.8. **[D]**

---

## 23. Task dependency graph

```
T-0.1 ─┬─ T-0.2
       ├─ T-0.3 ─┬─ T-0.4
       │         └─ T-2.1 ─┬─ T-2.2
       │                   └─ T-2.3 ─┬─ T-2.4
       │                             ├─ T-2.5 ─── T-2.6
       │                             └─ T-2.7
       ├─ T-0.5
       └─ T-1.1 ─ T-1.2 ─ T-1.3 ─ T-1.4 ─┬─ T-1.5
                                          └─ T-2.3

T-2.5 ─ T-3.1 ─┬─ T-3.2 ─────────────── T-5.3
               ├─ T-3.3 ─┬─ T-3.4 ──┐
               │         └─ T-3.5 ──┼── T-4.6
               └───────────────────┐│
T-2.1 ────────────────────────────┐││
                                  ▼▼▼
                              T-4.1 ─ T-4.2 ─┬─ T-4.3 ─┬─ T-4.4 ─┐
                                            │          └─ T-4.6  ├─ T-4.7b
                                            ├─ T-4.5             │
                                            └─ T-4.7a ───────────┘

T-4.7b ─ T-5.1 ─ T-5.2
              └──────── [GATE B] ─ T-6.1 ─ T-6.2 ─ T-6.3 ─ T-6.4
                                                            │
                                                     [GATE C]
                                                            ▼
T-0.3 ──────────────────────── T-7.2 ─┬─ T-7.3 ─┬─ T-7.5 ─ T-7.6
T-1.2, T-6.4 ─ T-7.1 ─────────────────┘         └─ T-7.4

T-7.3 ─┬─ T-8.4
T-1.2 ─┴─ T-8.3
T-0.3 ─── T-8.1 ─ T-8.2
T-7.6 ─── T-8.5 ─ T-8.6
```

Critical path: **T-0.1 → T-0.3 → T-1.1 → T-1.2 → T-1.4 → T-2.3 → T-2.5 → T-3.1 → T-3.3 →
T-4.1 → T-4.2 → T-4.3 → T-4.4 → T-4.7b → T-5.1 → [Gate B] → T-6.2 → T-6.3 → [Gate C] → T-7.2 → T-7.3 → T-8.5.**

---

## 24. Acceptance criteria per milestone

*Historical milestone criteria below remain recorded. SCOPE-01 supersedes live/outreach
completion requirements for this delivery; applicable technical integrity checks still apply.*

| Milestone | Accepted when |
|---|---|
| **M0** | Backend and frontend start on loopback only; DB migrates on a clean machine; `one_live_send_per_candidate` rejects a second live row; the audit log refuses UPDATE/DELETE; a crafted `runtime` block never reaches an API response. |
| **M1** | `GET /api/mcp/status` lists the live server's tools; ten queued jobs execute strictly one at a time; a killed backend leaves `interrupted` rows and no lost work; every §18 error class is produced by a fixture; SSE shows queue position and per-section progress. |
| **M2** | A real `search_people` stores raw text and references verbatim; `network` and `current_company` validation matches the server's rules **before** any call; the same person from two searches is one candidate with two sources; the identifier parity test passes; the UI makes "N of 15 references were people" legible. |
| **M3** | Stage-1 fetches store both sections verbatim with `profile_urn` when present; a rate-limited fetch stores what returned and queues exactly the missing sections; parsers never raise on any fixture and strictly >85 % of manually annotated experience blocks have both title and company correct across all 3 authorized real profiles; a fabricated LLM span cannot become evidence; clicking a parsed field highlights its span. |
| **M4** | Mixed S-1/S-2 outcomes persist as ordered claims, each with exactly one verdict-compatible provenance kind. Every matched/contradicted claim has exact `VerifiedSpan` evidence (100%); every `not_matched` claim has complete hashed coverage/no snippet; every `unknown` claim has missing-section provenance and exact lowercase FR-042 copy. The proven fractional-availability formulas satisfy `lower ≤ score ≤ upper` after identical penalty/clamp; all-unknown is nullable with confidence 0. The executable eight-row input matrix proves every inert signal is absent from calculation/persistence, and a positive-keyword-only/all-inert brief persists/returns null score/bounds, confidence 0, low band, status unknown, active count 0 and no child/penalty rows without `0/0`. S-7/network, `profile_urn` and messageability cannot affect scoring/Gate B. Empty-credential S-8, active zero-effective-weight rejection, and all-inert config version/rescore transitions obey FR-049/FR-046 without partial writes. Brief/config edits preserve history; populated/blank `v0023` migrations, disjoint ownership gates, frozen APIs, accessibility, `NullProvider`, full checks, and **Gates A and B** pass. |
| **M5** | Decisions are append-only with history; navigating and scoring write no decision rows; auto-promotion is off by default and, when on, enqueues fetches only. |
| **M6** | Drafts generate only for shortlisted candidates with a `main_profile`; the prompt contains only that candidate's evidence; a planted false claim is flagged; editing versions the draft and re-runs grounding; the whole flow works with `NullProvider`; **Gate C recorded.** |
| **M7** | `confirm_send=True` appears in exactly one non-test file; the modal shows name, URL, exact body, char count and checkbox; the button is dead without the checkbox and Enter never sends; concurrent sends produce exactly one MCP call; each of the seven statuses maps to the right state; `send_unavailable` is `AMBIGUOUS` and never auto-retried; a crash mid-send yields `AMBIGUOUS`; a finished attempt cannot be mutated (both triggers fire); `confirmed_not_sent` produces a new attempt with a new idempotency key while the original row survives unchanged; the fallback matches the §15 matrix row-for-row; sending is impossible with any gate unrecorded. |
| **M8** | Purge leaves no profile text in the DB file after `VACUUM`; export round-trips; Tier-1 contract tests fail against a mutated server checkout; all 13 integration scenarios pass; the single live E2E delivers exactly one message to a consenting recipient and refuses the second attempt; the README documents prerequisites, gates, fallback and the privacy notice. |

---

## 25. Definition of done

*The original task/project definitions below describe the broader roadmap. Current delivery
completion follows SCOPE-01 and its offline acceptance, with applicable technical checks;
it does not require outreach, live Gate A/B acceptance, a send exercise or a merge.*

A task is done when:

1. Code is merged, `ruff check`, `ruff format --check` and `ty check` pass.
2. Its tests exist **and have been mutation-checked** — the covered code was deliberately broken
   and a test went red (`AGENTS.md` § Tests).
3. Its acceptance criteria are demonstrated, not asserted.
4. Any assumption it relied on is either verified and unmarked, or still marked
   **[requires verification]** in this document.
5. `git diff --stat` under the MCP server checkout is empty (NFR-013).

The **project** is done when:

1. Every FR is implemented or explicitly deferred to the parking lot with the operator's
   agreement.
2. All three phase gates are recorded.
3. The single live E2E has run, its result is recorded, and the test session has been purged.
4. `SEND_ENABLED` returns to `false` by default after the E2E.
5. The README lets a fresh operator get from a clean checkout to a scored candidate list without
   asking a question.

---

## 26. Risks and mitigations

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| **R-01** | **The 15-reference cap yields fewer than 10 candidates.** `_SEARCH_RESULTS_REFERENCE_CAP = 15` (`link_metadata.py:93`) is shared by *all* reference kinds on the results page, so companies, schools and chrome links consume the same budget. The brief's "10–15 candidates per session" may not be reachable from one search. | High | High | **Call it out rather than resolve it by scope growth.** The UI reports person-reference count vs. total; the workflow expects **several narrower searches** per session (varying keywords, location, `network`, `current_company`). Adding pagination to the server is **out of scope** (NG-9, NG-13) and is logged in the parking lot as PL-1. |
| **R-02** | **Many candidates are not messageable.** `send_message` requires the recipient be "directly messageable from the profile page" (`tools/messaging.py:232`); a 2nd/3rd-degree profile without Open Profile/InMail returns `message_unavailable`. | High | Medium | The dry run (FR-070) is the precheck; `profile_urn` presence is a hint (A-10); the fallback (FR-080) covers the states where nothing was sent; briefs that need messaging should prefer `network=["F"]`, which the search UI recommends when the operator's goal is outreach. |
| **R-03** | **`AMBIGUOUS` is unresolvable without touching messaging.** Verifying a send means reading the inbox, and `get_conversation`/`search_conversations` are not read-only — they may mark threads read (`tools/messaging.py:78-79`). | Medium | Medium | Verification is operator-initiated only, with the consequence stated on the button. The primary resolution is the operator checking LinkedIn in their own browser. |
| **R-04** | **Raw-text parsing breaks when LinkedIn changes layout.** Everything downstream of `sections` is our own guesswork over innerText. | High | Medium | Parsers are total and never block scoring; unparsed content lowers confidence rather than the score; raw text is always visible; the LLM path exists precisely to absorb layout drift and is verified by substring, so drift degrades to `llm_unverified` rather than to fabricated data. |
| **R-05** | **Rate limiting mid-session.** The server aborts remaining sections on the sentinel (A-09). | Medium | Medium | Politeness delay, nav budget, exponential cool-down, resume-missing-sections, and a UI that makes the pause explicit rather than silent. |
| **R-06** | **A duplicate or unintended send.** The one failure this project cannot take back. | Low | Critical | Seven layers (§17), the DB index as the real guarantee, write-ahead attempt rows, no retries anywhere, and a feature gate that is off by default. |
| **R-07** | **LLM fabricates a claim in a message.** | Medium | High | The grounding check (FR-062) plus mandatory human editing plus the modal's exact-text display. Also: the whole system works with `NullProvider`, so the LLM is removable. |
| **R-08** | **Profile data leaves the machine via a hosted LLM.** | Medium | High | `NullProvider` default, explicit opt-in with a named warning, local Ollama recommended, `contact_info` never sent, per-call audit (§19.3). Decision **D-02**. |
| **R-09** | **Error classification by string matching breaks on a server upgrade.** `mask_error_details=True` (`server.py:215`) means we read prose, not types. | Medium | Medium | Tier-1 contract tests over the server checkout; fixtures captured live; `UNKNOWN` fails safe (job fails, queue continues, nothing is sent). |
| **R-10** | **Session expiry mid-run.** | Medium | Low | Queue pauses, data retained, blocking banner with the exact `--login` command, resume on recheck (§18). |
| **R-11** | **Scope creep toward a recruiting platform.** Every one of the out-of-scope items is one small feature away. | Medium | High | §29's checklist is run at every milestone acceptance; the parking lot exists so ideas are recorded rather than built; no feature ships without an FR id. |
| **R-12** | **Scoring is perceived as objective truth.** | Medium | Medium | Confidence bands, `score_lower`/`score_upper`, the FR-042 copy rule, and a permanent UI footer: "Scores rank retrieved evidence, not people." |
| **R-13** | **fastmcp client API differs from expectation.** | Medium | Low | Isolated in `mcp/client.py` (T-1.1); marked **[requires verification]**; the official `mcp` SDK is the drop-in alternative. |
| **R-14** | **Serialized queue makes the session slow.** ~55 navigations at 2 s server delay plus load and our politeness delay ≈ 15–25 minutes for a 15-candidate session. | High | Low | Set expectations in the UI (per-job ETA, queue position); Stage 2 only for promoted candidates; the whole staged design exists for this. |
| **R-15** | **Compliance and ToS.** Automated reading of LinkedIn profiles and automated sending sit in a contested area of LinkedIn's User Agreement. | — | High | Out of my hands to resolve, and worth stating plainly: this is single-user, one-time, human-in-the-loop, at human volume, using the operator's own session, with no evasion (NG-11). The operator should satisfy themselves this is acceptable use before M7. Logged as **D-07**. |

---

## 27. Decisions requiring your approval

**D-01, D-02 and D-08 were approved on 2026-09-02; D-03 through D-06 were approved on
2026-09-03.** All are recorded as locked in §1a and kept here with their reasoning for the
record; the "Status" column is the authority.

| ID | Decision | Options | Recommendation | Status |
|----|----------|---------|----------------|--------|
| **D-01** | **Where the companion app lives** | (a) sibling repo `linkedin-dashboard/`; (b) directory inside `linkedin-mcp-server`; (c) separate repo entirely | **(a)**. Keeps NFR-013 trivially true and keeps the server's release process (`AGENTS.md` § Release Process) untouched. A directory inside would drag the dashboard into the server's CI, versioning and Docker image. | **APPROVED 2026-09-02** — locked, see §1a |
| **D-02** | **LLM provider, and whether profile text leaves the machine** | (a) `NullProvider` only for MVP; (b) local Ollama; (c) hosted (Anthropic/OpenAI-compatible) | **(a) for M0–M5, (b) for M6.** Hosted only with an explicit, informed opt-in. This is the plan's single biggest privacy lever (§19.3, R-08). | **APPROVED 2026-09-02** — locked, see §1a |
| **D-03** | **Whether any MCP server change is permitted** | (a) none (NFR-013); (b) one additive read-only change | **(a)**. Every capability the workflow needs already exists. If (b) were ever taken, the only candidate worth it is search pagination (PL-1), and it belongs in the server's own issue/PR flow (`AGENTS.md` § Development Workflow), not here. | **APPROVED 2026-09-03** — locked, see §1a |
| **D-04** | **Default `network` filter for outreach-intent searches** | (a) none; (b) `["F"]`; (c) `["F","S"]` | **(c)**, with a UI note that only (b) reliably yields messageable candidates (R-02). Network remains non-scoring search context. | **APPROVED 2026-09-03** — locked, see §1a |
| **D-05** | **Retention window** | 7 / 30 / 90 days | **30 days**, with the purge countdown always visible and manual purge available at any time. Implementation remains M8/T-8.1. | **APPROVED 2026-09-03** — locked, see §1a |
| **D-06** | **Auto-promotion to Stage 2** | (a) always manual; (b) operator-set score threshold, default off | **(b) default off.** Keeps FR-053 honest while letting a confident operator save clicks. Implementation remains M5/T-5.3. | **APPROVED 2026-09-03** — locked, see §1a |
| **D-07** | **Acceptable-use confirmation for the send feature** | Operator attests before `SEND_ENABLED` can be turned on | **Require it.** A one-time in-app attestation recorded in the audit log (R-15). | Open — needed before M7 |
| **D-08** | **MCP server run mode** | (a) direct `--transport streamable-http`; (b) daemon owner with a bearer token | **(a)**. Simplest, and A-03 shows a direct server takes no token. If (b) is ever wanted, the client must present the owner's token and `mcp/client.py` is the only file that changes. | **APPROVED 2026-09-02** — locked, see §1a |
| **D-09** | **Character-limit warning threshold** | Needs a real number | **[requires verification]** — measure LinkedIn's actual limit once, then set a soft warning below it. Until measured, show the count with no threshold (A-14). | Open — needed before M7 |
| **D-10** | **Who the E2E recipient is** | Operator's second account / a consenting colleague | Your call; must be someone who consents in advance (§20.5). | Open — needed before M7 |

---

## 28. Parking lot

Recorded, not built. Nothing here maps to an FR.

| ID | Idea | Why it is parked |
|----|------|------------------|
| PL-1 | Search pagination beyond one results page | Would require an MCP server change (NG-9, NG-13); belongs in the server's own issue flow |
| PL-2 | `get_sidebar_profiles` (`tools/person.py:263`) as a candidate-expansion source | Extra navigations, no FR, and it widens sourcing beyond the search the operator specified |
| PL-3 | `get_company_employees` (`tools/company.py:228`) for company-targeted sourcing | Genuinely useful; a second discovery path is scope growth for a one-time session |
| PL-4 | Reading `posts` to personalize messages | Heaviest section (scroll-based), highest privacy sensitivity, and easiest way to write a creepy message |
| PL-5 | Job-posting import to auto-generate the brief (`search_jobs`, `get_job_details`) | Nice, unnecessary; the operator has the JD already |
| PL-6 | Embedding-based semantic skill matching | Breaks the evidence link that §14.5 depends on; only worth it with a span-preserving design |
| PL-7 | Multi-session comparison / candidate history across sessions | Becomes a CRM (NG-7) |
| PL-8 | Message A/B variants | One step from campaign management (NG-7) |
| PL-9 | Reply tracking via `get_inbox` | Marks threads read (`tools/messaging.py:78-79`) and starts a follow-up loop (NG-4) |
| PL-10 | Team sharing / hosted deployment | NG-8, NG-10 |
| PL-11 | Auto-tuning weights from operator decisions | Learned ranking is unexplainable by construction; conflicts with FR-041 |
| PL-12 | `connect_with_person` for non-messageable candidates | NG-3 |
| PL-13 | PDF/report export of the candidate pack | Trivial to add later; no FR |
| PL-14 | Live re-scrape to detect profile changes | NG-5 |

---

## 29. Scope-control checklist

Run at every milestone acceptance. Any "no" blocks acceptance. Items marked **[LD-nn]** check a
decision locked in §1a — a "no" there is not a scope question but a reverted decision, and must
be resolved by editing §1a with a reason before any other work continues.

1. Does every shipped feature map to an FR id in §4?
2. **[D-01, D-03]** Is `git diff --stat` under `linkedin_mcp_server/` still empty, with no additive server change?
3. Does `confirm_send=True` still appear in exactly one non-test file?
4. Is there still exactly one code path from a human click to a send?
5. **[LD-02]** Is there still no timer, scheduler, cron, watcher, poller or background loop that can read or write a `send_attempt` row?
6. Is `connect_with_person` still never called?
7. Does the queue still execute at most one MCP call at a time?
8. **[LD-07]** Does every displayed match claim still resolve to an exact substring of stored raw text?
9. Does every `unknown` verdict still render "not found in the retrieved data"?
10. Are protected attributes still absent from signals, aliases and prompts?
11. Is `SEND_ENABLED` still `false` by default, still gated on A+B+C?
12. **[LD-08]** Is `NullProvider` still the default, and does the app still work end-to-end without an LLM?
13. **[LD-01]** Does the fallback still match the §15 matrix exactly — offered where nothing was sent, guarded during `AMBIGUOUS`, withdrawn as a send action after `SENT`?
14. Did any new idea get built instead of being written into the parking lot?
15. **[LD-05]** Does the app still bind loopback only, and still leak no profile path or diagnostics `runtime` block to the frontend?
16. Are all `[requires verification]` markers either resolved or still marked?
17. **[LD-01]** Is `send_unavailable` still classified `AMBIGUOUS`; is a finished attempt still immutable (both triggers present); and does `confirmed_not_sent` still unlock only the creation of a *new* attempt rather than reopening the old one?
18. **[LD-03]** Does a partial or rate-limited response still re-request only the missing sections?
19. **[LD-04]** Is candidate volume still coming from multiple narrow searches, with no pagination added anywhere?
20. **[LD-06]** Do `profile_urn` and messageability still carry zero weight and appear in no signal definition?
21. **[D-08]** Does the MCP client still construct no auth header and manage no server lifecycle?
22. **[D-04]** Does search still default to `["F","S"]`, warn that only `F` is reliably messageable, and keep network as non-scoring context?
23. **[FR-048]** Is S-7 still permanently weight 0 and excluded from score, bounds, penalties, confidence and Gate B?
24. **[D-05]** Is retention still 30 days with countdown/manual purge, with implementation owned by M8/T-8.1?
25. **[D-06]** Is the operator promotion threshold still default-off, with implementation owned by M5/T-5.3?
26. Does every score claim still have exactly one verdict-compatible evidence, coverage or missing-section provenance kind, with mixed signal rollups preserved?
27. Do invalid positive empty-credential S-8 and zero-effective-weight updates with at least one active input still return `422` before any version, staleness or rescore write?
28. Does the eight-condition activity matrix still exclude every inert signal from calculation/persistence while allowing an all-inert positive-keyword-only brief and its versioned no-score lifecycle without `0/0`?

---

## Recommended build order

1. **T-0.1 → T-0.5** — scaffold, DB with the send index, audit log, privacy filter. The index and
   the filter go in *first* because retrofitting either is how the guarantees get lost.
2. **T-1.1 → T-1.5** — MCP client, typed wrappers, error classification, single-slot queue, SSE.
   Nothing above this line is meaningful until one real tool call round-trips.
3. **T-2.1 → T-2.7** — brief, search, candidate extraction, identifier parity. **Gate A.**
4. **T-3.1 → T-3.5** — Stage 1, resume, parsers, span verifier, raw+parsed detail view.
5. **T-4.1 → T-4.7a/T-4.7b** — signals, aggregation, claim/evidence persistence, ranked list, evidence panel,
   protected-attribute guard. **Gate B.**
6. **T-5.1 → T-5.3** — decisions and shortlist.
7. **T-6.1 → T-6.4** — providers, drafting, grounding, editor. **Gate C.**
8. **T-7.1 → T-7.6** — dry run, tokens, send state machine, modal, badges, feature gate.
9. **T-8.1 → T-8.6** — retention, export, contract tests, integration suite, the one E2E, docs.

Deliberate ordering choices: the **send index before any send code**; **evidence persistence
before the ranked list**, so no screen ever shows a score that cannot be explained; **the
grounding check before the editor**, so the first draft an operator ever sees is already checked.

---

## The smallest usable MVP

**M0 → M5 plus FR-080.** That is:

- Create a brief.
- Run `search_people`, collect and dedupe candidates.
- Stage-1 enrich (`main_profile` + `experience`).
- Provisional score with full evidence.
- Promote selected candidates to Stage 2 and re-score.
- Review, shortlist, reject.
- **Copy message / Open LinkedIn** — the operator writes and sends in LinkedIn's own UI.

This delivers goals G-1 through G-5, G-7, G-8 and G-10, has **zero send risk** because
`send_message` is never called at all, **and zero data-egress risk** because `NullProvider` is
the default and no profile text leaves the machine (D-02 / LD-08). It is genuinely useful on
its own. M6 (drafting) and M7
(in-app sending) are increments on top of a system that already works.

---

## The five highest-risk assumptions

| Rank | Assumption | Why it is the riskiest | How we learn early |
|---|---|---|---|
| 1 | **A-06 — one search yields enough candidates.** The 15-reference cap is shared across kinds (`link_metadata.py:93,107`), so a search could return 5 people and 10 company links. | It undercuts the brief's stated 10–15 candidates per session, and the fix is out of scope by construction (R-01, NG-9). | **T-2.3, day 1 of M2:** run three real searches and count person references. If it is consistently < 8, the multi-search workflow becomes mandatory, not optional. |
| 2 | **A-11 — `confirm_send=false` never sends.** The whole dry-run design assumes the early return at `extractor.py:5010-5017` precedes the `keyboard.type` at `:5033`. | If wrong, our "safe validation" step is a send. | Verified by reading the source; confirm once against a live server with a consenting recipient **before** T-7.1 ships. |
| 3 | **The `send_unavailable` → `AMBIGUOUS` classification.** Returned after typing and clicking (`extractor.py:5074-5080`). | Classifying it as a failure would let the operator re-send a message that already went out — the one unrecoverable bug (R-06). | Pinned by a mutation-checked test in T-7.3 and by reading the same source lines at review time. |
| 4 | **R-02 — enough candidates are messageable.** `send_message` needs the profile to be directly messageable (`tools/messaging.py:232`). | If most shortlisted candidates come back `message_unavailable`, M7 delivers little and the fallback becomes the primary path. | **Cheap test during M4:** run `send_message(confirm_send=false)` against 3–5 already-scored candidates and count `confirmation_required` vs. `message_unavailable`. Do this *before* building M7. |
| 5 | **R-04 — innerText parsing is good enough to score on.** Everything downstream of `sections` is our inference over free text. | If experience blocks parse poorly, S-3/S-4/S-5 (45 configured weight points before active-signal normalization) degrade to `unknown` and confidence collapses. | **T-3.3 acceptance:** strictly >85 % of manually annotated experience blocks have both title and company correct across all 3 authorized real profiles. If missed, the LLM proposal path (T-3.4) moves from optional to required, which re-opens D-02 earlier. |

---

## Decisions to approve before implementation

**Nothing blocks M4.** D-01, D-02 and D-08 were approved 2026-09-02; D-03, D-04, D-05 and
D-06 were approved 2026-09-03; and the eight invariants LD-01…LD-08 remain unchanged. All are
locked in §1a. D-05's 30-day retention is still implemented in M8/T-8.1, and D-06's
operator-threshold/default-off behavior is still implemented in M5/T-5.3; approving their
outcomes does not pull either implementation into M4.

Remaining, none of them blocking M4–M5:
- **Before M6:** the second half of **D-02** — whether message *generation* uses a local model
  (Ollama) or a hosted one. Deferred deliberately; the provider interface and `NullProvider`
  default mean this decision costs nothing to postpone and buys real information (by M6 you will
  know how good the deterministic drafts already are).
- **Before M7:** **D-07** (acceptable-use attestation), **D-09** (character-limit threshold —
  still **[requires verification]**), **D-10** (E2E recipient).

---

## Requirement-to-milestone traceability

| Req | Milestone | Primary tasks | Verified by |
|---|---|---|---|
| FR-001–FR-003 | M2/M4 | T-2.1, T-2.2, T-4.1, T-4.4 | Versioned input round-trip/staleness, eight inert cases and positive-keyword-only valid-brief tests |
| FR-004 | M2 | T-2.1 | Blocklist 422 test |
| FR-005 | M4 | T-4.1, T-4.4 | Equivalence version/exact-only/staleness tests |
| FR-010, FR-011 | M2/M4 | T-2.3, T-2.7, T-4.5 | Search validation plus D-04 default/warning UI tests |
| FR-012 | M2 | T-2.4 | URN present/absent fixtures |
| FR-013 | M2 | T-2.3 | Verbatim-storage test |
| FR-014, FR-015 | M2 | T-2.5, T-2.6 | Dedupe + identifier parity |
| FR-016 | M2 | T-2.3, T-2.7 | Rate-limit banner UI test |
| FR-020 | M3 | T-3.1 | Stage-1 job test |
| FR-021 | M3 | T-3.2 | Stage-2 job test |
| FR-022 | M1/M3 | T-1.4, T-3.1 | Serialization test |
| FR-023 | M3 | T-3.1 | Verbatim section storage |
| FR-024 | M3 | T-3.2 | Resume-missing-sections test |
| FR-025 | M3 | T-3.1 | `profile_urn` persistence test |
| FR-026 | M3 | T-3.1 | `unknown_sections` alarm test |
| FR-027 | M3 | T-3.1 | Failed-fetch retention test |
| FR-030, FR-031 | M3 | T-3.3 | Parser span fixtures |
| FR-032, FR-033 | M3 | T-3.4 | Verifier tests |
| FR-034 | M3 | T-3.3, T-3.5 | Raw viewer test |
| FR-040 | M4 | T-4.1–T-4.3, T-4.5, T-4.6 | Mixed-claim rollup, aggregation properties, eight-row inert matrix and all-inert no-score/UI tests |
| FR-041 | M4 | T-4.1, T-4.3, T-4.6, T-4.7b | Claim-provenance exclusivity and evidence integrity tests |
| FR-042 | M4 | T-4.6 | Exact-copy UI test |
| FR-043 | M4 | T-4.2, T-4.5 | Stage badge UI test |
| FR-044 | M4 | T-4.4 | Delta test |
| FR-045 | M4 | T-4.7a, T-4.7b | Protected-attribute guards |
| FR-046 | M4 | T-4.4, T-4.7b | Conditional positive-effective-weight no-write plus all-inert config-version/rescore tests |
| FR-047 **[LD-06]** | M4 | T-4.1, T-4.7a, T-4.7b | No-messageability-signal guards |
| FR-048 | M4 | T-4.1, T-4.2, T-4.7a, T-4.7b | S-7 non-scoring mutation guards |
| FR-049 | M4 | T-4.4, T-4.7b | S-8 active/all-inert transition and conditional all-zero no-write tests |
| FR-050 | M4 | T-4.5 | Ranked list UI test |
| FR-051 | M3/M4 | T-3.5, T-4.6 | Detail view tests |
| FR-052 | M5 | T-5.1, T-5.2 | Append-only decision test |
| FR-053 | M5 | T-5.2, T-5.3 | No-implicit-decision test |
| FR-060, FR-061 | M6 | T-6.2 | Prompt-composition + 409 tests |
| FR-062 | M6 | T-6.3 | Planted-false-claim fixture |
| FR-063 | M6 | T-6.4 | Draft versioning test |
| FR-064 | M6/M7 | T-6.2, T-7.3 | Byte-equality test body→tool arg |
| FR-070, FR-071 | M7 | T-7.1 | `confirm_send=False` assertion |
| FR-072 | M7 | T-7.2 | Token concurrency test |
| FR-073, FR-074 | M7 | T-7.4 | Modal RTL tests |
| FR-075 | M7 | T-7.3 | Single-call test |
| FR-076, FR-077 | M7 | T-7.3, T-7.5 | State machine tests; immutability triggers |
| FR-078 | M7 | T-7.3 | No-retry CI grep + test |
| FR-079 | M7 | T-7.3, T-7.5 | Two-step resolution; new-attempt-key test (scenario 15) |
| FR-080 | M5*/M7 | T-7.5 (*ship in MVP) | One test per §15 fallback-matrix row |
| FR-081 | M7 | T-7.6 | Gate-missing 409 tests |
| FR-090 | M0 | T-0.4 | Append-only audit test |
| FR-091 | M8 | T-8.2 | Export schema test |
| FR-092 | M8 | T-8.1 | Purge + VACUUM byte scan |
| FR-093 | M1 | T-1.1, T-1.5 | `/mcp/status` test |
| NFR-001 | M0 | T-0.2 | Non-loopback startup failure |
| NFR-002 | M0 | T-0.5 | Global response-scan test |
| NFR-003 | M1 | T-1.4 | Serialization test |
| NFR-004, NFR-005 | M1 | T-1.4 | Delay + budget tests |
| NFR-006 | M1 | T-1.1 | Timeout ordering test |
| NFR-007 | M0/M1 | T-0.3, T-1.4 | Write-before-parse test |
| NFR-008 | M4 | T-4.2 | Determinism test (100 runs) |
| NFR-009 | M0 | T-0.3 | Permission test |
| NFR-010 | M0/M1 | T-0.4, T-1.4 | Correlation-id test |
| NFR-011 | M7 | T-7.4 | Keyboard/focus tests |
| NFR-012 | M7 | T-7.6 | Gate verification test |
| NFR-013 | all | every task | `git diff --stat` at acceptance |
| NFR-014 | M3 | T-3.1, T-3.3 | Memory-bound parser test |
| NFR-015 **[LD-07]** | M3/M4/M6 | T-3.4, T-4.3, T-6.3 | Span integrity test (byte-for-byte) |
| NFR-016 **[LD-08]** | M0–M5 | T-3.4, T-6.1 | Full suite green with `LLM_PROVIDER=null` |
| LD-01…LD-08 | all | §1a guards | §29 items 2, 5, 8, 12, 15, 17–21 and 23 |
| NG-1…NG-15 | all | §29 checklist | Milestone acceptance review |
