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
  appType: 'custom',
  optimizeDeps: { noDiscovery: true },
  server: { middlewareMode: true },
})
const { RawTextViewer } = await vite.ssrLoadModule(
  '/src/components/RawTextViewer.tsx',
)
const { CandidateDetailPage } = await vite.ssrLoadModule(
  '/src/pages/CandidateDetailPage.tsx',
)
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
    candidateId: 'candidate', onBack() {}, queue,
  })))
  const honorsButton = await screen.findByRole('button', { name: 'honors' })
  honorsButton.focus()
  await user.keyboard('{Enter}')
  assert.equal(honorsButton.getAttribute('aria-pressed'), 'true')
  assert.equal(
    (await screen.findByLabelText('Raw honors profile text')).textContent,
    'Grace Hopper Award',
  )
  await user.click(screen.getByRole('button', { name: /🚀 Alpha/ }))
  await waitFor(() => assert.equal(document.querySelector('mark')?.textContent, '🚀 Alpha'))
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
    if (path.startsWith('/api/candidates?')) return json(candidates)
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
  await user.click(screen.getByRole('button', { name: 'Retrieve main profile + experience' }))
  const alert = await screen.findByRole('alert')
  assert.equal(alert.textContent.includes('Profile retrieval was not queued'), true)
  await waitFor(() => assert.equal(document.activeElement, alert))
  assert.equal(within(alert).getByText(/already has a queued/).textContent.length > 0, true)
})
