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
Object.defineProperty(globalThis, 'navigator', {
  configurable: true,
  value: dom.window.navigator,
})
globalThis.HTMLElement = dom.window.HTMLElement
globalThis.Node = dom.window.Node
globalThis.getComputedStyle = dom.window.getComputedStyle
globalThis.requestAnimationFrame = (callback) =>
  dom.window.setTimeout(() => callback(Date.now()), 0)
globalThis.IS_REACT_ACT_ENVIRONMENT = true

const { cleanup, render, screen, waitFor, within } = await import(
  '@testing-library/react'
)
const userEvent = (await import('@testing-library/user-event')).default
const vite = await createServer({
  root,
  cacheDir: `node_modules/.vite-test-${process.pid}`,
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  server: { hmr: false, middlewareMode: true },
})
const { RawTextViewer } = await vite.ssrLoadModule(
  '/src/components/RawTextViewer.tsx',
)
const { CandidateDetailPage } = await vite.ssrLoadModule(
  '/src/pages/CandidateDetailPage.tsx',
)
const { useNewRevisionEffect } = await vite.ssrLoadModule(
  '/src/scoreVerification.ts',
)
const { SearchPage } = await vite.ssrLoadModule('/src/pages/SearchPage.tsx')
const { QueueStatus } = await vite.ssrLoadModule('/src/components/QueueStatus.tsx')
const { CandidateOverview } = await vite.ssrLoadModule('/src/components/CandidateOverview.tsx')
const { CandidateDrawer } = await vite.ssrLoadModule('/src/components/CandidateDrawer.tsx')
const { searchLocations } = await vite.ssrLoadModule('/src/searchLocations.ts')
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
  return Promise.resolve(
    new Response(JSON.stringify(value), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  )
}

const queue = {
  state: 'active',
  pause_reason: null,
  resume_at: null,
  counts: {},
  jobs: [],
  connected: true,
  revision: 0,
  scoringRevision: 0,
  lastEventAt: null,
}

test('raw viewer marks exact repeated astral span and withholds overlap', () => {
  const section = {
    candidate_id: 'candidate',
    section_name: 'experience',
    profile_section_id: 'section',
    raw_text: '🚀 Alpha 🚀 Alpha',
    span_unit: 'unicode_code_point',
    spans: [
      {
        id: 'second',
        field_key: 'experience.0.title',
        profile_section_id: 'section',
        span_start: 8,
        span_end: 15,
        value: '🚀 Alpha',
        snippet: '🚀 Alpha',
        verbatim: '🚀 Alpha',
        provenance_available: true,
        provenance_label: 'Exact stored text',
      },
    ],
  }
  const rendered = render(
    React.createElement(RawTextViewer, { section, selectedFieldId: 'second' }),
  )
  assert.equal(rendered.container.querySelector('mark')?.textContent, '🚀 Alpha')
  assert.equal(rendered.container.querySelector('pre')?.textContent, section.raw_text)

  rendered.rerender(
    React.createElement(RawTextViewer, {
      section: {
        ...section,
        spans: [
          {
            ...section.spans[0],
            span_start: null,
            span_end: null,
            value: null,
            snippet: null,
            verbatim: null,
            provenance_available: false,
            provenance_label: 'Provenance withheld',
          },
        ],
      },
      selectedFieldId: 'second',
    }),
  )
  assert.equal(screen.getByRole('status').textContent.includes('Provenance withheld'), true)
  assert.equal(rendered.container.querySelector('mark'), null)
})

