import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { JSDOM } from 'jsdom'
import React from 'react'
import { createServer } from 'vite'

const root = fileURLToPath(new URL('..', import.meta.url))
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://127.0.0.1:5173',
})
globalThis.window = dom.window
globalThis.document = dom.window.document
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator })
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.Node = dom.window.Node
globalThis.getComputedStyle = dom.window.getComputedStyle
globalThis.requestAnimationFrame = (callback) => dom.window.setTimeout(() => callback(Date.now()), 0)
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const { cleanup, render, screen, waitFor, within } = await import('@testing-library/react')
const userEvent = (await import('@testing-library/user-event')).default
const vite = await createServer({
  root,
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  server: { hmr: false, middlewareMode: true },
})
const { CandidatesPage } = await vite.ssrLoadModule('/src/pages/CandidatesPage.tsx')
const { CandidateDetailPage } = await vite.ssrLoadModule('/src/pages/CandidateDetailPage.tsx')
const { EvidencePanel } = await vite.ssrLoadModule('/src/components/EvidencePanel.tsx')
const { missingReasonCopy } = await vite.ssrLoadModule('/src/components/scoringCopy.ts')
const { WeightsEditor } = await vite.ssrLoadModule('/src/components/WeightsEditor.tsx')
const { SearchPage } = await vite.ssrLoadModule('/src/pages/SearchPage.tsx')
await vite.close()

test.after(async () => {
  cleanup()
  dom.window.close()
})
test.afterEach(() => cleanup())

function wrapper(child) {
  return React.createElement(
    QueryClientProvider,
    { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
    child,
  )
}

function json(value, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(value), {
    status,
    headers: { 'content-type': 'application/json' },
  }))
}

const session = {
  id: 'session',
  phase_gates: { A: { gate: 'A', accepted_at: 'now', note: 'checked', evidence_ids: [] } },
}
const queue = {
  state: 'active', pause_reason: null, resume_at: null, counts: {}, jobs: [],
  connected: true, revision: 0, scoringRevision: 0, lastEventAt: null,
}
const config = {
  version: 'v3',
  weights: { 'S-1': 30, 'S-2': 10, 'S-3': 20, 'S-4': 15, 'S-5': 10, 'S-6': 8, 'S-8': 0 },
  active_signal_ids: ['S-1'],
  inert_reasons: { 'S-8': { code: 'brief_input_empty', message: 'brief input is empty' } },
  metro_region_equivalences: {},
}
function ranked(overrides) {
  return {
    id: 'numeric', score_id: 'score-v1', input_fingerprint: 'fingerprint-v1',
    username: 'numeric', profile_url: '/in/numeric', display_name: 'Numeric',
    headline: 'Platform leader',
    stage: 'provisional', score: 82.5, score_lower: 71, score_upper: 89,
    previous_score: 80, delta: 2.5, confidence: 0.75, confidence_band: 'high',
    calculation_status: 'scored', active_signal_count: 4, all_inert_attested: false,
    weights_version: 'v3',
    top_signals: [
      { signal_id: 'S-1', label: 'Required skills', contribution: 27, rollup: 'matched' },
      { signal_id: 'S-2', label: 'Optional skills', contribution: 8, rollup: 'matched' },
      { signal_id: 'S-3', label: 'Experience', contribution: 7, rollup: 'matched' },
      { signal_id: 'S-4', label: 'Fourth hidden signal', contribution: 6, rollup: 'matched' },
    ],
    non_scoring_hints: [{ kind: 'network', label: 'Search network', value: 'F/S' }],
    ...overrides,
  }
}

test('every canonical missing reason has distinct exhaustive copy', () => {
  assert.deepEqual(
    ['not_requested', 'rate_limit', 'fetch_error', 'unparseable'].map(missingReasonCopy),
    [
      'not requested',
      'not retrieved because a rate limit stopped the request',
      'could not be retrieved',
      'retrieved, but could not be parsed reliably',
    ],
  )
})

