import { expect, test, type Page } from '@playwright/test'

const filler = Array.from({ length: 180 }, (_, index) => `experience line ${index}`).join('\n')
const rawText = `${filler}\n🚀 Alpha first\nmore context\n🚀 Alpha target evidence`
const rawPoints = Array.from(rawText)
const target = '🚀 Alpha'
const firstStart = rawPoints.join('').indexOf(target)
const targetStartUtf16 = rawText.indexOf(target, firstStart + target.length)
const spanStart = Array.from(rawText.slice(0, targetStartUtf16)).length
const spanEnd = spanStart + Array.from(target).length

const evidence = {
  id: 'evidence-far',
  section_name: 'experience',
  profile_section_id: 'section-experience',
  span_start: spanStart,
  span_end: spanEnd,
  snippet: target,
  matched_term: 'Alpha',
  matcher: 'exact',
  polarity: 'supporting',
  availability: { state: 'available' },
}

const score = {
  id: 'candidate-1',
  score_id: 'score-v1',
  input_fingerprint: 'fingerprint-v1',
  username: 'ada',
  profile_url: '/in/ada',
  display_name: 'Ada Lovelace',
  headline: 'Platform engineering leader',
  stage: 'provisional',
  score: 88.5,
  score_lower: 78,
  score_upper: 93,
  previous_score: 84,
  delta: 4.5,
  confidence: 0.8,
  confidence_band: 'high',
  calculation_status: 'scored',
  active_signal_count: 2,
  all_inert_attested: false,
  weights_version: 'v1',
  top_signals: [
    { signal_id: 'S-1', label: 'Required skills', contribution: 24, rollup: 'mixed' },
    { signal_id: 'S-4', label: 'Title similarity', contribution: 12, rollup: 'matched' },
  ],
  non_scoring_hints: [{ kind: 'network', label: 'Search network', value: 'F/S' }],
}

const signals = [{
  id: 'signal-1',
  signal_id: 'S-1',
  label: 'Required skills',
  rollup: 'mixed',
  weight: 30,
  raw_subscore: 0.5,
  contribution: 15,
  availability: 0.5,
  claims: [
    {
      id: 'claim-match', claim_key: 'required:alpha', display_term: 'Alpha', verdict: 'matched',
      evidence: [evidence], coverage: [], missing_sections: [],
    },
    {
      id: 'claim-unknown', claim_key: 'required:rust', display_term: 'Rust', verdict: 'unknown',
      evidence: [], coverage: [], missing_sections: [{ section_name: 'skills', reason: 'not_requested' }],
    },
    {
      id: 'claim-absent', claim_key: 'required:python', display_term: 'Python', verdict: 'not_matched',
      evidence: [], missing_sections: [], coverage: [{
        section_name: 'experience', normalized_terms: ['python'], aliases: [], matcher_version: 'v1',
      }],
    },
  ],
}]

const detail = {
  id: 'candidate-1', username: 'ada', profile_url: '/in/ada', display_name: 'Ada Lovelace',
  profile_urn: 'urn:li:fsd_profile:ada', profile_urn_is_scored: false,
  profile_urn_quarantined: false, profile_urn_routing_allowed: true,
  profile_contract_error: null, stage: 'stage1', retrieval_status: 'ok', active_job_id: null,
  available_sections: {
    experience: { profile_section_id: 'section-experience', retrieved_at: '2026-01-01T00:00:00Z', char_len: rawText.length, field_count: 0 },
  },
  fields: [], fetches: [], errors: [], score, signals,
  score_history: [
    { id: 'score-v1', score: 88.5, weights_version: 'v1', computed_at: '2026-01-01T00:00:00Z', current: true },
    { id: 'score-v0', score: 84, weights_version: 'v0', computed_at: '2025-12-31T00:00:00Z', current: false },
  ],
  non_scoring_hints: [
    { kind: 'network', label: 'Search network', value: 'F/S' },
    { kind: 'messageability', label: 'Messageability', value: 'hint only' },
  ],
}

const sessionBase = {
  id: 'session-1', created_at: '2026-01-01T00:00:00Z', label: 'Test',
  purge_after: '2026-02-01T00:00:00Z', nav_budget: 120, nav_used: 2, send_enabled: false,
}
const brief = {
  id: 'brief-1', session_id: 'session-1', version: 1, created_at: 'now', superseded_at: null,
  job_description: 'Platform engineer', required_skills: [{ term: 'Alpha', aliases: [] }],
  optional_skills: [], target_titles: [], location: '', industries: [], positive_keywords: [],
  negative_keywords: [], message_tone: 'Professional', weights_version: 'v1', stale_scores: 0,
}
const weights = {
  version: 'v1',
  weights: { 'S-1': 30, 'S-2': 10, 'S-3': 20, 'S-4': 15, 'S-5': 10, 'S-6': 8, 'S-8': 0 },
  active_signal_ids: ['S-1'],
  inert_reasons: { 'S-8': { code: 'brief_input_empty', message: 'brief input is empty' } },
  metro_region_equivalences: {},
}

const poolCandidate = {
  id: 'candidate-1', username: 'ada', profile_url: '/in/ada',
  display_name: 'Ada Lovelace', stage: 'stage1', retrieval_status: 'ok',
  profile_urn: null, profile_urn_is_scored: false, profile_urn_quarantined: false,
  profile_urn_routing_allowed: false, profile_contract_error: null,
  active_job_id: null, source_count: 0, sources: [],
}

