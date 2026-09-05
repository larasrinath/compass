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
  await waitFor(() => assert.equal(document.querySelector('.profile-diagnostics').open, true))
  assert.equal(screen.getByRole('checkbox', { name: /^I verified this exact source span for/ }).checked, true)
  assert.equal(scoreChanges, 0, 'preexisting revision 7 must be the observation baseline')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second', detailKey: 'detail', currentQueue: historicalQueue,
  })))
  await screen.findByRole('heading', { name: 'Second Candidate' })
  assert.equal(screen.getByRole('checkbox', { name: /^I verified this exact source span for/ }).checked, true)
  assert.equal(scoreChanges, 0, 'opening a second candidate is not a score mutation')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second', detailKey: 'uncached-remount', currentQueue: historicalQueue,
  })))
  await screen.findByRole('heading', { name: 'Second Candidate' })
  assert.equal(screen.getByRole('checkbox', { name: /^I verified this exact source span for/ }).checked, true)
  assert.equal(scoreChanges, 0, 'an uncached remount must retain the historical baseline')

  rendered.rerender(wrapper(React.createElement(Harness, {
    candidateId: 'second',
    detailKey: 'uncached-remount',
    currentQueue: { ...historicalQueue, revision: 8, scoringRevision: 4 },
  })))
  await waitFor(() => assert.equal(scoreChanges, 1))
  assert.equal(screen.getByRole('checkbox', { name: /^I verified this exact source span for/ }).checked, false)
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
  const queued = await screen.findByRole('button', { name: 'Retrieval queued' })
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
    session: { id: 'session' },
    brief: { id: 'brief', version: 1, target_titles: [], positive_keywords: ['platform'], negative_keywords: [], location: '' },
    queue, retrievalReady: false, onCandidateOpen(id) { opened = id },
  })))
  await screen.findByRole('heading', { name: 'Ada' })
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
  assert.ok(screen.getByText('Not yet checked'))
  await user.click(screen.getByRole('button', { name: 'View evidence' }))
  assert.deepEqual(opened, ['experience', 'visible'])
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


test('Find candidates launches the saved setup without repeating its form', async () => {
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
      session: { id: 'session', phase_gates: {} }, brief: { id: 'launch-brief', location: 'Berlin', target_titles: [{ term: 'Engineer' }], required_skills: [], required_credentials: [], positive_keywords: [], negative_keywords: [] },
      queue, onCandidateOpen() {}, onEditBrief() { edited = true },
    })))
    assert.equal(screen.queryByLabelText('Keywords'), null)
    assert.equal(screen.queryByLabelText('Location preference'), null)
    assert.equal(screen.queryByText('Network & company filters'), null)
    assert.equal(submitted, null, 'opening results must not start a search')
    await user.click(screen.getByRole('button', { name: 'Run search' }))
    await waitFor(() => assert.notEqual(submitted, null))
    assert.deepEqual(submitted, { session_id: 'session', brief_id: 'launch-brief', keywords: '"Platform engineer" Go', location: 'Berlin', network: ['O'], current_company: '123' })
    await user.click(screen.getByRole('button', { name: 'Adjust criteria' }))
    assert.equal(edited, true)
  } finally { window.localStorage.removeItem('compass:search-settings:launch-brief') }
})