test('candidate-pool inspection records Gate A before ranking navigation', async () => {
  let gateBody = null
  let changed = false
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([{
      id: 'run', job_id: 'job', brief_id: 'brief', created_at: '2026-01-01T00:00:00Z',
      keywords: 'platform', location: null, network: ['F', 'S'], current_company: null,
      status: 'ok', reference_count: 1, person_reference_count: 1,
      new_candidate_count: 1, existing_candidate_count: 0,
    }])
    if (path.startsWith('/api/candidate-pool?')) return json([])
    if (path === '/api/session/gates/A') {
      gateBody = JSON.parse(init.body)
      return json({ gate: 'A', accepted_at: 'now', note: gateBody.note, evidence_ids: [] })
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(SearchPage, {
    session: { id: 'session' }, brief: null, queue,
    onCandidateOpen() {}, onGateAChanged() { changed = true },
  })))
  const accept = await screen.findByRole('button', { name: 'Accept Gate A and unlock ranking' })
  await waitFor(() => assert.equal(accept.disabled, false))
  accept.focus()
  await user.keyboard('{Enter}')
  await waitFor(() => assert.equal(changed, true))
  assert.equal(gateBody.note, 'Candidate extraction and dedupe inspected.')
})

test('Gate A accepts only persisted eligible search outcomes', async () => {
  const cases = [
    ['ok', false, 'eligible persisted search result'],
    ['partial', false, 'eligible persisted search result'],
    ['rate_limited', false, 'eligible persisted search result'],
    ['queued', true, 'queued searches have not started'],
    ['running', true, 'running searches have not finished'],
    ['failed', true, 'failed searches produced no eligible result'],
    ['interrupted', true, 'interrupted searches did not persist an eligible result'],
    ['cancelled', true, 'cancelled searches did not persist an eligible result'],
  ]
  for (const [status, disabled, explanation] of cases) {
    globalThis.fetch = (input) => {
      const path = String(input)
      if (path.startsWith('/api/searches?')) return json([{
        id: `run-${status}`, job_id: `job-${status}`, brief_id: 'brief',
        created_at: '2026-01-01T00:00:00Z', keywords: status, location: null,
        network: [], current_company: null, status, reference_count: 0,
        person_reference_count: 0, new_candidate_count: 0,
        existing_candidate_count: 0,
      }])
      if (path.startsWith('/api/candidate-pool?')) return json([])
      throw new Error(`unexpected fetch ${path}`)
    }
    const rendered = render(wrapper(React.createElement(SearchPage, {
      session: { id: 'session' }, brief: null, queue,
      onCandidateOpen() {}, onGateAChanged() {},
    })))
    const button = await screen.findByRole('button', {
      name: /Accept Gate A and unlock ranking/,
    })
    await waitFor(() => assert.equal(button.disabled, disabled))
    assert.equal(
      document.getElementById('gate-a-eligibility').textContent.includes(explanation),
      true,
    )
    rendered.unmount()
    cleanup()
  }
})