test('candidate detail opens zero-field sections and exact field evidence', async () => {
  const detail = {
    id: 'candidate',
    username: 'ada',
    profile_url: 'https://www.linkedin.com/in/ada/',
    display_name: 'Ada',
    profile_urn: null,
    profile_urn_is_scored: false,
    profile_urn_quarantined: false,
    profile_urn_routing_allowed: false,
    profile_contract_error: null,
    stage: 'stage1',
    retrieval_status: 'ok',
    active_job_id: null,
    available_sections: {
      experience: { profile_section_id: 'exp', retrieved_at: 'now', char_len: 17, field_count: 1 },
      honors: { profile_section_id: 'honors', retrieved_at: 'now', char_len: 20, field_count: 0 },
    },
    fields: [{
      id: 'field', field_key: 'experience.0.title', value: '🚀 Alpha',
      section_name: 'experience', profile_section_id: 'exp', span_start: 8,
      span_end: 15, snippet: '🚀 Alpha', origin: 'deterministic',
      provenance_available: true, provenance_label: 'Exact stored text',
    }],
    fetches: [], errors: [],
  }
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path === '/api/candidates/candidate') return json(detail)
    if (path === '/api/profile-sections') return json(['experience', 'honors'])
    if (path.endsWith('/sections/experience')) {
      return json({
        candidate_id: 'candidate', section_name: 'experience',
        profile_section_id: 'exp', raw_text: 'Prefix: 🚀 Alpha',
        span_unit: 'unicode_code_point', spans: [{
          id: 'field', field_key: 'experience.0.title', profile_section_id: 'exp',
          span_start: 8, span_end: 15, value: '🚀 Alpha', snippet: '🚀 Alpha',
          verbatim: '🚀 Alpha', provenance_available: true,
          provenance_label: 'Exact stored text',
        }],
      })
    }
    if (path.endsWith('/sections/honors')) {
      return json({ candidate_id: 'candidate', section_name: 'honors',
        profile_section_id: 'honors', raw_text: 'Grace Hopper Award',
        span_unit: 'unicode_code_point', spans: [] })
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(CandidateDetailPage, {
    backDestination: 'candidates', candidateId: 'candidate', onBack() {}, queue,
    rankingUnlocked: true,
    sessionId: 'session', verifiedEvidence: new Map(),
    onEvidenceVerified() {}, onScoreInputsChanged() {},
  })))
  const honorsButton = await screen.findByRole('button', { name: 'honors' })
  honorsButton.focus()
  await user.keyboard('{Enter}')
  assert.equal(honorsButton.getAttribute('aria-pressed'), 'true')
  assert.equal(
    (await screen.findByLabelText('Raw honors profile text')).textContent,
    'Grace Hopper Award',
  )
  assert.equal(screen.queryByRole('button', { name: /🚀 Alpha/ }), null)
  await user.click(screen.getByRole('button', { name: 'experience', exact: true }))
  await user.click(screen.getByRole('button', { name: /🚀 Alpha/ }))
  await waitFor(() => assert.equal(document.querySelector('mark')?.textContent, '🚀 Alpha'))
})

test('historical queue revisions and candidate remounts preserve score-bound verifications', async () => {
  function scoredDetail(id, name) {
    const evidenceId = `evidence-${id}`
    return {
      id,
      username: id,
      profile_url: `/in/${id}`,
      display_name: name,
      profile_urn: null,
      profile_urn_is_scored: false,
      profile_urn_quarantined: false,
      profile_urn_routing_allowed: false,
      profile_contract_error: null,
      stage: 'stage1',
      retrieval_status: 'ok',
      active_job_id: null,
      available_sections: {},
      fields: [], fetches: [], errors: [], score_history: [], non_scoring_hints: [],
      score: {
        id, score_id: `score-${id}`, input_fingerprint: `fingerprint-${id}`,
        username: id, profile_url: `/in/${id}`, display_name: name, headline: null,
        stage: 'provisional', score: 80, score_lower: 70, score_upper: 90,
        previous_score: null, delta: null, confidence: 0.8, confidence_band: 'high',
        calculation_status: 'scored', active_signal_count: 1,
        all_inert_attested: false, weights_version: 'v1', top_signals: [],
        non_scoring_hints: [],
      },
      signals: [{
        id: `signal-${id}`, signal_id: 'S-1', label: 'Required skills',
        rollup: 'matched', weight: 1, raw_subscore: 1, contribution: 1,
        availability: 1, claims: [{
          id: `claim-${id}`, claim_key: `required:${id}`, display_term: name,
          verdict: 'matched', coverage: [], missing_sections: [], evidence: [{
            id: evidenceId, section_name: 'experience', profile_section_id: `section-${id}`,
            span_start: 0, span_end: 4, snippet: name, matched_term: name,
            matcher: 'exact', polarity: 'supporting', availability: { state: 'available' },
          }],
        }],
      }],
    }
  }

  const details = {
    first: scoredDetail('first', 'First Candidate'),
    second: scoredDetail('second', 'Second Candidate'),
  }
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path === '/api/profile-sections') return json(['experience'])
    if (path === '/api/candidates/first') return json(details.first)
    if (path === '/api/candidates/second') return json(details.second)
    throw new Error(`unexpected fetch ${path}`)
  }
  const verified = new Map([
    ['evidence-first', {
      evidenceId: 'evidence-first', sessionId: 'session', scoreId: 'score-first',
      inputFingerprint: 'fingerprint-first',
    }],
    ['evidence-second', {
      evidenceId: 'evidence-second', sessionId: 'session', scoreId: 'score-second',
      inputFingerprint: 'fingerprint-second',
    }],
  ])
  const historicalQueue = { ...queue, revision: 7, scoringRevision: 3 }
  let scoreChanges = 0
  function Harness({ candidateId, detailKey, currentQueue }) {
    const [currentVerified, setCurrentVerified] = React.useState(verified)
    useNewRevisionEffect(currentQueue.scoringRevision, () => {
      scoreChanges += 1
      setCurrentVerified(new Map())
    })
    return React.createElement(CandidateDetailPage, {
      key: detailKey,
      backDestination: 'candidates', candidateId, onBack() {}, queue: currentQueue,
      rankingUnlocked: true, sessionId: 'session', verifiedEvidence: currentVerified,
      onEvidenceVerified() {}, onScoreInputsChanged() {},
    })
  }

  const rendered = render(wrapper(React.createElement(Harness, {
    candidateId: 'first', detailKey: 'detail', currentQueue: historicalQueue,
  })))
  await screen.findByRole('heading', { name: 'First Candidate' })
  const scoreSummary = screen.getByRole('region', { name: 'Match score' })
  assert.equal(scoreSummary.closest('details'), null, 'score must be visible before opening any disclosure')
  assert.equal(scoreSummary.compareDocumentPosition(document.querySelector('.profile-actions')) & 4, 4)
  assert.equal(document.querySelectorAll('.signal-table').length, 1, 'do not repeat the score table below')
  await userEvent.setup({ document: dom.window.document }).click(screen.getByRole('button', { name: 'Review score evidence' }))
  assert.equal(document.querySelector('.profile-diagnostics').open, false)
  await userEvent.setup({ document: dom.window.document }).click(document.querySelector('.evidence-link'))
  assert.equal(document.querySelector('.evidence-link').textContent.includes('Source checked'), true)
  assert.equal(scoreChanges, 0, 'preexisting revision 7 must be the observation baseline')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second', detailKey: 'detail', currentQueue: historicalQueue,
  })))
  await screen.findByRole('heading', { name: 'Second Candidate' })
  assert.equal(document.querySelector('.evidence-link').textContent.includes('Source checked'), true)
  assert.equal(scoreChanges, 0, 'opening a second candidate is not a score mutation')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second', detailKey: 'uncached-remount', currentQueue: historicalQueue,
  })))
  await screen.findByRole('heading', { name: 'Second Candidate' })
  assert.equal(document.querySelector('.evidence-link').textContent.includes('Source checked'), true)
  assert.equal(scoreChanges, 0, 'an uncached remount must retain the historical baseline')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second',
    detailKey: 'uncached-remount',
    currentQueue: { ...historicalQueue, revision: 8, scoringRevision: 4 },
  })))
  await waitFor(() => assert.equal(scoreChanges, 1))
  assert.equal(document.querySelector('.evidence-link').textContent.includes('Source checked'), false)
})

