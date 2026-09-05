import { expect, test, type Locator, type Page } from '@playwright/test'
import canonicalRanking from '../tests/fixtures/canonical-ranking.json' with { type: 'json' }

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
      evidence: [], coverage: [], missing_sections: [{ section_name: 'skills', reason: 'unparseable' }],
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

const mobileEvidenceDetail = {
  ...detail,
  score: { ...score, active_signal_count: 3 },
  signals: [signals[0], {
    ...signals[0], id: 'signal-optional', signal_id: 'S-2', label: 'Optional skills',
    rollup: 'unknown', weight: 10, raw_subscore: 0, contribution: 0, availability: 0,
    claims: [{ ...signals[0].claims[1], id: 'claim-go', display_term: 'Go' }],
  }, {
    ...signals[0], id: 'signal-title', signal_id: 'S-4', label: 'Target title',
    rollup: 'not_matched', weight: 15, raw_subscore: 0, contribution: 0, availability: 1,
    claims: [{ ...signals[0].claims[2], id: 'claim-title', display_term: 'Director' }],
  }],
}

// Measure rendered word fragments, rather than accepting a contained but unreadable layout.
async function wordsBrokenAcrossLines(locator: Locator) {
  return locator.evaluate((element) => {
    const broken: string[] = []
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
      const node = walker.currentNode
      for (const match of (node.textContent ?? '').matchAll(/\p{L}+/gu)) {
        const range = document.createRange()
        range.setStart(node, match.index)
        range.setEnd(node, match.index + match[0].length)
        const lines = new Set(Array.from(range.getClientRects(), (rect) => Math.round(rect.top)))
        if (lines.size > 1) broken.push(match[0])
      }
    }
    return broken
  })
}

const sessionBase = {
  id: 'session-1', created_at: '2026-01-01T00:00:00Z', label: 'Test',
  purge_after: '2026-02-01T00:00:00Z', nav_budget: 120, nav_used: 2, send_enabled: false,
}
const brief = {
  id: 'brief-1', session_id: 'session-1', version: 1, created_at: 'now', superseded_at: null,
  job_description: 'Platform engineer', required_skills: [{ term: 'Alpha', aliases: [] }],
  optional_skills: [], required_experience_months: null, target_titles: [], location: '',
  industries: [], required_credentials: [], positive_keywords: [], negative_keywords: [],
  message_tone: 'Professional', weights_version: 'v1', stale_scores: 0,
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

async function mockApi(
  page: Page,
  gateA: boolean,
  hostileStrings = false,
  detailOverride: Record<string, unknown> | null = null,
) {
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
  const detailPayload = detailOverride ?? (
    hostileStrings
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
  )
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
      raw_text: rawText, span_unit: 'unicode_code_point', spans: (detailPayload.signals ?? []).flatMap((signal: any) => signal.claims.flatMap((claim: any) => claim.evidence.map((item: any) => ({ ...item, value: target, verbatim: target, provenance_available: true, provenance_label: 'Exact stored text' })))),
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
  await expect(page.getByRole('heading', { name: 'Ada Lovelace', level: 1 })).toBeVisible()
  await expect(page.locator('.score-badge')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Why this score changed' })).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'Current and previous scores' })).toHaveCount(0)
  await expect(page.getByRole('checkbox', { name: /^I verified this exact source span for/ })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Close candidate profile' })).toBeVisible()
})

test('Search to detail Back returns to search before Gate A', async ({ page }) => {
  await mockApi(page, false)
  await page.goto('/search')
  await page.getByRole('button', { name: 'Review', exact: true }).click()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
  await page.getByRole('button', { name: 'Close candidate profile' }).click()
  await expect(page).toHaveURL(/\/search$/)
})

test('detail routes survive reload and participate in browser history', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/candidates')
  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Ada Lovelace', level: 1 })).toBeVisible()
  await page.getByRole('button', { name: 'Close candidate profile' }).click()
  await expect(page).toHaveURL(/\/candidates$/)
  await page.goForward()
  await expect(page).toHaveURL(/\/candidates\/candidate-1$/)
})

for (const [sort, expectedIds] of Object.entries(canonicalRanking.orders)) {
  test(`Chrome preserves canonical API ${sort} ranking across ties and nulls`, async ({ page }) => {
    await mockApi(page, true)
    const records = canonicalRanking.candidates.map((candidate) => ({
      ...score, ...candidate,
      score_id: `score-${candidate.id}`, input_fingerprint: `input-${candidate.id}`,
      score_lower: candidate.score, score_upper: candidate.score,
      previous_score: null, delta: null,
      confidence_band: candidate.score === null
        ? candidate.all_inert_attested ? 'low' : null
        : candidate.confidence >= 0.8 ? 'high' : 'medium',
      calculation_status: candidate.score === null ? 'unknown' : 'scored',
      top_signals: [],
    }))
    let requestedSort: string | null = null
    await page.route(/\/api\/candidates\?/, async (route) => {
      requestedSort = new URL(route.request().url()).searchParams.get('sort')
      const ids = canonicalRanking.orders[requestedSort as keyof typeof canonicalRanking.orders]
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify(ids.map((id) => records.find((row) => row.id === id))),
      })
    })
    await page.goto('/candidates')
    await expect(page.getByLabel('Ranked candidates')).toBeVisible()
    await page.getByText('Filter, sort & scoring settings', { exact: true }).click()
    await page.getByLabel('Sort order').selectOption(sort)
    await expect.poll(() => requestedSort).toBe(sort)
    await expect(page.locator('.result-person h3')).toHaveText(expectedIds.map((id) => {
      const row = records.find((candidate) => candidate.id === id)!
      return row.display_name ?? row.username
    }))
  })
}

