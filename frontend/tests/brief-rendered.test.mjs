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
test.afterEach(() => { cleanup(); window.localStorage.clear() })

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

test('create adds skills and credentials together and saves minimum experience in years', async () => {
  let request = null
  globalThis.fetch = (input, init) => {
    assert.equal(String(input), '/api/briefs')
    request = { method: init.method, body: JSON.parse(init.body) }
    return json(briefRecord(request.body))
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, { session, current: null })))

  await user.type(screen.getByLabelText('Job description'), 'Platform engineer')
  await user.click(screen.getByRole('button', { name: 'Set up search' }))
  assert.equal(request, null, 'review must not save or search')
  assert.equal(screen.getByRole('button', { name: /Decrease minimum experience/ }).disabled, true)
  await user.click(screen.getByRole('button', { name: /Increase minimum experience/ }))
  await user.click(screen.getByRole('button', { name: /Increase minimum experience/ }))
  assert.equal(screen.getByRole('status').textContent, '2+ years')

  const titles = screen.getByRole('group', { name: 'Target titles' })
  const titleInput = within(titles).getByLabelText('New target titles term')
  await user.type(titleInput, 'Engineer')
  await user.click(within(titles).getByRole('button', { name: 'Add term' }))
  assert.equal(document.activeElement, titleInput)
  await user.keyboard('Architect{Enter}')
  assert.equal(within(titles).getByLabelText('Target titles term 2').value, 'Architect')

  const locations = screen.getByRole('group', { name: 'Locations' })
  await user.type(within(locations).getByLabelText('New locations term'), 'Austin, TX{Enter}Chicago{Enter}')
  assert.ok(document.querySelector('.criteria-optional [data-field-prefix="industries"]'))

  const filters = screen.getByRole('group', { name: 'Skills & keywords' })
  await user.type(within(filters).getByLabelText('New key filter'), 'Go{Enter}')
  await user.type(within(filters).getByLabelText('New key filter'), 'AWS Architect')
  // Changing type must not prematurely add the pending credential as a skill.
  await user.selectOptions(within(filters).getByLabelText('Filter type'), 'credential')
  await user.click(within(filters).getByRole('button', { name: 'Add filter' }))
  assert.equal(document.activeElement, within(filters).getByLabelText('New key filter'))
  await user.selectOptions(within(filters).getByLabelText('Filter type'), 'optional')
  await user.type(within(filters).getByLabelText('New key filter'), 'Terraform{Enter}')
  assert.equal(screen.queryByLabelText('Search keywords — optional'), null)
  await user.type(screen.getByLabelText('Company ID'), '123')
  await user.click(screen.getByRole('checkbox', { name: '3rd-degree and beyond' }))
  await user.click(screen.getByRole('button', { name: 'Save brief' }))

  await waitFor(() => assert.notEqual(request, null))
  assert.deepEqual(JSON.parse(window.localStorage.getItem('compass:search-settings:brief-1')), { network: ['F', 'S', 'O'], companyId: '123' })
  assert.equal(request.method, 'POST')
  assert.equal(request.body.required_experience_months, 24)
  assert.equal(request.body.location, 'Austin, TX; Chicago')
  assert.deepEqual(request.body.required_skills, [{ term: 'Go', aliases: [] }])
  assert.deepEqual(request.body.required_credentials, [{ term: 'AWS Architect', aliases: [] }])
  assert.deepEqual(request.body.optional_skills, [{ term: 'Terraform', aliases: [] }])
})

test('loaded experience and credentials can be explicitly removed without affecting skills', async () => {
  let request = null
  globalThis.fetch = (input, init) => {
    assert.equal(String(input), '/api/briefs/current')
    request = { method: init.method, body: JSON.parse(init.body) }
    return json(briefRecord({ ...request.body, id: 'brief-2', version: 2 }))
  }
  const user = userEvent.setup({ document: dom.window.document })
  const skills = [{ term: 'Go', aliases: ['Golang'] }]
  render(wrapper(React.createElement(BriefPage, {
    session, current: briefRecord({ required_skills: skills }),
  })))

  assert.equal(screen.getByRole('status').textContent, '2+ years')
  const filters = screen.getByRole('group', { name: 'Skills & keywords' })
  assert.equal(within(filters).getByLabelText('Credential filter 1').value, 'PMP')
  await user.click(screen.getByRole('button', { name: /Decrease minimum experience/ }))
  await user.click(screen.getByRole('button', { name: /Decrease minimum experience/ }))
  assert.equal(screen.getByRole('status').textContent, 'Any')
  assert.equal(screen.getByRole('button', { name: /Decrease minimum experience/ }).disabled, true)
  await user.click(within(filters).getByRole('button', { name: 'Remove PMP' }))
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() => assert.notEqual(request, null))
  assert.equal(request.method, 'PUT')
  assert.equal(request.body.required_experience_months, null)
  assert.deepEqual(request.body.required_credentials, [])
  assert.deepEqual(request.body.required_skills, skills)
})

test('legacy zero experience is preserved until the user clears it', async () => {
  const writes = []
  globalThis.fetch = (_input, init) => {
    const body = JSON.parse(init.body)
    writes.push(body)
    return json(briefRecord({ ...body, version: 2 }))
  }
  const user = userEvent.setup({ document: dom.window.document })
  render(wrapper(React.createElement(BriefPage, {
    session, current: briefRecord({ required_experience_months: 0 }),
  })))
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() => assert.equal(writes.length, 1))
  assert.equal(writes[0].required_experience_months, 0)
  await user.click(screen.getByRole('button', { name: /Decrease minimum experience/ }))
  assert.equal(screen.getByRole('status').textContent, 'Any')
  await user.click(screen.getByRole('button', { name: 'Save new version' }))
  await waitFor(() => assert.equal(writes.length, 2))
  assert.equal(writes[1].required_experience_months, null)
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

  const term = screen.getByLabelText('Credential filter 1')
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

test('editing the description preserves saved criteria and only saves on final confirmation', async () => {
  const original = briefRecord({
    required_experience_months: 27,
    required_skills: [{ term: 'PostgreSQL', aliases: ['Postgres'] }],
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
  assert.equal(screen.queryByText('Alternate names'), null)
  assert.equal(screen.queryByText('Set exact months'), null)
  assert.equal(document.querySelector('details'), null)
  assert.ok(screen.getByRole('region', { name: 'Optional preferences' }))
  assert.equal(screen.getByLabelText('Positive keywords').value, 'Platform')
  assert.equal(screen.getByRole('status').textContent, '2.25+ years')
  await user.click(screen.getByRole('button', { name: 'Continue to search' }))
  await waitFor(() => assert.equal(continued, true))
  assert.equal(writes.length, 1)
  assert.equal(writes[0].job_description, 'Senior platform engineer')
  for (const key of ['required_experience_months', 'required_skills', 'required_credentials', 'optional_skills', 'industries', 'positive_keywords', 'negative_keywords', 'message_tone']) {
    assert.deepEqual(writes[0][key], original[key], `${key} must survive the description step`)
  }
})