test('candidate pool renders queued, failed, and focused enqueue errors', { timeout: 5000 }, async () => {
  const candidates = [
    { id: 'queued', username: 'queued', profile_url: '/in/queued', display_name: 'Queued', stage: 'discovered', retrieval_status: 'pending', profile_urn: null, profile_urn_is_scored: false, profile_urn_quarantined: false, profile_urn_routing_allowed: false, profile_contract_error: null, active_job_id: 'job', source_count: 0, sources: [] },
    { id: 'failed', username: 'failed', profile_url: '/in/failed', display_name: 'Failed', stage: 'discovered', retrieval_status: 'failed', profile_urn: null, profile_urn_is_scored: false, profile_urn_quarantined: false, profile_urn_routing_allowed: false, profile_contract_error: null, active_job_id: null, source_count: 0, sources: [] },
    { id: 'ready', username: 'ready', profile_url: '/in/ready', display_name: 'Ready', stage: 'discovered', retrieval_status: 'pending', profile_urn: null, profile_urn_is_scored: false, profile_urn_quarantined: false, profile_urn_routing_allowed: false, profile_contract_error: null, active_job_id: null, source_count: 0, sources: [] },
  ]
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([])
    if (path.startsWith('/api/candidate-pool?')) return json(candidates)
    if (path === '/api/candidates/ready/enrich' && init?.method === 'POST') {
      return json({ detail: 'candidate already has a queued or running fetch' }, 409)
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  let opened = null
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(SearchPage, {
    session: { id: 'session' }, brief: null, queue,
    onCandidateOpen(id) { opened = id },
  })))
  const queued = await screen.findByRole('button', { name: 'Waiting to download' })
  assert.equal(queued.disabled, true)
  await user.click(screen.getByRole('button', { name: 'Review retrieval failure' }))
  assert.equal(opened, 'failed')
  await user.click(screen.getByRole('button', { name: 'Download profile & experience' }))
  const alert = await screen.findByRole('alert')
  assert.equal(alert.textContent.includes('Profile retrieval was not queued'), true)
  await waitFor(() => assert.equal(document.activeElement, alert))
  assert.equal(within(alert).getByText(/already has a queued/).textContent.length > 0, true)
})

