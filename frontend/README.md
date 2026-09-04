# LinkedIn Dashboard frontend

Local React/Vite workspace for role briefs, read-only discovery, staged profile retrieval,
and evidence-based ranking. It proxies `/api` only to the loopback backend and contains no
LinkedIn or MCP credentials.

```bash
npm ci
npm test
npm run lint
npm run build
```

## M4 API boundary

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

Real-browser checks run on the isolated loopback port 5194 with the installed Chrome:

```bash
npm run test:e2e
```