test('ranked list distinguishes stage and both null-score forms without color', async () => {
  const candidates = [
    ranked({}),
    ranked({ id: 'unknown', display_name: 'Unknown', stage: 'enriched', score: null,
      score_lower: null, score_upper: null, previous_score: null, delta: null,
      confidence: 0, confidence_band: null, calculation_status: 'unknown',
      top_signals: [] }),
    ranked({ id: 'inert', display_name: 'Inert', stage: 'enriched', score: null,
      score_lower: null, score_upper: null, previous_score: null, delta: null,
      confidence: 0, confidence_band: 'low', calculation_status: 'unknown',
      active_signal_count: 0, all_inert_attested: true, top_signals: [] }),
  ]
  let opened = null
  let lastUrl = ''
  globalThis.fetch = (input) => {
    lastUrl = String(input)
    if (lastUrl.startsWith('/api/candidates?')) return json(candidates)
    if (lastUrl === '/api/weights') return json(config)
    throw new Error(`unexpected fetch ${lastUrl}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(CandidatesPage, {
    session,
    verifiedEvidence: new Map(), onEvidenceReconciled() {}, onScoresChanged() {},
    onCandidateOpen(id) { opened = id },
  })))
  const list = await screen.findByLabelText('Ranked candidates')
  const cards = within(list).getAllByRole('article')
  const numericCard = cards.find((card) => card.textContent.includes('Numeric'))
  const unknownCard = cards.find((card) => card.textContent.includes('Unknown'))
  const inertCard = cards.find((card) => card.textContent.includes('Inert'))
  assert.equal(numericCard.textContent.includes('◐ Provisional'), true)
  assert.equal(unknownCard.textContent.includes('◆ Enriched'), true)
  assert.equal(unknownCard.textContent.includes('not found in the retrieved data'), true)
  assert.equal(inertCard.textContent.includes('Not scored — no active scoring criteria'), true)
  assert.equal(inertCard.textContent.includes('Low confidence (0%)'), true)
  assert.equal(numericCard.textContent.includes('Search network: F/S · non-scoring'), true)
  assert.equal(numericCard.textContent.includes('Fourth hidden signal'), false)
  assert.equal(cards[0], numericCard, 'numeric scores sort before both null-score forms')
  const open = within(numericCard).getByRole('button', { name: /Open evidence/ })
  open.focus()
  await user.keyboard('{Enter}')
  assert.equal(opened, 'numeric')
  await user.selectOptions(screen.getByLabelText('Sort order'), 'confidence_desc')
  await waitFor(() => assert.equal(lastUrl.includes('sort=confidence_desc'), true))
})

test('name sort ignores score nullability and uses stable id ties', async () => {
  const ada = ranked({
    id: 'ada-2', username: 'ada', display_name: 'Ada', score: null,
    score_lower: null, score_upper: null, previous_score: null, delta: null,
    confidence: 0, confidence_band: null, calculation_status: 'unknown',
    top_signals: [],
  })
  const zoe = ranked({ id: 'zoe', username: 'zoe', display_name: 'Zoe' })
  const adaTie = ranked({
    id: 'ada-1', username: 'ada-one', display_name: 'Ada', score: null,
    score_lower: null, score_upper: null, previous_score: null, delta: null,
    confidence: 0, confidence_band: null, calculation_status: 'unknown',
    top_signals: [],
  })
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path.startsWith('/api/candidates?')) return json([zoe, ada, adaTie])
    if (path === '/api/weights') return json(config)
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  let opened = null
  render(wrapper(React.createElement(CandidatesPage, {
    session, verifiedEvidence: new Map(), onEvidenceReconciled() {},
    onScoresChanged() {}, onCandidateOpen(id) { opened = id },
  })))
  await screen.findByText('Zoe')
  await user.selectOptions(screen.getByLabelText('Sort order'), 'name_asc')
  await waitFor(() => {
    const cards = within(screen.getByLabelText('Ranked candidates')).getAllByRole('article')
    assert.deepEqual(
      cards.map((card) => card.querySelector('h3').textContent),
      ['Ada', 'Ada', 'Zoe'],
    )
  })
  const orderedButtons = within(screen.getByLabelText('Ranked candidates'))
    .getAllByRole('button', { name: /Open evidence/ })
  await user.click(orderedButtons[0])
  assert.equal(opened, 'ada-1')
})

test('evidence opening and verification are separate; unknown and masked states are exact', async () => {
  const calls = []
  const signals = [{
    id: 'signal', signal_id: 'S-1', label: 'Required skills', rollup: 'mixed',
    weight: 30, raw_subscore: 0.5, contribution: 15, availability: 0.5,
    claims: [
      { id: 'match', claim_key: 'required:go', display_term: 'Go', verdict: 'matched',
        evidence: [{ id: 'e1', section_name: 'experience', profile_section_id: 'p1',
          span_start: 9, span_end: 16, snippet: '🚀 Alpha', matched_term: 'Alpha', matcher: 'exact',
          polarity: 'supporting', availability: { state: 'available' } }],
        coverage: [], missing_sections: [] },
      { id: 'unknown', claim_key: 'required:rust', display_term: 'Rust', verdict: 'unknown',
        evidence: [], coverage: [], missing_sections: [{ section_name: 'skills', reason: 'unparseable' }] },
      { id: 'masked', claim_key: 'required:masked', display_term: 'Masked', verdict: 'matched',
        evidence: [{ id: 'masked-e', section_name: 'experience', profile_section_id: 'p1',
          span_start: 1, span_end: 4, snippet: 'secret', matched_term: 'secret', matcher: 'exact',
          polarity: 'supporting', availability: { state: 'masked', reason: 'Masked diagnostic overlap.' } }],
        coverage: [], missing_sections: [] },
      { id: 'purged', claim_key: 'required:purged', display_term: 'Purged', verdict: 'matched',
        evidence: [{ id: 'purged-e', section_name: 'experience', profile_section_id: 'p1',
          span_start: 1, span_end: 4, snippet: 'gone', matched_term: 'gone', matcher: 'exact',
          polarity: 'supporting', availability: { state: 'raw_purged', reason: 'Session raw text was manually purged.', purged_at: '2026-01-02T03:04:05Z' } }],
        coverage: [], missing_sections: [] },
    ],
  }]
  const user = userEvent.setup({ document: dom.window.document })
  render(React.createElement(EvidencePanel, {
    signals,
    allInert: false,
    verifiedEvidenceIds: new Set(),
    onEvidenceOpen(sectionName, evidenceId) { calls.push(['open', sectionName, evidenceId]) },
    onEvidenceVerified(evidenceId, verified) { calls.push(['verify', evidenceId, verified]) },
  }))
  assert.equal(screen.getByText('not found in the retrieved data', { exact: false }).textContent.includes('not found in the retrieved data'), true)
  const unparseable = screen.getByText('Rust').closest('.claim-card')
  assert.equal(unparseable.textContent.includes('retrieved, but could not be parsed reliably'), true)
  assert.equal(unparseable.textContent.includes('Searched every required retrieved section'), false)
  assert.equal(screen.getByText(/Evidence withheld/).textContent, 'Evidence withheld')
  assert.equal(screen.getByText(/Raw text purged on/).textContent.includes('Raw text purged on'), true)
  assert.equal(screen.getByText(/Session raw text was manually purged/).textContent.includes('manually purged'), true)
  assert.equal(screen.getByText('This does not mean the candidate lacks this qualification.').textContent.length > 0, true)
  assert.equal(screen.queryByRole('button', { name: /secret/ }), null)
  const evidence = screen.getByRole('button', { name: /🚀 Alpha/ })
  evidence.focus()
  await user.keyboard('{Enter}')
  assert.deepEqual(calls, [['open', 'experience', 'e1']])
  const verify = screen.getByRole('checkbox', { name: /^I verified this exact source span for/ })
  verify.focus()
  await user.keyboard(' ')
  assert.deepEqual(calls[1], ['verify', 'e1', true])
})

test('ten evidence controls have unique accessible names and remain keyboard operable', async () => {
  const calls = []
  const evidence = Array.from({ length: 10 }, (_, index) => ({
    id: `evidence-${index + 1}`,
    section_name: index % 2 ? 'skills' : 'experience',
    profile_section_id: `section-${index + 1}`,
    span_start: index,
    span_end: index + 1,
    snippet: `Evidence ${index + 1}`,
    matched_term: 'Platform',
    matcher: 'exact',
    polarity: 'supporting',
    availability: { state: 'available' },
  }))
  const signals = [{
    id: 'signal-ten', signal_id: 'S-1', label: 'Required skills',
    rollup: 'matched', weight: 30, raw_subscore: 1, contribution: 30,
    availability: 1, claims: [{
      id: 'claim-ten', claim_key: 'required:platform', display_term: 'Platform',
      verdict: 'matched', evidence, coverage: [], missing_sections: [],
    }],
  }]
  const user = userEvent.setup({ document: dom.window.document })
  render(React.createElement(EvidencePanel, {
    signals, allInert: false, verifiedEvidenceIds: new Set(),
    onEvidenceOpen() {},
    onEvidenceVerified(id, checked) { calls.push([id, checked]) },
  }))
  const checkboxes = screen.getAllByRole('checkbox', {
    name: /^I verified this exact source span for/,
  })
  assert.equal(checkboxes.length, 10)
  assert.equal(new Set(checkboxes.map((checkbox) => checkbox.getAttribute('aria-label'))).size, 10)
  assert.equal(screen.getAllByText('I verified this exact source span').length, 10)
  checkboxes[9].focus()
  await user.keyboard(' ')
  assert.deepEqual(calls, [['evidence-10', true]])
})

test('backend scoring empty state replaces empty evidence while all-inert stays explicit', async () => {
  const baseDetail = {
    id: 'empty', username: 'empty', profile_url: '/in/empty',
    display_name: 'Empty Candidate', profile_urn: null,
    profile_urn_is_scored: false, profile_urn_quarantined: false,
    profile_urn_routing_allowed: false, profile_contract_error: null,
    stage: 'stage1', retrieval_status: 'ok', active_job_id: null,
    available_sections: {}, fields: [], fetches: [], errors: [], score: null,
    score_history: [], signals: [], non_scoring_hints: [],
    scoring_empty_state: 'Backend says scoring input is unavailable.',
  }
  const inertDetail = {
    ...baseDetail,
    id: 'inert-detail',
    username: 'inert',
    display_name: 'Inert Candidate',
    score: ranked({
      id: 'inert-detail', username: 'inert', display_name: 'Inert Candidate',
      score: null, score_lower: null, score_upper: null, previous_score: null,
      delta: null, confidence: 0, confidence_band: 'low',
      calculation_status: 'unknown', active_signal_count: 0,
      all_inert_attested: true, top_signals: [],
    }),
    scoring_empty_state: 'This must not replace the all-inert explanation.',
  }
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path === '/api/profile-sections') return json([])
    if (path === '/api/candidates/empty') return json(baseDetail)
    if (path === '/api/candidates/inert-detail') return json(inertDetail)
    throw new Error(`unexpected fetch ${path}`)
  }
  const props = {
    backDestination: 'candidates', onBack() {}, queue, rankingUnlocked: true,
    sessionId: 'session', verifiedEvidence: new Map(), onEvidenceVerified() {},
    onScoreInputsChanged() {},
  }
  const rendered = render(wrapper(React.createElement(CandidateDetailPage, {
    ...props, candidateId: 'empty',
  })))
  await screen.findByText('Backend says scoring input is unavailable.')
  assert.equal(screen.queryByRole('heading', { name: 'Why this score changed' }), null)

  rendered.rerender(wrapper(React.createElement(CandidateDetailPage, {
    ...props, candidateId: 'empty', rankingUnlocked: false,
  })))
  await waitFor(() => assert.equal(
    screen.queryByText('Backend says scoring input is unavailable.'),
    null,
  ))

  rendered.rerender(wrapper(React.createElement(CandidateDetailPage, {
    ...props, candidateId: 'inert-detail',
  })))
  await screen.findByRole('heading', { name: 'No active scoring criteria' })
  assert.equal(screen.queryByText('This must not replace the all-inert explanation.'), null)
})

test('Gate B posts only the separately verified exact evidence ids', async () => {
  const verified = new Map(Array.from({ length: 10 }, (_, index) => [
    `e${index}`,
    { evidenceId: `e${index}`, sessionId: 'session', scoreId: 'score-v1', inputFingerprint: 'fingerprint-v1' },
  ]))
  let payload = null
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/candidates?')) return json([ranked({})])
    if (path === '/api/weights') return json(config)
    if (path === '/api/session/gates/B') {
      payload = JSON.parse(init.body)
      return json({ gate: 'B', accepted_at: 'now', note: payload.note, evidence_ids: payload.evidence_ids })
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(CandidatesPage, {
    session,
    verifiedEvidence: verified, onEvidenceReconciled() {}, onScoresChanged() {},
    onCandidateOpen() {},
  })))
  const button = await screen.findByRole('button', { name: 'Accept Gate B' })
  await waitFor(() => assert.equal(button.disabled, false))
  button.focus()
  await user.keyboard('{Enter}')
  await waitFor(() => assert.equal(payload.evidence_ids.length, 10))
  assert.deepEqual(new Set(payload.evidence_ids), new Set(verified.keys()))
})

test('verification sink reconciles only a complete unfiltered two-candidate dataset', async () => {
  const first = ranked({
    id: 'first', score_id: 'score-first', input_fingerprint: 'fingerprint-first',
    username: 'first', display_name: 'First Candidate',
  })
  const second = ranked({
    id: 'second', score_id: 'score-second', input_fingerprint: 'fingerprint-second',
    username: 'second', display_name: 'Second Candidate',
  })
  const initial = new Map([
    ['evidence-first', {
      evidenceId: 'evidence-first', sessionId: 'session', scoreId: 'score-first',
      inputFingerprint: 'fingerprint-first',
    }],
    ['evidence-second', {
      evidenceId: 'evidence-second', sessionId: 'session', scoreId: 'score-second',
      inputFingerprint: 'fingerprint-second',
    }],
    ['stale-evidence', {
      evidenceId: 'stale-evidence', sessionId: 'session', scoreId: 'old-score',
      inputFingerprint: 'old-fingerprint',
    }],
  ])
  const requested = []
  let releaseComplete
  const pendingComplete = new Promise((resolve) => {
    releaseComplete = () => resolve(new Response(JSON.stringify([first, second]), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))
  })
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path === '/api/weights') return json(config)
    if (path.startsWith('/api/candidates?')) {
      requested.push(path)
      const params = new URL(path, 'http://local').searchParams
      if (params.get('confidence') === 'high') return json([first])
      if (params.get('confidence') === 'low') return json({ detail: 'failed' }, 500)
      return pendingComplete
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  let sink = initial
  const sinkSnapshots = []
  function Harness() {
    const [verified, setVerified] = React.useState(initial)
    return React.createElement(CandidatesPage, {
      session,
      verifiedEvidence: verified,
      onEvidenceReconciled(values) {
        sink = values
        sinkSnapshots.push(new Map(values))
        setVerified(values)
      },
      onScoresChanged() {},
      onCandidateOpen() {},
    })
  }

  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(Harness)))
  await screen.findByText('Calculating ranked evidence…')
  assert.equal(sinkSnapshots.length, 0, 'pending data cannot reach the sink')
  releaseComplete()
  await screen.findByText('First Candidate')
  await waitFor(() => assert.equal(sink.size, 2))
  assert.deepEqual(new Set(sink.keys()), new Set(['evidence-first', 'evidence-second']))
  assert.equal(screen.getByText('2 / 10').textContent.includes('2 / 10'), true)
  const completeSinkCalls = sinkSnapshots.length

  await user.selectOptions(screen.getByLabelText('Confidence'), 'high')
  await waitFor(() => assert.equal(requested.at(-1).includes('confidence=high'), true))
  await screen.findByText('First Candidate')
  assert.equal(screen.queryByText('Second Candidate'), null)
  assert.equal(sinkSnapshots.length, completeSinkCalls)
  assert.equal(sink.size, 2, 'a filtered subset cannot prune a valid second selection')

  await user.selectOptions(screen.getByLabelText('Confidence'), 'low')
  await screen.findByRole('alert')
  assert.equal(sinkSnapshots.length, completeSinkCalls)
  assert.equal(sink.size, 2, 'an error response cannot become reconciliation input')
})

test('weights save uses the loaded optimistic version and never offers S-7 input', async () => {
  let payload = null
  let scoresChanged = 0
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path === '/api/weights' && !init?.method) return json(config)
    if (path === '/api/weights/current') {
      payload = JSON.parse(init.body)
      return json({ ...config, version: 'v4', weights: payload.weights })
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(WeightsEditor, {
    onScoresChanged() { scoresChanged += 1 },
  })))
  await user.click(await screen.findByText('Scoring weights'))
  const required = screen.getByLabelText('Required skills weight')
  await user.clear(required)
  await user.type(required, '31')
  await user.click(screen.getByRole('button', { name: 'Save from v3' }))
  await waitFor(() => assert.equal(payload.expected_version, 'v3'))
  assert.equal(payload.weights['S-1'], 31)
  assert.equal(scoresChanged, 1)
  assert.equal(screen.queryByLabelText(/Network context weight/), null)
  assert.equal(screen.getByText('Search only — not a scoring criterion.').textContent.length > 0, true)
})

test('hostile unexpected weight key is a contract error and never reaches PUT', async () => {
  let writes = 0
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path === '/api/weights' && !init?.method) {
      return json({ ...config, weights: { ...config.weights, 'S-7': 99 } })
    }
    if (init?.method === 'PUT') writes += 1
    throw new Error(`unexpected fetch ${path}`)
  }
  render(wrapper(React.createElement(WeightsEditor, { onScoresChanged() {} })))
  const alert = await screen.findByRole('alert')
  assert.equal(alert.textContent.includes('unexpected weight keys S-7'), true)
  assert.equal(writes, 0)
  assert.equal(screen.queryByRole('button', { name: /Save from/ }), null)
})