test('Chrome distinguishes unchecked and unmatched evidence with text and neutral borders', async ({ page, browser }, testInfo) => {
  await mockApi(page, true, false, {
    ...detail,
    signals: [signals[0], {
      ...signals[0], id: 'signal-unknown', signal_id: 'S-2', label: 'Optional skills',
      rollup: 'unknown', raw_subscore: 0, contribution: 0, availability: 0,
      claims: [{ ...signals[0].claims[1], id: 'claim-unknown-optional' }],
    }],
  })
  await page.goto('/candidates/candidate-1')
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  const unknown = page.locator('.verdict-badge.unknown')
  await expect(unknown).toHaveCount(3)
  for (const verdict of await unknown.all()) {
    await expect(verdict).toBeVisible()
    await expect(verdict).toHaveCSS('text-transform', 'none')
    // innerText reflects Chrome's CSS text transformation; textContent does not.
    expect(await verdict.innerText()).toBe('Not checked')
  }
  const unknownClaim = page.locator('.claim-card.unknown').first()
  const absentClaim = page.locator('.claim-card.not_matched')
  await expect(unknownClaim).toHaveCSS('border-left-style', 'solid')
  await expect(absentClaim).toHaveCSS('border-left-style', 'solid')
  await expect(absentClaim.locator('.verdict-badge')).toContainText('No exact match')
  const rendering = await unknown.evaluateAll((elements) => elements.map((element) => ({
    context: element.closest('table') ? 'signal' : 'claim',
    textContent: element.textContent,
    renderedText: (element as HTMLElement).innerText,
    textTransform: getComputedStyle(element).textTransform,
  })))
  await testInfo.attach('unknown-rendered-copy', {
    body: JSON.stringify({ browserVersion: browser.version(), verdicts: rendering }, null, 2),
    contentType: 'application/json',
  })
  await page.screenshot({
    path: testInfo.outputPath('unknown-lowercase.png'), fullPage: true,
  })
})

test('keyboard evidence flow scrolls the actual far-down astral mark and keeps focus', async ({ page }) => {
  await mockApi(page, true)
  await page.goto('/candidates/candidate-1')
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  const link = page.getByRole('button', { name: /🚀 Alpha/ })
  await link.focus()
  await page.keyboard.press('Enter')
  const raw = page.locator('.source-check').getByLabel('Raw experience profile text')
  const mark = raw.locator('mark')
  await expect(mark).toHaveText(target)
  await expect(raw).toBeFocused()
  expect(await raw.evaluate((element) => element.scrollTop)).toBeGreaterThan(0)
  const [rawBox, markBox] = await Promise.all([raw.boundingBox(), mark.boundingBox()])
  expect(rawBox && markBox && markBox.y >= rawBox.y && markBox.y < rawBox.y + rawBox.height).toBeTruthy()
  const verify = page.getByRole('checkbox', { name: /^I verified this exact source span for/ })
  await verify.focus()
  await page.keyboard.press('Space')
  await expect(verify).toBeChecked()
  await expect(page.getByText('This does not mean the candidate lacks this qualification.')).toBeVisible()
})