async function mockApi(page: Page, gateA: boolean, hostileStrings = false) {
  const hostile = 'HOSTILE'.repeat(240)
  const rankedPayload = hostileStrings
    ? {
        ...score,
        headline: hostile,
        weights_version: hostile,
        top_signals: [{ ...score.top_signals[0], label: hostile }],
        non_scoring_hints: [{ kind: 'search_context', label: hostile, value: hostile }],
      }
    : score
  const detailPayload = hostileStrings
    ? {
        ...detail,
        profile_urn: hostile,
        errors: [{ section_name: hostile, error_message: hostile }],
        score: rankedPayload,
        non_scoring_hints: [{ kind: 'search_context', label: hostile, value: hostile }],
        signals: signals.map((signal) => ({
          ...signal,
          label: hostile,
          claims: signal.claims.map((claim) => ({
            ...claim,
            display_term: hostile,
            evidence: claim.evidence.map((item) => ({
              ...item,
              snippet: hostile,
              matched_term: hostile,
            })),
            coverage: claim.coverage.map((item) => ({
              ...item,
              normalized_terms: [hostile],
              matcher_version: hostile,
            })),
          })),
        })),
      }
    : detail
  await page.route('**/api/**', async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/events') {
      await route.abort()
      return
    }
    let body: unknown
    if (path === '/api/health') body = { status: 'ok', database: 'ok', send_enabled: false, llm_provider: 'null' }
    else if (path === '/api/mcp/status') body = { reachable: true, tools: [], last_error_class: null, correlation_id: 'test' }
    else if (path === '/api/session') body = { ...sessionBase, phase_gates: gateA ? { A: { gate: 'A', accepted_at: 'now', note: 'checked', evidence_ids: [] } } : {} }
    else if (path === '/api/briefs/current') body = brief
    else if (path === '/api/searches') body = []
    else if (path === '/api/candidate-pool') body = [poolCandidate]
    else if (path === '/api/candidates/candidate-1') body = detailPayload
    else if (path === '/api/candidates/candidate-1/sections/experience') body = {
      candidate_id: 'candidate-1', section_name: 'experience', profile_section_id: 'section-experience',
      raw_text: rawText, span_unit: 'unicode_code_point', spans: [{
        id: 'evidence-far', profile_section_id: 'section-experience', span_start: spanStart,
        span_end: spanEnd, value: target, snippet: target, verbatim: target,
        provenance_available: true, provenance_label: 'Exact stored text',
      }],
    }
    else if (path === '/api/profile-sections') body = ['experience', 'skills', 'education', 'projects']
    else if (path === '/api/candidates') body = [rankedPayload]
    else if (path === '/api/weights') body = weights
    else throw new Error(`Unhandled API route ${path}`)
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('pre-Gate direct detail route cannot reveal poisoned ranking fields', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/candidates/candidate-1')
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
  await expect(page.getByRole('heading', { name: 'Ada Lovelace' })).toBeVisible()
  await expect(page.locator('.score-badge')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Why this score changed' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Current and previous scores' })).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: 'I verified this exact source span' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '← Back to search' })).toBeVisible()
})

test('Search to detail Back returns to search before Gate A', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/search')
  await page.getByRole('button', { name: 'Review retrieved details' }).click()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
  await page.getByRole('button', { name: '← Back to search' }).click()
  await expect(page).toHaveURL(/\/search$/)
})

test('detail routes survive reload and participate in browser history', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/candidates')
  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Ada Lovelace' })).toBeVisible()
  await page.getByRole('button', { name: '← Back to candidates' }).click()
  await expect(page).toHaveURL(/\/candidates$/)
  await page.goBack()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
})

test('keyboard evidence flow scrolls the actual far-down astral mark and keeps focus', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/candidates/candidate-1')
  const link = page.getByRole('button', { name: /🚀 Alpha/ })
  await link.focus()
  await page.keyboard.press('Enter')
  const raw = page.getByLabel('Raw experience profile text')
  const mark = raw.locator('mark')
  await expect(mark).toHaveText(target)
  await expect(raw).toBeFocused()
  expect(await raw.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  const [rawBox, markBox] = await Promise.all([raw.boundingBox(), mark.boundingBox()])
  expect(rawBox && markBox && markBox.y >= rawBox.y && markBox.y < rawBox.y + rawBox.height).toBeTruthy()
  const verify = page.getByRole('checkbox', { name: 'I verified this exact source span' })
  await verify.focus()
  await page.keyboard.press('Space')
  await expect(verify).toBeChecked()
  await expect(page.getByText('This does not mean the candidate lacks this qualification.')).toBeVisible()
})

test('ranked and detail layouts stay readable at narrow width with non-color cues', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true)
  await page.goto('/candidates')
  await expect(page.locator('.stage-badge')).toContainText('◐ Provisional')
  await expect(page.getByText('Scored · config v1', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await expect(page.getByText('? not found in the retrieved data')).toBeVisible()
  await expect(page.getByText('○ not matched')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('hostile unbroken API strings never widen the 390px candidate views', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true, true)
  await page.goto('/candidates')
  await expect(page.locator('.candidate-headline')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()

  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await expect(page.getByRole('heading', { name: 'Why this score changed' })).toBeVisible()
  await expect(page.locator('.evidence-link')).toBeVisible()
  await expect(page.locator('.context-panel')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})
