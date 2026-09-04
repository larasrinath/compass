import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const projectRoot = fileURLToPath(new URL('../..', import.meta.url))
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
const plan = readFileSync(`${projectRoot}/PROJECT_PLAN.md`, 'utf8')

const missingReasons = [
  'not_requested',
  'rate_limit',
  'fetch_error',
  'unparseable',
]

function quotedValues(value) {
  return [...value.matchAll(/'([^']+)'/g)].map((match) => match[1])
}

test('plan and frontend share the four-value missing-reason domain', () => {
  const sqlDomain = plan.match(/reason CHECK\(reason IN \(([^)]+)\)\)/)?.[1]
  const planType = plan.match(/MissingSection \{[\s\S]*?reason\s+: ([^\n]+)/)?.[1]
  const frontendType = api.match(/export type MissingReason =([\s\S]*?)\n\n/)?.[1]
  const parsingContract = plan.match(/### 13\.2 Section parsing([\s\S]*?)### 13\.3/)?.[1]
  const s3Contract = plan.match(/- \*\*S-3:\*\*([\s\S]*?)(?=\n- \*\*S-4:)/)?.[1]
  const signalAcceptance = plan.match(/\*\*T-4\.1 · Signal implementations\*\*([\s\S]*?)(?=\n\*\*T-4\.2)/)?.[1]
  assert.ok(sqlDomain)
  assert.ok(planType)
  assert.ok(frontendType)
  assert.ok(parsingContract)
  assert.ok(s3Contract)
  assert.ok(signalAcceptance)
  assert.deepEqual(quotedValues(sqlDomain), missingReasons)
  assert.deepEqual(planType.split('|').map((reason) => reason.trim()), missingReasons)
  assert.deepEqual(quotedValues(frontendType), missingReasons)
  assert.match(parsingContract, /Reliably parsed content with no relevant value[\s\S]*full retrieved availability[\s\S]*eligible for deterministic `not_matched`/)
  assert.match(parsingContract, /marked unreliable by a `parse_note`[\s\S]*canonical `unparseable` missing[\s\S]*reduced availability/)
  assert.match(parsingContract, /never produces absence coverage or `not_matched`[\s\S]*never coerced to `fetch_error`/)
  assert.doesNotMatch(parsingContract, /parses to nothing[\s\S]*counts as \*retrieved\*/)
  assert.match(s3Contract, /no normalized target-title or required-skill terms[\s\S]*every parsed role is relevant/)
  assert.match(s3Contract, /empty relevance-filter term set never creates absence coverage/)
  assert.match(s3Contract, /Optional skills, positive[\s\S]*keywords and job-description prose are not S-3 relevance filters/)
  assert.match(s3Contract, /Only relevant roles[\s\S]*reduce availability[\s\S]*irrelevant role does neither/)
  assert.match(s3Contract, /no roles parse reliably[\s\S]*`unknown`[\s\S]*`unparseable`[\s\S]*never absence coverage or[\s\S]*`not_matched`/)
  assert.match(signalAcceptance, /months-only brief[\s\S]*every parsed role is relevant[\s\S]*mutation test must fail/)
})

test('Gate A structurally separates candidate pool from ranking', () => {
  assert.match(api, /\/api\/candidate-pool\?session_id=/)
  assert.match(api, /`\/api\/candidates\?\$\{params\}`/)
  assert.match(candidates, /enabled: Boolean\(session\.phase_gates\?\.A\)/)
  assert.match(candidates, /Inspect the candidate pool before ranking/)
  assert.match(search, /Accept Gate A and unlock ranking/)
  assert.match(detail, /rankingUnlocked && \(hasScoreSignals \|\| allInert\)/)
  assert.match(detail, /rankingUnlocked && !candidate\.score && !hasScoreSignals/)
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