test('ten Gate B evidence controls expose unique names and keyboard toggles', async ({ page }) => {
  const manyEvidence = Array.from({ length: 10 }, (_, index) => ({
    ...evidence,
    id: `evidence-${index + 1}`,
    snippet: `Alpha evidence ${index + 1}`,
  }))
  const manyDetail = {
    ...detail,
    signals: [{
      ...signals[0],
      claims: [{
        ...signals[0].claims[0],
        evidence: manyEvidence,
      }],
    }],
  }
  await mockApi(page, true, false, manyDetail)
  await page.goto('/candidates/candidate-1')
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  const checkboxes = page.getByRole('checkbox', {
    name: /^I verified this exact source span for/,
  })
  await expect(checkboxes).toHaveCount(0)
  const names = []
  for (let index = 0; index < 10; index++) {
    await page.locator('.evidence-link').nth(index).click()
    await expect(checkboxes).toHaveCount(1)
    await expect(checkboxes).toBeEnabled()
    names.push(await checkboxes.getAttribute('aria-label'))
    await checkboxes.focus()
    await page.keyboard.press('Space')
    await expect(checkboxes).toBeChecked()
  }
  expect(new Set(names).size).toBe(10)
})

test('backend scoring empty state suppresses an empty evidence panel', async ({ page }) => {
  await mockApi(page, true, false, {
    ...detail,
    score: null,
    signals: [],
    scoring_empty_state: 'No score is available for the retrieved inputs.',
  })
  await page.goto('/candidates/candidate-1')
  await expect(page.getByText('No score is available for the retrieved inputs.')).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Why this score changed' })).toHaveCount(0)
})

