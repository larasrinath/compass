// Render the real UI with fictional, intercepted API responses. No backend is used.
import { spawn } from 'node:child_process'
import { mkdir } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const cwd = fileURLToPath(new URL('../', import.meta.url))
const output = fileURLToPath(new URL('../../docs/screenshots/', import.meta.url))
const origin = 'http://127.0.0.1:5195'
const date = '2026-09-04T12:00:00Z'
const term = value => ({ term: value, aliases: [] })
const brief = {
  id: 'docs-brief', session_id: 'docs-session', version: 1, created_at: date,
  superseded_at: null, job_description: 'Find backend engineers with Go and PostgreSQL experience, at least five years building production services, based in Berlin or London.',
  required_skills: ['Go', 'PostgreSQL'].map(term), optional_skills: ['Distributed systems'].map(term),
  target_titles: ['Backend engineer'].map(term), required_experience_months: 60,
  location: 'Berlin; London', industries: ['Financial services'].map(term),
  required_credentials: [], positive_keywords: [], negative_keywords: [],
  message_tone: 'Professional', weights_version: '1', stale_scores: 0,
}
const run = {
  id: 'docs-search', job_id: 'docs-job', brief_id: brief.id, created_at: date,
  keywords: 'Backend engineer Go PostgreSQL', location: 'Berlin', network: [],
  current_company: null, status: 'ok', reference_count: 4, person_reference_count: 4,
  new_candidate_count: 4, existing_candidate_count: 0,
  automatic_downloads: true, pagination: { pages_completed: 2, people_found: 4, profile_limit: 1000, stop_reason: 'exhausted' },
}
const names = ['Robin Serrano', 'Alex Morgan', 'Sam Rivera', 'Jordan Ellis']
const companies = ['Example Metrics', 'Example Payments', 'Example Cloud', 'Example Systems']
const ranked = names.map((name, index) => ({
  id: `docs-person-${index}`, username: `example-${index}`, display_name: name,
  profile_url: `https://example.invalid/profiles/${index}`,
  headline: `Backend Engineer at ${companies[index]}`,
  score_id: `docs-score-${index}`, input_fingerprint: `docs-fingerprint-${index}`,
  stage: 'provisional', score: [88.9, 83.3, 77.8, 66.7][index],
  score_lower: [80, 75, 70, 60][index], score_upper: [90, 85, 80, 70][index],
  previous_score: null, delta: null, confidence: 0.9, confidence_band: 'high',
  calculation_status: 'scored', active_signal_count: 6, all_inert_attested: false,
  weights_version: '1', top_signals: [
    { signal_id: 'S-1', label: 'Required skills', contribution: 30, rollup: 'matched' },
    { signal_id: 'S-3', label: 'Relevant experience', contribution: 10, rollup: 'mixed' },
    { signal_id: 'S-2', label: 'Distributed systems', contribution: 0, rollup: 'unknown' },
  ], non_scoring_hints: [],
}))
const pool = ranked.map((person, index) => ({
  ...person, stage: 'stage1', retrieval_status: 'ok', profile_urn: null,
  profile_urn_is_scored: false, profile_urn_quarantined: false, profile_urn_routing_allowed: false,
  profile_contract_error: null, active_job_id: null, source_count: 1,
  sources: [{ search_run_id: run.id, created_at: date, keywords: run.keywords,
    location: run.location, network_filter: [], current_company: null,
    reference_position: index, reference_text: person.display_name, reference_context: null, notice: '' }],
}))
const signals = [
  ['S-1', 'Required skills', 'matched', 30, 30, 1],
  ['S-2', 'Optional skills', 'unknown', 10, 0, 0],
  ['S-3', 'Relevant experience', 'mixed', 20, 10, 1],
  ['S-4', 'Title similarity', 'matched', 15, 15, 1],
  ['S-5', 'Industry fit', 'matched', 10, 10, 1],
  ['S-6', 'Location fit', 'matched', 15, 15, 1],
].map(([id, label, rollup, weight, contribution, availability]) => ({
  id: `docs-${id}`, signal_id: id, label, rollup, weight, contribution, availability,
  raw_subscore: contribution / weight, claims: [],
}))
const rawText = 'Built payment services in Go and PostgreSQL. Backend Engineer at Example Metrics. Berlin, Germany. March 2024–present.'
signals[0].claims = ['Go', 'PostgreSQL'].map(skill => ({
  id: `docs-claim-${skill}`, claim_key: `required:${skill}`, display_term: skill, verdict: 'matched',
  coverage: [], missing_sections: [], evidence: [{
    id: `docs-evidence-${skill}`, section_name: 'experience', profile_section_id: 'docs-section',
    span_start: rawText.indexOf(skill), span_end: rawText.indexOf(skill) + skill.length,
    snippet: skill, matched_term: skill, matcher: 'exact', polarity: 'supporting',
    availability: { state: 'available' },
  }],
}))
signals[1].claims = [{ id: 'docs-distributed', claim_key: 'optional:distributed', display_term: 'Distributed systems', verdict: 'unknown', evidence: [], coverage: [], missing_sections: [{ section_name: 'skills', reason: 'not_requested' }] }]
const fields = [
  ['headline', ranked[0].headline], ['location', 'Berlin, Germany'],
  ['experience.0.title', 'Backend Engineer'], ['experience.0.company', companies[0]],
  ['experience.0.dates', 'March 2024–present'],
].map(([field_key, value], index) => ({
  id: `docs-field-${index}`, field_key, value, section_name: 'experience',
  profile_section_id: 'docs-section', span_start: null, span_end: null,
  snippet: value, origin: 'deterministic', provenance_available: true,
  provenance_label: 'Fictional documentation example',
}))
const detail = {
  ...pool[0], fields, fetches: [], errors: [], score: ranked[0], signals,
  available_sections: { experience: { profile_section_id: 'docs-section', retrieved_at: date, char_len: rawText.length, field_count: fields.length } },
  score_history: [], non_scoring_hints: [],
}
const weights = {
  version: '1', weights: Object.fromEntries([...signals.map(s => [s.signal_id, s.weight]), ['S-8', 0]]),
  active_signal_ids: signals.map(s => s.signal_id), inert_reasons: {}, metro_region_equivalences: {},
}
const responses = {
  '/api/health': { status: 'ok', database: 'ok', send_enabled: false, llm_provider: 'null' },
  '/api/mcp/status': { reachable: true, tools: [], last_error_class: null, correlation_id: 'docs' },
  '/api/session': { id: 'docs-session', label: 'Documentation example', created_at: date,
    purge_after: '2026-10-04T12:00:00Z', nav_budget: 120, nav_used: 8, send_enabled: false,
    phase_gates: { A: { gate: 'A', accepted_at: date, note: 'Fictional reviewed pool', evidence_ids: [] } } },
  '/api/briefs/current': brief, '/api/searches': [run], '/api/candidate-pool': pool,
  '/api/candidates': ranked, '/api/candidates/docs-person-0': detail,
  '/api/profile-sections': ['experience', 'skills', 'education', 'projects'], '/api/weights': weights,
  '/api/candidates/docs-person-0/sections/experience': { candidate_id: ranked[0].id, section_name: 'experience', profile_section_id: 'docs-section', raw_text: rawText, span_unit: 'unicode_code_point', spans: signals[0].claims.flatMap(claim => claim.evidence.map(item => ({ ...item, provenance_available: true, provenance_label: 'Exact saved text' }))) },
}

