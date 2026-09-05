# LinkedIn Dashboard frontend

Local React/Vite workspace for role briefs, read-only discovery, staged profile retrieval,
and evidence-based ranking. It proxies `/api` only to the loopback backend and contains no
LinkedIn or MCP credentials.

```bash
npm ci
npm run dev
```

Vite defaults to `http://127.0.0.1:5173` and proxies `/api` to the local dashboard
API on port 8787. See the [root README](../README.md#run-locally) for the three-service
setup and numeric-loopback configuration rules.

## Screens and code ownership

| Route | Screen | Main implementation |
| --- | --- | --- |
| `/brief` | Role description and editable criteria | `src/pages/BriefPage.tsx`, `src/search-setup.css` |
| `/search` | Discovery, card/list views, retrieval, and candidate-list review | `src/pages/SearchPage.tsx`, `src/results.css` |
| `/candidates` | Ranked cards, comparison, and scoring settings | `src/pages/CandidatesPage.tsx`, `src/components/CandidateRow.tsx` |
| `/candidates/:id` | Candidate review drawer | `src/pages/CandidateDetailPage.tsx`, `src/components/CandidateOverview.tsx`, `src/candidate-profile.css` |
| `/saved` | Saved runs with three-profile previews | `src/pages/SavedSearchesPage.tsx`, `src/saved-searches.css` |
| `/how-it-works` | Guide overview | `src/learn/LearnHome.tsx` |
| `/how-it-works/:chapter` | Interactive chapter or guided tour | `src/learn/LearnPage.tsx`, `src/learn/content.ts` |

`src/components/RankedPoolList.tsx` adds the score-ranked list in Find candidates.
It preserves the backend’s descending score/tie order, puts unscored pool entries
last, and scopes rank positions to the selected run before applying the name filter.
Ranking is fetched only after Gate A and only when the ranked view is selected.
Queue revisions refresh the scores; loading/error states do not become fake zero scores.

`src/App.tsx` owns navigation and shared selection state; `src/routing.ts` parses
URLs. Candidate drawers preserve their originating page. The [design reference](DESIGN.md)
defines the shared shell, typography, pills, disclosures, and status colors.

Search keywords come from the saved brief through `src/searchSettings.ts`.
Nice-to-haves are used when primary terms are absent. Query construction keeps
up to four normalized unique terms within 500 characters. Multiple locations
are stored as semicolon-separated alternatives and queued separately.
Company and network preferences use browser storage keyed to the saved brief.

## How it works guide

The guide is lazy-loaded separately from the working screens. It contains eleven
chapters across basics, working with results, and guided tours. Its fixtures and
practice state stay inside `src/learn`; exercises must not import API mutations,
write real brief or candidate data, or trigger retrieval.

Guide styles use the scoped Tailwind configuration in `tailwind.learn.config.cjs`
and `src/learn/learn.css`. The PostCSS configuration resolves that file relative
to its own directory, including when checks are launched from the repository root.
Shared CSS changes should be checked against both the working pages and the guide.

When a working flow changes, update its chapter labels, control order, and
interaction together. The guide must distinguish text matches, missing evidence,
and separately confirmed source verification just as the real screens do.

## Verification and documentation screenshots

```bash
npm test
npm run lint
npm run build
npm run test:e2e
npm run docs:screenshots
```

Rendered/component checks include guide behavior, brief editing, query generation,
saved-run filtering, and comparison. Chrome tests use isolated port 5194 with
intercepted APIs; documentation capture uses port 5195 and fictional fixtures.
Neither requires the live connector. See the [screenshot guide](../docs/screenshots/README.md)
for capture settings and the [project review](../docs/reviews/2026-09-04-project-review.md)
for dated results and known limitations, including unsaved brief drafts.

## API and evidence boundary

The TypeScript DTOs in `src/api/client.ts` are the frozen WP2/WP3 integration boundary.
Discovery uses `GET /api/candidate-pool` before Gate A; `GET /api/candidates` is ranking-only
and must remain blocked until Gate A. `SessionRecord.phase_gates` is keyed by `A`, `B`, and
`C`; each recorded gate supplies `{gate, accepted_at, note, evidence_ids}`.

Evidence offsets are zero-based, half-open Unicode code-point offsets. Opening a source span
does not verify it. Gate B sends only the IDs separately checked by the operator, and the
backend must revalidate those current exact-profile evidence IDs transactionally.
Client verification state is also bound to `(session_id, score_id, input_fingerprint)` and is
discarded whenever that identity changes. Candidate details use `/candidates/:id`, including
direct loads and browser history, but all scoring fields remain hidden before Gate A even if a
malformed response includes them.

Profile evidence uses a discriminated `availability` value: `available`, `masked` with a safe
reason, or `raw_purged` with a reason and purge timestamp. Masked and purged evidence can never
be selected for Gate B.

Scores and context remain separate: network filters, profile URNs, search provenance, and
messageability hints are displayed as `non_scoring_hints` and never appear in a signal or
weight control.

The only accepted weight keys, in display and request order, are S-1 through S-6 and S-8.
Unexpected or missing keys are treated as contract errors before a `PUT` can be issued; S-7 has
no weight control.


### Review workflow

The list check confirms names and source searches before ranking. Its successful
submission stays on Find candidates and selects Ranked list. It never certifies
candidate qualifications.

CandidateDetailPage puts EvidencePanel before career history. SourceCheck loads
the selected passage inline and enables its checkbox only when candidate, section,
span identity, provenance, and Unicode bounds match the current evidence. Opening
text never verifies it or changes its score. A failed or stale source cannot be
checked. The existing score-fingerprint reconciliation still clears stale checks.

Compare matches exposes Record source checks above the results as an optional
audit. The existing ten-current-evidence requirement is unchanged; the note starts
empty. Comparison does not require this audit. How it works teaches these same
controls and distinguishes identity checks, candidate assessment, and source checks.