test('ranked and detail layouts stay readable at narrow width with non-color cues', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true)
  await page.goto('/candidates')
  await page.getByText('Scoring details', { exact: true }).click()
  await expect(page.getByText('Provisional · partial retrieval', { exact: true })).toBeVisible()
  await expect(page.getByText('Scored · config v1', { exact: false })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  await expect(page.locator('.verdict-badge.unknown')).toBeVisible()
  const unparseable = page.locator('.claim-card').filter({ hasText: 'Rust' })
  await expect(unparseable).toContainText('skills · retrieved, but could not be parsed reliably')
  await expect(unparseable).not.toContainText('Searched every required retrieved section')
  await expect(page.locator('.verdict-badge.not_matched')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('mobile signal table keeps normal words intact and supports keyboard scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true, false, mobileEvidenceDetail)
  await page.goto('/candidates/candidate-1')
  const table = page.locator('.signal-table')
  await expect(table).toBeVisible()
  expect(await wordsBrokenAcrossLines(table)).toEqual([])
  const scrollRegion = page.getByRole('region', { name: 'Scoring signal comparison' })
  expect(await scrollRegion.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true)
  await scrollRegion.focus()
  await expect(scrollRegion).toBeFocused()
  await expect(scrollRegion).toHaveCSS('outline-style', 'none')
  await page.keyboard.press('ArrowRight')
  await expect.poll(() => scrollRegion.evaluate((element) => element.scrollLeft)).toBeGreaterThan(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('mobile claim headings keep Go and Rust intact beside status labels', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true, false, mobileEvidenceDetail)
  await page.goto('/candidates/candidate-1')
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  const claims = page.locator('.signal-claims')
  await expect(claims).toBeVisible()
  for (const heading of await claims.locator('.claim-heading').all()) {
    expect(await wordsBrokenAcrossLines(heading)).toEqual([])
  }
  for (const term of ['Go', 'Rust']) {
    const claim = claims.locator('.claim-card').filter({ has: page.getByText(term, { exact: true }) })
    await expect(claim.locator('strong')).toHaveText(term)
    const verdict = claim.locator('.verdict-badge')
    await expect(verdict).toHaveCSS('text-transform', 'none')
    expect(await verdict.innerText()).toBe('Not checked')
    await expect(claim).toHaveCSS('border-left-style', 'solid')
  }
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.screenshot({ path: testInfo.outputPath('evidence-mobile-readable.png'), fullPage: true })
  const panelBox = await page.locator('.evidence-panel').boundingBox()
  if (!panelBox) throw new Error('Evidence panel has no rendered bounds')
  await page.screenshot({
    path: testInfo.outputPath('evidence-mobile-panel.png'), fullPage: true, clip: panelBox,
  })
})

test('hostile unbroken API strings never widen the 390px candidate views', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await mockApi(page, true, true)
  await page.goto('/candidates')
  await expect(page.locator('.result-person-identity > p')).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()

  await page.getByRole('button', { name: 'Open evidence for Ada Lovelace' }).click()
  await page.getByRole('button', { name: 'Review score evidence' }).click()
  await expect(page.getByRole('heading', { name: 'Review against your criteria' })).toBeVisible()
  await expect(page.locator('.evidence-link')).toBeVisible()
  await page.getByText('All saved text & score history', { exact: true }).click()
  await page.getByText('Search details', { exact: true }).click()
  await expect(page.locator('.context-panel')).toBeVisible()
  expect(await page.locator('.candidate-drawer').evaluate(el => el.scrollWidth <= el.clientWidth)).toBeTruthy()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('brief preserves experience and credential aliases through create and edit', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const hostileAlias = 'A'.repeat(160)
  let currentBrief: Record<string, unknown> | null = null
  const writes: Array<Record<string, unknown>> = []
  let rejectProtected = false
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    if (!path.startsWith('/api/')) {
      await route.continue()
      return
    }
    if (path === '/api/events') {
      await route.abort()
      return
    }
    let body: unknown
    let status = 200
    if (path === '/api/health') {
      body = { status: 'ok', database: 'ok', send_enabled: false, llm_provider: 'null' }
    } else if (path === '/api/mcp/status') {
      body = { reachable: true, tools: [], last_error_class: null, correlation_id: 'test' }
    } else if (path === '/api/session') {
      body = { ...sessionBase, phase_gates: {} }
    } else if (path === '/api/briefs/current' && request.method() === 'GET') {
      body = currentBrief
    } else if (
      (path === '/api/briefs' && request.method() === 'POST') ||
      (path === '/api/briefs/current' && request.method() === 'PUT')
    ) {
      const payload = request.postDataJSON() as Record<string, unknown>
      writes.push(payload)
      if (rejectProtected) {
        status = 422
        body = {
          detail: {
            message: 'Protected criteria are not permitted.',
            offending_terms: [{
              field: 'required_credentials.0.aliases.0',
              term: 'gender',
            }],
          },
        }
      } else {
        const version = writes.length
        currentBrief = {
          ...payload,
          id: `brief-${version}`,
          version,
          created_at: '2026-01-01T00:00:00Z',
          superseded_at: null,
          weights_version: `weights-${version}`,
          stale_scores: 0,
        }
        body = currentBrief
        status = request.method() === 'POST' ? 201 : 200
      }
    } else if (path === '/api/searches' || path === '/api/candidate-pool') {
      body = []
    } else {
      throw new Error(`Unhandled API route ${request.method()} ${path}`)
    }
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    })
  })

  await page.goto('/brief')
  await page.getByLabel('Job description').fill('Platform engineer')
  await page.getByRole('button', { name: 'Set up search', exact: true }).click()
  const increase = page.getByRole('button', { name: 'Increase minimum experience by one year' })
  await increase.click()
  await page.getByLabel('Filter type').selectOption('credential')
  await page.getByLabel('New key filter').fill('AWS Architect')
  await page.getByRole('button', { name: 'Add filter', exact: true }).click()
  await page.getByRole('button', { name: 'Continue to search' }).click()
  await expect(page).toHaveURL(/\/search$/)
  expect(writes[0].required_experience_months).toBe(12)
  expect(writes[0].required_credentials).toEqual([{term:'AWS Architect',aliases:[]}])

  // Older saved aliases survive editing even though there is no alias editor.
  currentBrief = {...currentBrief!, required_credentials:[{term:'AWS Architect', aliases:['SAA',hostileAlias]}]}
  await page.goto('/brief')
  await expect(page.getByLabel('Credential filter 1')).toHaveValue('AWS Architect')
  await expect(page.getByLabel('Aliases for AWS Architect')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await increase.click()
  await page.getByRole('button', {name:'Continue to search'}).click()
  await expect(page).toHaveURL(/\/search$/)
  expect(writes[1].required_experience_months).toBe(24)
  expect(writes[1].required_credentials).toEqual([{term:'AWS Architect',aliases:['SAA',hostileAlias]}])

  await page.goto('/brief')
  const decrease=page.getByRole('button',{name:'Decrease minimum experience by one year'})
  await decrease.click()
  await decrease.click()
  await expect(decrease).toBeDisabled()
  await page.getByRole('button',{name:'Remove AWS Architect',exact:true}).click()
  await page.getByLabel('New key filter').fill('Go')
  await page.getByRole('button',{name:'Add filter',exact:true}).click()
  await page.getByRole('button',{name:'Continue to search'}).click()
  await expect(page).toHaveURL(/\/search$/)
  expect(writes[2].required_experience_months).toBeNull()
  expect(writes[2].required_credentials).toEqual([])

  await page.goto('/brief')
  await page.getByLabel('Filter type').selectOption('credential')
  await page.getByLabel('New key filter').fill('Safe credential')
  await page.getByRole('button',{name:'Add filter',exact:true}).click()
  rejectProtected = true
  await page.getByRole('button',{name:'Continue to search'}).click()
  await expect(page.getByText('Remove protected criterion “gender”.')).toBeVisible()
  await expect(page.getByLabel('Credential filter 1')).toBeFocused()
})