await mkdir(output, { recursive: true })
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js'], {
  cwd, env: { ...process.env, FRONTEND_HOST: '127.0.0.1', FRONTEND_PORT: '5195' }, stdio: 'pipe',
})
let browser
try {
  let ready = false
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error('Documentation Vite server did not start; check port 5195.')
    try { ready = (await fetch(origin)).ok } catch { /* wait for Vite */ }
    if (ready) break
    await new Promise(resolve => setTimeout(resolve, 100))
  }
  if (!ready) throw new Error('Documentation Vite server timed out.')
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1, locale: 'en-US', timezoneId: 'UTC', reducedMotion: 'reduce' })
  await page.addInitScript(() => {
    // Keep the fictional queue connected without opening a real event stream.
    window.EventSource = class extends EventTarget {
      constructor() {
        super()
        this.timer = setTimeout(() => {
          this.dispatchEvent(new Event('open'))
          this.dispatchEvent(new MessageEvent('snapshot', { data: JSON.stringify({ state: 'active', pause_reason: null, resume_at: null, counts: {}, jobs: [] }) }))
        }, 0)
      }
      close() { clearTimeout(this.timer) }
    }
  })
  const failures = []
  page.on('pageerror', error => failures.push(error.message))
  await page.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (url.origin !== origin) return route.abort()
    if (!url.pathname.startsWith('/api/')) return route.continue()
    if (url.pathname === '/api/events') return route.abort()
    if (route.request().method() !== 'GET' || !(url.pathname in responses)) {
      failures.push(`Unexpected request: ${route.request().method()} ${url.pathname}`)
      return route.abort()
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(responses[url.pathname]) })
  })
  async function capture(file) {
    await page.evaluate(() => document.fonts.ready)
    await page.screenshot({ path: `${output}${file}.png`, animations: 'disabled' })
  }
  await page.goto(`${origin}/brief`)
  await page.getByText('Skills & keywords', { exact: true }).waitFor()
  await capture('role-brief')
  await page.goto(`${origin}/candidates`)
  await page.getByRole('heading', { name: names[0], exact: true }).waitFor()
  await capture('candidate-results')
  await page.getByRole('button', { name: `Open evidence for ${names[0]}` }).click()
  await page.getByRole('heading', { name: 'Match score', exact: true }).waitFor()
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  await page.locator('.evidence-link').first().click()
  await page.locator('.source-check mark').waitFor()
  await capture('candidate-review')
  await page.goto(`${origin}/search`)
  await page.getByRole('heading', { name: names[0], exact: true }).waitFor()
  await page.getByRole('button', { name: 'Ranked list', exact: true }).click()
  await page.getByRole('table', { name: 'Candidates ranked by score' }).waitFor()
  await capture('ranked-list')
  await page.goto(`${origin}/saved`)
  await page.getByRole('button', { name: `Review ${names[2]}` }).waitFor()
  await capture('saved-searches')
  await page.goto(`${origin}/how-it-works`)
  await page.getByText('Working with results', { exact: true }).waitFor()
  await capture('how-it-works')
  if (failures.length) throw new Error(failures.join('\n'))
  console.log(`Captured six documentation screenshots in ${output}`)
} finally {
  await browser?.close()
  server.kill('SIGTERM')
}
