# Documentation screenshots

These PNGs show the working Compass UI with fictional people, companies, search
results, and score data. They contain no saved user profiles. Values illustrate
the interface and are not evidence of a live search, retrieval, or scoring run.

| Image | Screen |
| --- | --- |
| [candidate-results.png](candidate-results.png) | Reviewed candidate cards and comparison controls |
| [ranked-list.png](ranked-list.png) | Find candidates in score-ranked list view |
| [role-brief.png](role-brief.png) | Criteria, repeated locations, year stepper, and optional preferences |
| [candidate-review.png](candidate-review.png) | Score-first candidate drawer |
| [saved-searches.png](saved-searches.png) | Three-profile saved-run preview |
| [how-it-works.png](how-it-works.png) | Interactive guide overview |

## Refresh

Install frontend dependencies and Google Chrome, then run from `frontend/`:

```bash
npm run docs:screenshots
```

The [capture script](../../frontend/scripts/capture-docs.mjs) starts Vite on
`127.0.0.1:5195` and renders real application components at 1440 × 1000 with
bundled fonts, a fixed locale/timezone, and reduced motion. It uses a fresh browser
context, supplies fictional GET responses, aborts event streams, and rejects
unexpected API operations and external requests. It does not start or access the
dashboard backend, connector, or database. Chrome and the temporary Vite process
close after capture, including on failure.

Port 5195 must be free. The command needs permission to start a local listener and
Chrome when run in a restricted environment. Inspect every output after changes;
do not replace these assets with screenshots containing live profile information.

The root README embeds these files with repository-relative paths so GitHub and
local Markdown renderers can display them without external image hosting.
