import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const read = (path) => readFileSync(`${root}/src/${path}`, 'utf8')
const app = read('App.tsx')
const api = read('api/client.ts')
const search = read('pages/SearchPage.tsx')
const candidates = read('pages/CandidatesPage.tsx')
const evidence = read('components/EvidencePanel.tsx')
const copy = read('components/scoringCopy.ts')
const weights = read('components/WeightsEditor.tsx')
const detail = read('pages/CandidateDetailPage.tsx')
const routing = read('routing.ts')

test('Gate A structurally separates candidate pool from ranking', () => {
  assert.match(api, /\/api\/candidate-pool\?session_id=/)
  assert.match(api, /`\/api\/candidates\?\$\{params\}`/)
  assert.match(candidates, /enabled: Boolean\(session\.phase_gates\?\.A\)/)
  assert.match(candidates, /Inspect the candidate pool before ranking/)
  assert.match(search, /Accept Gate A and unlock ranking/)
  assert.match(detail, /rankingUnlocked && \(candidate\.score \|\| candidate\.signals\)/)
  assert.match(detail, /rankingUnlocked && candidate\.score_history/)
  assert.match(routing, /\/candidates\/\$\{encodeURIComponent/)
})

test('M4 keeps context outside scoring and sending outside the UI', () => {
  assert.match(search, /\['F', 'S'\]/)
  assert.match(search, /Only F is reliably messageable/)
  assert.match(search, /never affects a score/)
  assert.match(weights, /Search only — not a scoring criterion\./)
  assert.doesNotMatch(weights, /'S-7':\s*[1-9]/)
  assert.doesNotMatch(`${candidates}${evidence}${weights}`, /send now|draft message/i)
})

test('evidence copy and Gate B controls preserve human verification', () => {
  assert.match(copy, /'not found in the retrieved data'/)
  assert.match(evidence, /Opening is not verification/)
  assert.match(evidence, /I verified this exact source span/)
  assert.match(evidence, /Evidence withheld/)
  assert.match(candidates, /eligibleEvidence\.size < 10/)
  assert.match(app, /Scores rank retrieved evidence, not people\./)
})
