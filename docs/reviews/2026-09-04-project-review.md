# Project review — 4 September 2026

Reviewed the working tree on `codex/dashboard-ready-today`, including the uncommitted How it works integration. This was a code, behavior, and design review with automated regression checks and read-only inspection of the running app. It was not a new live LinkedIn retrieval or a formal security audit.

## Remaining findings

### P2 — Unsaved brief edits are discarded when navigating away

`frontend/src/pages/BriefPage.tsx:96` holds editable criteria in component state. Its dirty flag changes the form presentation, but the app unmounts the page when another sidebar destination is selected (`frontend/src/App.tsx:269`). Returning initializes the form from the last saved brief.

**Impact:** editing a location or skill, opening How it works or Saved searches, and returning loses the edit. Browser reload also loses it.

**Recommended change:** retain a session-scoped draft keyed to the saved brief version and restore it on return, with an explicit discard action. Clear it after successful save and isolate it from another session or newer brief version. This review did not add draft persistence.

### P2 — A first credential-only brief still requires a scoring setup detour

Credentials now count as a valid discovery criterion. However, S-8 defaults to zero (`backend/linkedin_dashboard/services/scoring/types.py:256`), and saving a brief with active signals but no positive active weight is correctly rejected. The weights editor is in the comparison workflow, so a fresh credential-only brief is not a complete first-run path.

**Impact:** users must first save another supported criterion alongside the credential, reach the scoring settings, assign a positive credential weight, and then remove the other criterion.

**Changed:** the API now explains the positive-weight requirement, and a regression test covers the valid configured case and protected-term rejection. The scoring invariant and default weights were preserved.

**Recommended change:** make credential weight configuration reachable from brief setup, or explicitly support discovery before scoring configuration. Choose this behavior deliberately rather than silently assigning a weight.

## Issues corrected in this review

| Finding | Correction |
| --- | --- |
| Nice-to-have-only briefs passed validation but produced an empty search query | Use optional skills when no primary search terms exist; do not add them as extra requirements to an existing primary query. |
| Equivalent query terms consumed slots, and long combined terms could exceed the search API limit | Deduplicate normalized terms and keep whole terms within the existing four-term and 500-character limits. |
| Credential-only briefs failed discovery validation even with usable scoring configuration | Include required credentials in discovery validation; retain scoring and protected-criterion checks. |
| Results omitted nice-to-haves and displayed minimum experience in months | Show labeled nice-to-have criteria and years, matching the brief. |
| Guide examples still described older brief and candidate-review layouts | Update criteria examples, optional preferences, action names, review order, and result exercises to follow the working pages. Exercises remain fictional and local. |
| Guide styles depended on starting tools from the frontend directory | Resolve the Tailwind configuration relative to the PostCSS configuration file. |
| Long API labels overflowed mobile result cards | Allow labels to shrink and wrap inside their card, including flex rows. |
| Long sticky drawer headlines covered review actions | Let the profile header scroll with the drawer while retaining the fixed close control. Wrap long drawer content and preserve intact table headings. |
| Browser tests still targeted removed controls and old verdict glyphs | Update selectors and interactions to the current disclosures, drawer navigation, neutral cards, status badges, and brief controls. Keep behavioral assertions. |
| README and design notes contradicted the current guide and score placement | Distinguish guide exercises from working data and document card versus drawer ordering. |

## Verification

- Full backend baseline: **1,403 passed, 3 skipped**. This completed while review changes were in progress; afterward, **59 affected discovery/scoring API tests passed**, including the new credential-only regression. Counts overlap and should not be added together.
- Frontend tests: **85 passed**, including the guide, search query generation, rendered flows, and real IPv4/IPv6 loopback proxy checks.
- Chrome end-to-end tests: **15 passed**. Coverage includes pre-review ranking suppression, canonical ordering, route reload/back/forward, evidence verification identity, keyboard source navigation, mobile table scrolling, long API strings, and brief editing.
- Frontend production build and lint passed. Backend Ruff and type checks passed. Diff whitespace checks passed.
- The root-launched guide/search checks passed, confirming that style configuration no longer depends on the process working directory.
- Read-only browser inspection covered the live brief, saved searches, and candidate review at desktop and phone widths. Checked consistent 40px pills, fixed desktop navigation, three-profile saved previews, score-first drawer layout, and narrow-screen fit.

Tests used isolated API fixtures and temporary databases. The review did not change the user's live brief, run searches, request profile downloads, or change live scoring weights. The temporary inspection tab was closed.

The backend emitted two dependency deprecation warnings; they did not fail checks. The running backend was not restarted, so its new validation messages take effect after the next restart. Changes remain uncommitted, alongside the existing guide integration.
