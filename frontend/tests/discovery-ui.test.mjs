import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const app = readFileSync(`${root}/src/App.tsx`, 'utf8')
const brief = readFileSync(`${root}/src/pages/BriefPage.tsx`, 'utf8')
const search = readFileSync(`${root}/src/pages/SearchPage.tsx`, 'utf8')
const terms = readFileSync(`${root}/src/components/TermEditor.tsx`, 'utf8')
const css = readFileSync(`${root}/src/App.css`, 'utf8')

test('brief and search are separate explicit actions', () => {
  assert.match(brief, /You’ll review everything before searching/)
  assert.match(brief, /Save brief/)
  assert.doesNotMatch(brief, /runSearch|\/api\/searches/)
  assert.match(app, /disabled=\{!brief\.data\}/)
  assert.match(search, /Run search/)
})

test('discovery explains cap, provenance and uncertainty', () => {
  assert.match(search, /references were\s+people/)
  assert.match(search, /shared 15-reference cap/)
  assert.match(search, /another narrower search/)
  assert.match(search, /source\.notice/)
  assert.match(search, /Profile not retrieved/)
  assert.match(search, /first-seen order/)
})

test('M2 does not expose selection, ranking, drafting or messaging actions', () => {
  const controls = `${brief}${search}${terms}`
  for (const forbidden of [
    /numeric score/i,
    /confidence band/i,
    /shortlist/i,
    /reject candidate/i,
    /draft message/i,
    /copy message/i,
    /send now/i,
    /messageability/i,
  ]) {
    assert.doesNotMatch(controls, forbidden)
  }
})

test('forms and mobile layout retain accessible controls', () => {
  assert.match(search, /<fieldset className="network-fieldset">/)
  assert.match(search, /<legend>Network distance/)
  assert.match(search, /rel="noopener noreferrer"/)
  assert.match(terms, /aria-label=\{`Remove/)
  assert.match(terms, /event\.key === 'Enter'/)
  assert.match(css, /min-height:\s*44px/)
  assert.match(css, /@media \(max-width:\s*820px\)/)
  assert.match(css, /grid-template-columns:\s*1fr/)
})