test('offline discovery keeps saved profiles browseable and filters names locally', async () => {
  let opened = null
  let compared = false
  let writes = 0
  const pool = ['Ada', 'Grace'].map((name, index) => ({
    id: name, username: name.toLowerCase(), display_name: name,
    profile_url: `https://www.linkedin.com/in/${name.toLowerCase()}/`,
    stage: index ? 'discovered' : 'stage1', retrieval_status: index ? 'pending' : 'ok',
    active_job_id: null, source_count: 0, sources: [],
  }))
  globalThis.fetch = (input, init) => {
    if (init?.method === 'POST') writes += 1
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([])
    if (path.startsWith('/api/candidate-pool?')) return json(pool)
    throw new Error(`Unexpected request: ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(SearchPage, {
    session: { id: 'session', phase_gates: { A: true } },
    brief: { id: 'brief', version: 1, target_titles: [], positive_keywords: ['platform'], negative_keywords: [], location: '' },
    queue, retrievalReady: false, onCandidateOpen(id) { opened = id },
    onGateAChanged() { compared = true },
  })))
  await screen.findByRole('heading', { name: 'Ada' })
  assert.equal(document.querySelector('#pool-review'), null)
  assert.ok(screen.getByText('List check recorded'))
  const history = document.querySelector('.search-history')
  const cards = document.querySelector('.candidate-grid')
  assert.ok(history.compareDocumentPosition(cards) & 4)
  await user.click(screen.getByRole('button', { name: 'Compare candidates' }))
  assert.equal(compared, true)
  assert.equal(screen.getByRole('button', { name: 'Run search' }).disabled, true)
  assert.equal(screen.getByRole('button', { name: 'Download profile & experience' }).disabled, true)
  await user.click(screen.getByRole('button', { name: 'Review', exact: true }))
  assert.equal(opened, 'Ada')
  await user.type(screen.getByLabelText('Find a saved candidate'), 'ada')
  assert.equal(screen.queryByRole('heading', { name: 'Grace' }), null)
  assert.ok(screen.getByRole('heading', { name: 'Ada' }))
  assert.equal(writes, 0)
})

test('queue recovery surfaces a failed resume instead of silently doing nothing', async () => {
  let resumes = 0
  globalThis.fetch = (input, init) => {
    assert.equal(String(input), '/api/queue/resume')
    assert.equal(init.method, 'POST')
    resumes += 1
    return json({ detail: 'Resume unavailable' }, 503)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(QueueStatus, {
    queue: { ...queue, state: 'paused', pause_reason: 'TRANSPORT' },
  })))
  assert.equal(resumes, 0)
  assert.ok(screen.getByText(/connector is not reachable/))
  await user.click(screen.getByRole('button', { name: 'Resume downloads' }))
  await screen.findByText(/Request failed|Queue resume failed/)
  assert.equal(resumes, 1)
})


test('profile overview keeps withheld evidence hidden and exports the actual saved text', async () => {
  const field = (field_key, value, provenance_available = true) => ({ field_key, value, provenance_available })
  const candidate = {
    id: 'person', display_name: 'Casey Chen', username: 'casey', profile_url: 'https://www.linkedin.com/in/casey/',
    score: { headline: 'Engineer' },
    fields: [field('headline', 'Platform engineer'), field('location', 'Berlin'),
      field('experience.0.title', 'Staff engineer'), field('experience.0.company', 'Example Co'),
      field('experience.0.dates', '2022 – Present'), field('experience.1.title', 'Withheld role', false)],
    available_sections: { experience: { retrieved_at: '2026-09-04T12:00:00Z' } },
    signals: [{ claims: [
      { id: 'found', display_term: 'Go', verdict: 'matched', evidence: [
        { id: 'hidden', section_name: 'experience', snippet: 'Sensitive source content', availability: { state: 'masked' } },
        { id: 'visible', section_name: 'experience', snippet: 'Built services in Go', availability: { state: 'available' } },
      ] },
      { id: 'missing', display_term: 'PMP', verdict: 'unknown', evidence: [] },
    ] }],
  }
  let opened = null
  let comparisons = 0
  const user = userEvent.setup({ document: dom.window.document })
  render(React.createElement(CandidateOverview, {
    candidate, rankingUnlocked: true, onSourceOpen: (...args) => { opened = args }, onCompare: () => { comparisons++ },
  }))
  assert.equal(screen.getByRole('heading', { name: 'Casey Chen' }).textContent, 'Casey Chen')
  assert.ok(screen.getByText('Staff engineer · Example Co'))
  assert.equal(screen.queryByText('Withheld role'), null)
  assert.equal(screen.queryByText(/Sensitive source content/), null)
  await user.click(screen.getByRole('button', { name: /View source/ }))
  assert.deepEqual(opened, ['experience'])
  await user.click(screen.getByRole('button', { name: 'Compare' }))
  assert.equal(comparisons, 1)

  let exported = null
  let downloaded = null
  const originalCreate = URL.createObjectURL
  const originalRevoke = URL.revokeObjectURL
  const originalClick = dom.window.HTMLAnchorElement.prototype.click
  URL.createObjectURL = blob => { exported = blob; return 'blob:test' }
  URL.revokeObjectURL = () => {}
  dom.window.HTMLAnchorElement.prototype.click = function () { downloaded = this.download }
  globalThis.fetch = input => {
    assert.equal(String(input), '/api/candidates/person/sections/experience')
    return json({ raw_text: 'Actual saved career text' })
  }
  try {
    await user.click(screen.getByRole('button', { name: 'Download text' }))
    await waitFor(() => assert.notEqual(exported, null))
    assert.equal(await exported.text(), 'Actual saved career text')
    assert.equal(downloaded, 'compass-person-experience.txt')
  } finally {
    URL.createObjectURL = originalCreate
    URL.revokeObjectURL = originalRevoke
    dom.window.HTMLAnchorElement.prototype.click = originalClick
  }
})

test('drawer closes on cancel, locks page scrolling, and restores focus', async () => {
  // JSDOM lacks native showModal; the browser check covers the actual modal surface.
  dom.window.HTMLDialogElement.prototype.showModal = function () { this.open = true }
  dom.window.HTMLDialogElement.prototype.close = function () { this.open = false }
  const opener = document.createElement('button')
  opener.textContent = 'Review candidate'
  document.body.append(opener)
  opener.focus()
  const user = userEvent.setup({ document: dom.window.document })
  function Harness() {
    const [open, setOpen] = React.useState(true)
    return open ? React.createElement(CandidateDrawer, { onClose: () => setOpen(false) }, React.createElement('p', null, 'Saved candidate')) : null
  }
  const rendered = render(React.createElement(Harness))
  const dialog = screen.getByRole('dialog', { name: 'Candidate review' })
  assert.equal(document.body.style.overflow, 'hidden')
  await user.click(screen.getByRole('button', { name: 'Close candidate profile' }))
  assert.equal(screen.queryByRole('dialog'), null)
  assert.equal(document.body.style.overflow, '')
  assert.equal(document.activeElement, opener)
  rendered.unmount()
  render(React.createElement(Harness))
  const second = screen.getByRole('dialog', { name: 'Candidate review' })
  const { act } = await import('@testing-library/react')
  await act(async () => second.dispatchEvent(new dom.window.Event('cancel', { cancelable: true, bubbles: true })))
  assert.equal(screen.queryByRole('dialog'), null)
  assert.equal(document.body.style.overflow, '')
  assert.equal(dialog.isConnected, false)
  opener.remove()
})


test('Find candidates derives keywords from the brief and ignores legacy overrides', async () => {
  let submitted = null
  window.localStorage.setItem('compass:search-settings:launch-brief', JSON.stringify({ keywords: '"Platform engineer" Go', network: ['O'], companyId: '123' }))
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/searches?') || path.startsWith('/api/candidate-pool?')) return json([])
    if (path === '/api/searches') {
      submitted = JSON.parse(init.body)
      return json({ search_run_id: 'new-run', job_id: 'job' })
    }
    if (path === '/api/searches/new-run') return json(null)
    throw new Error(`unexpected fetch ${path}`)
  }
  let edited = false
  const user = userEvent.setup({ document: dom.window.document })
  try {
    render(wrapper(React.createElement(SearchPage, {
      session: { id: 'session', phase_gates: {} }, brief: { id: 'launch-brief', location: 'Berlin', target_titles: [{ term: 'Engineer' }], required_skills: [{ term: 'Go' }], required_credentials: [], positive_keywords: ['payments'], negative_keywords: [] },
      queue, onCandidateOpen() {}, onEditBrief() { edited = true },
    })))
    assert.equal(screen.queryByLabelText('Keywords'), null)
    assert.equal(screen.queryByLabelText('Location preference'), null)
    assert.equal(screen.queryByText('Network & company filters'), null)
    assert.equal(submitted, null, 'opening results must not start a search')
    await user.click(screen.getByRole('button', { name: 'Run search' }))
    await waitFor(() => assert.notEqual(submitted, null))
    assert.deepEqual(submitted, { paginate: true, automatic_downloads: true, session_id: 'session', brief_id: 'launch-brief', keywords: 'Engineer Go payments', location: 'Berlin', network: ['O'], current_company: '123' })
    await user.click(screen.getByRole('button', { name: 'Adjust criteria' }))
    assert.equal(edited, true)
  } finally { window.localStorage.removeItem('compass:search-settings:launch-brief') }
})


test('multiple locations queue separate searches and retain comma-separated place names', async () => {
  const submitted = []
  globalThis.fetch = (_input, init) => {
    submitted.push(JSON.parse(init.body))
    return json({ search_run_id: `run-${submitted.length}`, job_id: `job-${submitted.length}` })
  }
  const result = await searchLocations({ session_id: 'session', brief_id: 'brief', keywords: 'Engineer', location: 'Austin, TX; Chicago' })
  assert.deepEqual(submitted.map(item => item.location), ['Austin, TX', 'Chicago'])
  assert.equal(result.queued.length, 2)
  assert.equal(result.multiple, true)
  assert.deepEqual(result.failed, [])
})

test('a partial location enqueue failure reports the unqueued locations without retrying earlier ones', async () => {
  let calls = 0
  globalThis.fetch = () => ++calls === 1 ? json({ search_run_id: 'first', job_id: 'job' }) : json({ detail: 'Queue unavailable' }, 503)
  const result = await searchLocations({ session_id: 'session', brief_id: 'brief', keywords: 'Engineer', location: 'Chicago; Berlin; London' })
  assert.equal(calls, 2)
  assert.equal(result.queued.length, 1)
  assert.deepEqual(result.failed, ['Berlin', 'London'])
})

test('nice-to-have-only brief can search and the results summary uses years', async () => {
  let submitted
  globalThis.fetch = (input, init) => {
    const path=String(input)
    if (path.startsWith('/api/searches?') || path.startsWith('/api/candidate-pool?')) return json([])
    if (path === '/api/searches') { submitted=JSON.parse(init.body); return json({search_run_id:'optional-run',job_id:'optional-job'}) }
    if (path === '/api/searches/optional-run') return json(null)
    throw new Error(`Unexpected request: ${path}`)
  }
  render(wrapper(React.createElement(SearchPage, {
    session:{id:'session',phase_gates:{}}, queue, onCandidateOpen(){},
    brief:{id:'optional-brief',location:'',target_titles:[],required_skills:[],optional_skills:[{term:'Go',aliases:[]}],required_credentials:[],positive_keywords:[],negative_keywords:[],required_experience_months:60},
  })))
  assert.ok(screen.getByText('Go · nice-to-have'))
  assert.ok(screen.getByText('5+ years'))
  assert.equal(screen.queryByText('60 months minimum'),null)
  assert.equal(screen.getByRole('button',{name:'Run search'}).disabled,false)
  assert.equal(submitted,undefined)
  await userEvent.setup({document:dom.window.document}).click(screen.getByRole('button',{name:'Run search'}))
  await waitFor(()=>assert.equal(submitted?.keywords,'Go'))
})

function rankingPool() {
  return ['Unscored','Zoe','Ada'].map((name,index)=>({id:name,username:name.toLowerCase(),display_name:name,profile_url:'/in/example',stage:index===0?'discovered':'stage1',retrieval_status:'ok',active_job_id:null,source_count:1,sources:[{search_run_id:'run',keywords:'Go',network_filter:[],reference_position:index}]}))
}

test('Find candidates does not request rankings before candidate-list review', async () => {
  const calls=[]
  globalThis.fetch=input=>{const path=String(input);calls.push(path);if(path.startsWith('/api/searches?')) return json([]);if(path.startsWith('/api/candidate-pool?')) return json(rankingPool());throw new Error(`Unexpected request ${path}`)}
  render(wrapper(React.createElement(SearchPage,{session:{id:'session',phase_gates:{}},brief:null,queue,onCandidateOpen(){}})))
  await screen.findByRole('heading',{name:'Ada'})
  assert.equal(screen.getByRole('button',{name:'Ranked list'}).disabled,true)
  assert.equal(screen.queryByRole('table',{name:'Candidates ranked by score'}),null)
  assert.equal(calls.some(path=>path.startsWith('/api/candidates?')),false)
})

test('ranked pool preserves API ties, keeps unscored last, filters without renumbering, and returns to cards', async () => {
  const calls=[];let opened
  const scopedPool=rankingPool();scopedPool[1].sources=[{...scopedPool[1].sources[0],search_run_id:'other'}]
  const scores=['Zoe','Ada'].map(id=>({id,score:80,confidence:0.7,confidence_band:'medium',calculation_status:'scored',headline:'Backend engineer'}))
  globalThis.fetch=input=>{const path=String(input);calls.push(path);if(path.startsWith('/api/searches?'))return json([{id:'run',keywords:'Go',created_at:'2026-09-04',status:'ok',network:[]}]);if(path==='/api/searches/run')return json(null);if(path.startsWith('/api/candidate-pool?'))return json(scopedPool);if(path.startsWith('/api/candidates?')){assert.equal(new URL(path,'http://local').searchParams.get('sort'),'score_desc');return json(scores)}throw new Error(path)}
  render(wrapper(React.createElement(SearchPage,{session:{id:'session',phase_gates:{A:true}},brief:null,queue,retrievalReady:false,onCandidateOpen(id){opened=id}})))
  const user=userEvent.setup({document:dom.window.document})
  await screen.findByRole('heading',{name:'Ada'})
  assert.equal(calls.some(path=>path.startsWith('/api/candidates?')),false)
  await user.click(screen.getByRole('button',{name:'Ranked list'}))
  const table=await screen.findByRole('table',{name:'Candidates ranked by score'})
  const rows=within(table).getAllByRole('row').slice(1)
  assert.deepEqual(rows.map(row=>row.querySelector('.pool-person p').textContent),['Zoe','Ada','Unscored'])
  assert.deepEqual(rows.map(row=>row.querySelector('.pool-rank').textContent),['1','2','—'])
  assert.equal(within(rows[2]).queryByText('0.0'),null)
  assert.ok(within(rows[2]).getByText('Not scored'))
  assert.equal(screen.getByRole('button',{name:'Download profile for Unscored'}).disabled,true)
  await user.type(screen.getByLabelText('Find a saved candidate'),'ada')
  assert.equal(within(table).getAllByRole('row').length,2)
  assert.equal(within(table).getAllByRole('row')[1].querySelector('.pool-rank').textContent,'2')
  await user.click(screen.getByRole('button',{name:'Review Ada'}))
  assert.equal(opened,'Ada')
  await user.selectOptions(screen.getByLabelText('Results from'),'run')
  await waitFor(()=>assert.equal(within(table).getAllByRole('row')[1].querySelector('.pool-rank').textContent,'1'))
  await user.selectOptions(screen.getByLabelText('Results from'),'')
  assert.equal(within(table).getAllByRole('row')[1].querySelector('.pool-rank').textContent,'2')
  await user.click(screen.getByRole('button',{name:'Cards',exact:true}))
  assert.ok(screen.getByRole('heading',{name:'Ada'}))
  assert.equal(screen.queryByRole('heading',{name:'Zoe'}),null)
})

test('ranked pool shows a recoverable error and refreshes after queue updates', async () => {
  let scoreCalls=0
  globalThis.fetch=input=>{const path=String(input);if(path.startsWith('/api/searches?'))return json([]);if(path.startsWith('/api/candidate-pool?'))return json(rankingPool());if(path.startsWith('/api/candidates?')){scoreCalls++;return scoreCalls===1?json({detail:'Unavailable'},503):json([{id:'Ada',score:scoreCalls===2?60:90,confidence:1,confidence_band:'high',calculation_status:'scored'}])}throw new Error(path)}
  const props={session:{id:'session',phase_gates:{A:true}},brief:null,queue,onCandidateOpen(){}}
  const client=new QueryClient({defaultOptions:{queries:{retry:false}}})
  const node=p=>React.createElement(QueryClientProvider,{client},React.createElement(SearchPage,p))
  const view=render(node(props));const user=userEvent.setup({document:dom.window.document})
  await screen.findByRole('heading',{name:'Ada'})
  await user.click(screen.getByRole('button',{name:'Ranked list'}))
  await screen.findByText('Scores could not be loaded.')
  assert.equal(screen.queryByRole('table'),null)
  await user.click(screen.getByRole('button',{name:'Try again'}))
  await screen.findByText('60.0')
  view.rerender(node({...props,queue:{...queue,revision:1}}))
  await screen.findByText('90.0')
  assert.equal(screen.queryByText('60.0'),null)
})

test('old-search catch-up targets only the selected run and cannot double-submit', async () => {
  const submitted = []
  let requested = false
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([{ id: 'chosen', status: 'ok', keywords: 'Go', created_at: '2026-09-05', network: ['O'], automatic_downloads: requested }])
    if (path === '/api/searches/chosen') return json(null)
    if (path.startsWith('/api/candidate-pool?')) return json([{ ...rankingPool()[0], sources: [{ search_run_id: 'chosen', keywords: 'Go', network_filter: ['O'], reference_position: 0 }] }])
    if (path === '/api/searches/chosen/downloads' && init?.method === 'POST') {
      submitted.push(path)
      requested = true
      return json({ search_run_id: 'chosen' })
    }
    throw new Error(path)
  }
  render(wrapper(React.createElement(SearchPage, { session: { id: 'session' }, brief: null, queue, initialRunId: 'chosen', onCandidateOpen() {} })))
  const button = await screen.findByRole('button', { name: 'Download remaining profiles' })
  await userEvent.setup({ document: dom.window.document }).click(button)
  await waitFor(() => assert.equal(screen.queryByRole('button', { name: 'Download remaining profiles' }) === null, true))
  assert.deepEqual(submitted, ['/api/searches/chosen/downloads'])
})

test('paginated search shows progress and stops only the selected discovery', async () => {
  const submitted = []
  let stopReason = null
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([{ id: 'paged', status: 'ok', keywords: 'Go', created_at: '2026-09-05', network: ['O'], automatic_downloads: true, pagination: { pages_completed: 2, people_found: 26, profile_limit: 1000, stop_reason: stopReason } }])
    if (path === '/api/searches/paged') return json(null)
    if (path.startsWith('/api/candidate-pool?')) return json([])
    if (path === '/api/searches/paged/stop' && init?.method === 'POST') {
      submitted.push(path)
      stopReason = 'stopped'
      return json({ search_run_id: 'paged' })
    }
    throw new Error(path)
  }
  render(wrapper(React.createElement(SearchPage, { session: { id: 'session' }, brief: null, queue, initialRunId: 'paged', onCandidateOpen() {} })))
  await screen.findByText(/26 people · 2 pages · Searching more pages/)
  await userEvent.setup({ document: dom.window.document }).click(screen.getByRole('button', { name: 'Stop discovery' }))
  await screen.findByText('Discovery stopped. Profiles already queued will finish downloading.')
  await waitFor(() => assert.equal(screen.queryByRole('button', { name: 'Stop discovery' }), null))
  assert.deepEqual(submitted, ['/api/searches/paged/stop'])
})

test('Find candidates pages cards and global rankings in groups of 30', async () => {
  const template = rankingPool()[0]
  const people = Array.from({ length: 65 }, (_, index) => ({ ...template, id: `person-${index}`, username: `person-${index}`, display_name: `Person ${String(index).padStart(2, '0')}`, sources: [{ ...template.sources[0], search_run_id: index < 32 ? 'first' : 'second' }] }))
  const scores = people.slice(0, 62).reverse().map((person, index) => ({ id: person.id, score: 100 - index, confidence: .8, confidence_band: 'high', calculation_status: 'scored' }))
  globalThis.fetch = input => {
    const path = String(input)
    if (path.startsWith('/api/searches?')) return json([{ id: 'first', keywords: 'First search', created_at: '2026-09-05', status: 'ok', network: [] }])
    if (path === '/api/searches/first') return json(null)
    if (path.startsWith('/api/candidate-pool?')) return json(people)
    if (path.startsWith('/api/candidates?')) return json(scores)
    throw new Error(path)
  }
  render(wrapper(React.createElement(SearchPage, { session: { id: 'session', phase_gates: { A: true } }, brief: null, queue, onCandidateOpen() {} })))
  const user = userEvent.setup({ document: dom.window.document })
  const controls = () => within(screen.getByRole('navigation', { name: 'Candidate pages', exact: true }))
  const cards = () => document.querySelectorAll('.candidate-card')
  await screen.findByRole('heading', { name: 'Person 00' })
  assert.equal(cards().length, 30)
  assert.equal(controls().getByRole('button', { name: 'Previous' }).disabled, true)
  await user.click(controls().getByRole('button', { name: 'Next' }))
  assert.equal(cards().length, 30)
  assert.ok(screen.getByRole('heading', { name: 'Person 30' }))
  assert.equal(screen.queryByRole('heading', { name: 'Person 00' }), null)
  await user.click(controls().getByRole('button', { name: 'Next' }))
  assert.equal(cards().length, 5)
  assert.ok(screen.getByRole('heading', { name: 'Person 64' }))
  assert.equal(controls().getByRole('button', { name: 'Next' }).disabled, true)
  await user.click(controls().getByRole('button', { name: 'Previous' }))
  assert.ok(screen.getByRole('heading', { name: 'Person 30' }))
  await user.selectOptions(screen.getByLabelText('Results from'), 'first')
  assert.ok(screen.getByRole('heading', { name: 'Person 00' }))
  assert.equal(cards().length, 30)
  await user.selectOptions(screen.getByLabelText('Results from'), '')
  await user.click(controls().getByRole('button', { name: 'Next' }))
  await user.click(screen.getByRole('button', { name: 'Ranked list', exact: true }))
  const table = await screen.findByRole('table', { name: 'Candidates ranked by score' })
  const rows = () => within(table).getAllByRole('row').slice(1)
  assert.equal(rows().length, 30)
  assert.equal(rows()[0].querySelector('.pool-person p').textContent, 'Person 61')
  await user.click(controls().getByRole('button', { name: 'Next' }))
  assert.equal(rows().length, 30)
  assert.equal(rows()[0].querySelector('.pool-rank').textContent, '31')
  assert.equal(rows()[0].querySelector('.pool-person p').textContent, 'Person 31')
  await user.click(controls().getByRole('button', { name: 'Next' }))
  assert.equal(rows().length, 5)
  assert.deepEqual(rows().map(row => row.querySelector('.pool-rank').textContent), ['61', '62', '—', '—', '—'])
  await user.type(screen.getByLabelText('Find a saved candidate'), 'Person 61')
  assert.equal(rows().length, 1)
  assert.equal(rows()[0].querySelector('.pool-rank').textContent, '1')
  assert.equal(screen.queryByRole('navigation', { name: 'Candidate pages', exact: true }), null)
  await user.clear(screen.getByLabelText('Find a saved candidate'))
  assert.equal(rows().length, 30)
  assert.equal(rows()[0].querySelector('.pool-rank').textContent, '1')
})
