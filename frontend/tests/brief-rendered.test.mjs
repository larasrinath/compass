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
const { BriefPage } = await vite.ssrLoadModule('/src/pages/BriefPage.tsx')
await vite.close()

test.after(async () => {
  cleanup()
  dom.window.close()
})
test.afterEach(() => cleanup())

const session = { id: 'session' }

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

function briefRecord(overrides = {}) {
  return {
    id: 'brief-1', session_id: 'session', version: 1, created_at: '2026-01-01T00:00:00Z',
    superseded_at: null, weights_version: 'weights-1', stale_scores: 0,
    job_description: 'Platform engineer', required_skills: [], optional_skills: [],
    required_experience_months: 24, target_titles: [], location: '', industries: [],
    required_credentials: [{ term: 'PMP', aliases: ['Project Management Professional'] }],
    positive_keywords: [], negative_keywords: [], message_tone: 'Professional',
    ...overrides,
  }
}

test('create serializes credential aliases and preserves zero experience', async () => {
  let request = null
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path === '/api/briefs') {
      request = { method: init.method, body: JSON.parse(init.body) }
      return json(briefRecord({ ...request.body, required_experience_months: 0 }))
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, { session, current: null })))

  await user.type(screen.getByLabelText('Job description'), 'Platform engineer')
  await user.click(screen.getByRole('button', { name: 'Set up search' }))
  assert.equal(request, null, 'review must not save or search')
  await user.click(screen.getByText('Set exact months'))
  const experience = screen.getByLabelText('Required experience in months')
  assert.equal(experience.value, '')
  await user.type(experience, '-1')
  assert.equal(experience.checkValidity(), false)
  await user.click(screen.getByRole('button', { name: 'Save brief' }))
  assert.equal(request, null, 'negative experience must fail native validation')
  await user.clear(experience)
  await user.type(experience, '1.5')
  assert.equal(experience.checkValidity(), false)
  await user.click(screen.getByRole('button', { name: 'Save brief' }))
  assert.equal(request, null, 'fractional experience must fail integer validation')
  await user.clear(experience)
  await user.type(experience, '0')

  const credentials = screen.getByRole('group', { name: 'Required credentials' })
  await user.type(
    within(credentials).getByLabelText('New required credentials term'),
    'AWS Architect',
  )
  await user.click(within(credentials).getByRole('button', { name: 'Add term' }))
  await user.click(within(credentials).getByText('Alternate names'))
  await user.type(
    within(credentials).getByLabelText('Aliases for AWS Architect'),
    'SAA, Solutions Architect Associate',
  )
  await user.click(screen.getByRole('button', { name: 'Save brief' }))

  await waitFor(() => assert.notEqual(request, null))
  assert.equal(request.method, 'POST')
  assert.equal(request.body.required_experience_months, 0)
  assert.deepEqual(request.body.required_credentials, [{
    term: 'AWS Architect', aliases: ['SAA', 'Solutions Architect Associate'],
  }])
})

test('loaded experience and credentials survive edit until explicitly removed', async () => {
  let request = null
  globalThis.fetch = (input, init) => {
    const path = String(input)
    if (path === '/api/briefs/current') {
      request = { method: init.method, body: JSON.parse(init.body) }
      return json(briefRecord({ ...request.body, id: 'brief-2', version: 2 }))
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, {
    session,
    current: briefRecord(),
  })))

  await user.click(screen.getByText('Set exact months'))
  const experience = screen.getByLabelText('Required experience in months')
  assert.equal(experience.value, '24')
  const credentials = screen.getByRole('group', { name: 'Required credentials' })
  assert.equal(within(credentials).getByLabelText('Required credentials term 1').value, 'PMP')
  assert.equal(
    within(credentials).getByLabelText('Aliases for PMP').value,
    'Project Management Professional',
  )

  await user.clear(experience)
  await user.click(within(credentials).getByRole('button', { name: 'Remove PMP' }))
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() => assert.notEqual(request, null))
  assert.equal(request.method, 'PUT')
  assert.equal(request.body.required_experience_months, null)
  assert.deepEqual(request.body.required_credentials, [])
})

test('protected credential rejection renders beside and focuses the credential editor', async () => {
  globalThis.fetch = (input) => {
    const path = String(input)
    if (path === '/api/briefs/current') {
      return json({
        detail: {
          message: 'Protected criteria are not permitted.',
          offending_terms: [{
            field: 'required_credentials.0.aliases.0',
            term: 'gender',
          }],
        },
      }, 422)
    }
    throw new Error(`unexpected fetch ${path}`)
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, {
    session,
    current: briefRecord({
      required_credentials: [{ term: 'Safe credential', aliases: ['gender'] }],
    }),
  })))

  const term = screen.getByLabelText('Required credentials term 1')
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  const fieldError = await screen.findByText('Remove protected criterion “gender”.')
  assert.equal(fieldError.closest('[data-field-prefix]')?.dataset.fieldPrefix, 'required_credentials')
  await waitFor(() => assert.equal(document.activeElement, term))
})

test('conflicting include and exclude keywords block saving until corrected', async () => {
  let writes = 0
  globalThis.fetch = (_input, init) => {
    writes += 1
    return json(briefRecord({ ...JSON.parse(init.body), version: 2 }))
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, {
    session, current: briefRecord({ positive_keywords: ['Anaplan'], negative_keywords: ['ANAPLAN'] }),
  })))
  assert.equal(screen.getByRole('button', { name: 'Save new version' }).disabled, true)
  assert.match(screen.getByRole('alert').textContent, /both lists/)
  assert.equal(writes, 0)
  await user.clear(screen.getByLabelText('Exclusions / negative keywords'))
  assert.equal(screen.queryByRole('alert'), null)
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() => assert.equal(writes, 1))
})

test('editing the description preserves hidden criteria and only saves on final confirmation', async () => {
  const original = briefRecord({
    required_experience_months: 27,
    optional_skills: [{ term: 'Go', aliases: ['Golang'] }],
    industries: [{ term: 'Payments', aliases: [] }],
    positive_keywords: ['Platform'], negative_keywords: ['Intern'],
  })
  const writes = []
  let continued = false
  globalThis.fetch = (input, init) => {
    assert.equal(String(input), '/api/briefs/current')
    writes.push(JSON.parse(init.body))
    return json(briefRecord({ ...writes[0], version: 2 }))
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, { session, current: original, onSaved() { continued = true } })))
  await user.click(screen.getByRole('button', { name: 'Back to role description' }))
  await user.clear(screen.getByLabelText('Job description'))
  await user.type(screen.getByLabelText('Job description'), 'Senior platform engineer')
  await user.click(screen.getByRole('button', { name: 'Set up search' }))
  assert.equal(writes.length, 0)
  assert.equal(continued, false)
  await user.click(screen.getByRole('button', { name: 'Continue to search' }))
  await waitFor(() => assert.equal(continued, true))
  assert.equal(writes.length, 1)
  assert.equal(writes[0].job_description, 'Senior platform engineer')
  for (const key of ['required_experience_months', 'required_credentials', 'optional_skills', 'industries', 'positive_keywords', 'negative_keywords', 'message_tone']) {
    assert.deepEqual(writes[0][key], original[key], `${key} must survive the description step`)
  }
})