test('Find candidates ranked list retains filters and drawer navigation on desktop and mobile', async ({ page }) => {
  await mockApi(page, true)
  const pool = [poolCandidate, {...poolCandidate,id:'candidate-2',username:'grace',display_name:'Grace Hopper'}, {...poolCandidate,id:'candidate-3',username:'no-profile',display_name:'No Profile',stage:'discovered'}]
  const ranked = [{...score,id:'candidate-2',display_name:'Grace Hopper',score:95},score]
  await page.route('**/api/candidate-pool?**',route=>route.fulfill({json:pool}))
  await page.route('**/api/candidates?**',route=>{
    expect(new URL(route.request().url()).searchParams.get('sort')).toBe('score_desc')
    return route.fulfill({json:ranked})
  })
  await page.goto('/search')
  await expect(page.getByRole('heading',{name:'Ada Lovelace',exact:true})).toBeVisible()
  await page.getByRole('button',{name:'Ranked list',exact:true}).click()
  const table=page.getByRole('table',{name:'Candidates ranked by score'})
  await expect(table).toBeVisible()
  await expect(table.locator('.pool-person > p')).toHaveText(['Grace Hopper','Ada Lovelace','No Profile'])
  await expect(table.locator('.pool-rank')).toHaveText(['1','2','—'])
  await page.getByLabel('Find a saved candidate').fill('Ada')
  await expect(table.locator('.pool-rank')).toHaveText(['2'])
  await page.getByRole('button',{name:'Review Ada Lovelace'}).click()
  await expect(page.getByRole('dialog',{name:'Candidate review'})).toBeVisible()
  await page.getByRole('button',{name:'Close candidate profile'}).click()
  await expect(table.locator('.pool-rank')).toHaveText(['2'])
  await page.getByLabel('Find a saved candidate').fill('')
  await page.setViewportSize({width:390,height:844})
  await expect(table).toBeVisible()
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
  await expect(table.getByRole('button',{name:'Review Ada Lovelace'})).toBeVisible()
  await page.getByRole('button',{name:'Cards',exact:true}).click()
  await expect(table).toHaveCount(0)
  await expect(page.getByRole('heading',{name:'Ada Lovelace',exact:true})).toBeVisible()
})


test('list check stays in Find candidates and opens ranking without verifying a candidate', async ({ page }) => {
  await mockApi(page, false)
  let accepted = false
  await page.route('**/api/session', route => route.fulfill({ json: { ...sessionBase, phase_gates: accepted ? { A: { gate: 'A', accepted_at: 'now', note: 'Checked identities', evidence_ids: [] } } : {} } }))
  await page.route('**/api/searches?**', route => route.fulfill({ json: [{ id: 'run-1', status: 'ok', created_at: '2026-09-04', keywords: 'Alpha', person_reference_count: 1, reference_count: 1, network: [], location: null, current_company: null, new_candidate_count: 1, existing_candidate_count: 0 }] }))
  await page.route('**/api/session/gates/A', async route => {
    expect(route.request().postDataJSON().note).toBe('Checked names and LinkedIn links.')
    accepted = true
    await route.fulfill({ json: { gate: 'A', accepted_at: 'now', note: 'Checked identities', evidence_ids: [] } })
  })
  await page.goto('/search')
  const confirm = page.getByRole('button', { name: 'Confirm list & show ranking' })
  await expect(confirm).toBeDisabled()
  await page.getByRole('textbox', { name: 'What did you check?' }).fill('Checked names and LinkedIn links.')
  await confirm.click()
  await expect(page).toHaveURL(/\/search$/)
  await expect(page.getByRole('button', { name: 'Ranked list', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(page.getByRole('table', { name: 'Candidates ranked by score' })).toBeVisible()
  await expect(page.getByText('List check recorded')).toBeVisible()
  await expect(page.getByRole('checkbox', { name: /^I verified/ })).toHaveCount(0)
})

test('Find candidates pagination stays within 30 profiles on mobile', async ({ page }) => {
  await mockApi(page, true)
  const pool = Array.from({ length: 65 }, (_, index) => ({ ...poolCandidate, id: `paged-${index}`, username: `paged-${index}`, display_name: `Person ${index}` }))
  await page.route('**/api/candidate-pool?**', route => route.fulfill({ json: pool }))
  await page.route('**/api/candidates?**', route => route.fulfill({ json: pool.map((person, index) => ({ ...score, id: person.id, display_name: person.display_name, score: 100 - index })) }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/search')
  await expect(page.locator('.candidate-card')).toHaveCount(30)
  const navigation = page.getByRole('navigation', { name: 'Candidate pages', exact: true })
  await navigation.getByRole('button', { name: 'Next' }).click()
  await expect(page.locator('.candidate-card')).toHaveCount(30)
  await expect(page.getByRole('heading', { name: 'Person 30', exact: true })).toBeVisible()
  await navigation.getByRole('button', { name: 'Next' }).click()
  await expect(page.locator('.candidate-card')).toHaveCount(5)
  await expect(navigation.getByRole('button', { name: 'Next' })).toBeDisabled()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: 'Ranked list', exact: true }).click()
  const table = page.getByRole('table', { name: 'Candidates ranked by score' })
  await expect(table.locator('tbody tr')).toHaveCount(30)
  await navigation.getByRole('button', { name: 'Next' }).click()
  await expect(table.locator('.pool-rank').first()).toHaveText('31')
  await expect(table.locator('tbody tr')).toHaveCount(30)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
})

test('activity summary keeps a thousand waiting downloads compact on desktop and mobile', async ({ page }) => {
  await mockApi(page, true)
  await page.addInitScript(() => {
    window.EventSource = class extends EventTarget {
      timer: ReturnType<typeof setTimeout>
      constructor() {
        super()
        this.timer = setTimeout(() => {
          this.dispatchEvent(new Event('open'))
          this.dispatchEvent(new MessageEvent('snapshot', { data: JSON.stringify({
            state: 'active', pause_reason: null, resume_at: null, counts: {},
            jobs: Array.from({ length: 1001 }, (_, index) => ({
              id: `job-${index}`, kind: index ? 'get_person_profile' : 'search_people',
              state: index ? 'queued' : 'running', position: index || null, depth: 1001, percent: 100,
            })),
          }) }))
        }, 0)
      }
      close() { clearTimeout(this.timer) }
    } as unknown as typeof EventSource
  })
  await page.goto('/search')
  const activity = page.getByRole('complementary', { name: 'Activity queue' })
  await expect(activity.getByText('Finding candidates')).toBeVisible()
  await expect(activity.getByText('1,000 profiles')).toBeVisible()
  expect((await activity.boundingBox())!.height).toBeLessThan(150)
  await expect(activity.locator('.queue-job')).toHaveCount(0)
  await page.screenshot({ path: '/private/tmp/compass-activity-desktop.png' })
  await activity.getByRole('button', { name: 'View tasks' }).click()
  await expect(activity.locator('.queue-job')).toHaveCount(10)
  await activity.getByRole('button', { name: 'Next tasks' }).click()
  await expect(activity.getByText('11–20 of 1,001 tasks')).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await expect(activity.getByRole('button', { name: 'Cancel Profile download at position 10' })).toBeVisible()
  await activity.getByRole('button', { name: 'Hide tasks' }).click()
  expect((await activity.boundingBox())!.height).toBeLessThan(170)
  await activity.screenshot({ path: '/private/tmp/compass-activity-mobile.png' })
})
