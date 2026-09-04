export interface HealthResponse {
  status: 'ok' | 'degraded'
  database: 'ok' | 'unavailable'
  send_enabled: boolean
  llm_provider: string
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health', {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Health check failed (${response.status})`)
  }
  return response.json() as Promise<HealthResponse>
}

export interface McpStatusResponse {
  reachable: boolean
  tools: string[]
  last_error_class: string | null
  correlation_id: string
}

export interface QueueJob {
  id: string
  kind: string
  state: string
  position: number | null
  depth: number
  error_class: string | null
  correlation_id: string
  progress?: number
  total?: number | null
  percent?: number | null
}

export interface QueueSnapshot {
  state: 'active' | 'paused'
  pause_reason: string | null
  resume_at: string | null
  counts: Record<string, number>
  jobs: QueueJob[]
}

export async function getMcpStatus(): Promise<McpStatusResponse> {
  const response = await fetch('/api/mcp/status', {
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`MCP status failed (${response.status})`)
  }
  return response.json() as Promise<McpStatusResponse>
}

export async function resumeQueue(): Promise<QueueSnapshot> {
  const response = await fetch('/api/queue/resume', {
    method: 'POST',
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) {
    throw new Error(`Queue resume failed (${response.status})`)
  }
  return response.json() as Promise<QueueSnapshot>
}

export class ApiError extends Error {
  detail: unknown

  constructor(message: string, detail: unknown) {
    super(message)
    this.name = 'ApiError'
    this.detail = detail
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    let structuredDetail: unknown = null
    try {
      const payload = (await response.json()) as { detail?: unknown }
      structuredDetail = payload.detail ?? payload
      message =
        typeof payload.detail === 'string'
          ? payload.detail
          : JSON.stringify(payload.detail ?? payload)
    } catch {
      // Keep the status-only message for a non-JSON failure.
    }
    throw new ApiError(message, structuredDetail)
  }
  return response.json() as Promise<T>
}

export interface SessionRecord {
  id: string
  created_at: string
  label: string
  purge_after: string
  nav_budget: number
  nav_used: number
  send_enabled: boolean
  phase_gates?: Partial<Record<'A' | 'B' | 'C', PhaseGateRecord>>
}

export interface PhaseGateRecord {
  gate: 'A' | 'B' | 'C'
  accepted_at: string
  note: string | null
  evidence_ids: string[]
}

export interface BriefTerm {
  term: string
  aliases: string[]
}

export interface BriefInput {
  session_id: string
  job_description: string
  required_skills: BriefTerm[]
  optional_skills: BriefTerm[]
  required_experience_months: number | null
  target_titles: BriefTerm[]
  location: string
  industries: BriefTerm[]
  required_credentials: BriefTerm[]
  positive_keywords: string[]
  negative_keywords: string[]
  message_tone: string
}

export interface BriefRecord extends BriefInput {
  id: string
  version: number
  created_at: string
  superseded_at: string | null
  weights_version: string
  stale_scores: number
}

export interface SearchInput {
  session_id: string
  brief_id: string
  keywords: string
  location?: string | null
  network?: string[] | null
  current_company?: string | null
}

export interface SearchRun {
  id: string
  job_id: string
  brief_id: string
  created_at: string
  keywords: string
  location: string | null
  network: string[]
  current_company: string | null
  status: string
  reference_count: number
  person_reference_count: number
  new_candidate_count: number
  existing_candidate_count: number
}

export interface SearchDetail extends SearchRun {
  result_url: string | null
  raw_text: string | null
  reference_kind_counts: Record<string, number>
  references: Array<{
    kind: string
    url: string | null
    text: string | null
    context: string | null
    value: string | null
    position: number
  }>
  errors: Array<{
    section_name: string
    error_type: string
    error_message: string
    extra: Record<string, unknown>
  }>
}

export interface CandidateSource {
  search_run_id: string
  created_at: string
  keywords: string
  location: string | null
  network_filter: string[]
  current_company: string | null
  reference_position: number
  reference_text: string | null
  reference_context: string | null
  notice: string
}

export interface CandidateRecord {
  id: string
  username: string
  profile_url: string
  display_name: string | null
  stage: 'discovered' | 'stage1' | 'stage2'
  retrieval_status: string
  profile_urn: string | null
  profile_urn_is_scored: false
  profile_urn_quarantined: boolean
  profile_urn_routing_allowed: boolean
  profile_contract_error: string | null
  active_job_id: string | null
  source_count: number
  sources: CandidateSource[]
}

export interface CompanyLookup {
  id: string
  job_id: string
  slug: string
  status: string
  candidates: Array<{ urn_id: string; text: string }>
  note: string | null
}

export interface ParsedFieldRecord {
  id: string
  field_key: string
  value: string | null
  section_name: string
  profile_section_id: string
  span_start: number | null
  span_end: number | null
  snippet: string | null
  origin: 'deterministic' | 'llm_verified'
  provenance_available: boolean
  provenance_label: string
}

export interface CandidateDetail {
  id: string
  username: string
  profile_url: string
  display_name: string | null
  profile_urn: string | null
  profile_urn_is_scored: false
  profile_urn_quarantined: boolean
  profile_urn_routing_allowed: boolean
  profile_contract_error: string | null
  stage: CandidateRecord['stage']
  retrieval_status: string
  active_job_id: string | null
  available_sections: Record<
    string,
    {
      profile_section_id: string
      retrieved_at: string
      char_len: number
      field_count: number
    }
  >
  fields: ParsedFieldRecord[]
  fetches: Array<{
    id: string
    job_id: string
    requested_sections: string[]
    started_at: string
    finished_at: string | null
    outcome: string | null
    contract_error: string | null
  }>
  errors: Array<{
    section_name: string
    error_type: string
    error_message: string
    extra: Record<string, unknown>
  }>
  score?: RankedCandidateRecord | null
  score_history?: Array<{
    id: string
    score: number | null
    weights_version: string
    computed_at: string
    current: boolean
  }>
  signals?: ScoreSignalRecord[]
  non_scoring_hints?: RankedCandidateRecord['non_scoring_hints']
  scoring_empty_state?: string | null
}

export interface CandidateSection {
  candidate_id: string
  section_name: string
  profile_section_id: string
  raw_text: string
  span_unit: 'unicode_code_point'
  spans: Array<{
    id: string
    field_key?: string
    profile_section_id: string
    span_start: number | null
    span_end: number | null
    value: string | null
    snippet: string | null
    verbatim: string | null
    provenance_available: boolean
    provenance_label: string
  }>
}

export type ClaimVerdict =
  | 'matched'
  | 'not_matched'
  | 'unknown'
  | 'contradicted'

export interface ProfileEvidenceRecord {
  id: string
  section_name: string
  profile_section_id: string
  span_start: number
  span_end: number
  snippet: string
  matched_term: string
  matcher: 'exact' | 'alias' | 'stem' | 'llm_verified'
  polarity: 'supporting' | 'contradicting'
  availability:
    | { state: 'available' }
    | { state: 'masked'; reason: string }
    | { state: 'raw_purged'; reason: string; purged_at: string }
}

export interface AbsenceCoverageRecord {
  section_name: string
  normalized_terms: string[]
  aliases: string[]
  matcher_version: string
}

export interface MissingSectionRecord {
  section_name: string
  reason: 'not_requested' | 'rate_limit' | 'fetch_error'
}

export interface ScoreClaimRecord {
  id: string
  claim_key: string
  display_term: string
  verdict: ClaimVerdict
  evidence: ProfileEvidenceRecord[]
  coverage: AbsenceCoverageRecord[]
  missing_sections: MissingSectionRecord[]
}

export interface ScoreSignalRecord {
  id: string
  signal_id: 'S-1' | 'S-2' | 'S-3' | 'S-4' | 'S-5' | 'S-6' | 'S-8'
  label: string
  rollup: ClaimVerdict | 'mixed'
  weight: number
  raw_subscore: number
  contribution: number
  availability: number
  claims: ScoreClaimRecord[]
}

export interface RankedCandidateRecord {
  id: string
  score_id: string
  input_fingerprint: string
  username: string
  profile_url: string
  display_name: string | null
  headline: string | null
  stage: 'provisional' | 'enriched'
  score: number | null
  score_lower: number | null
  score_upper: number | null
  previous_score: number | null
  delta: number | null
  confidence: number
  confidence_band: 'low' | 'medium' | 'high' | null
  calculation_status: 'scored' | 'unknown'
  active_signal_count: number
  all_inert_attested: boolean
  weights_version: string
  top_signals: Array<{
    signal_id: string
    label: string
    contribution: number
    rollup: ScoreSignalRecord['rollup']
  }>
  non_scoring_hints: Array<{
    kind: 'network' | 'profile_urn' | 'messageability' | 'search_context'
    label: string
    value: string
  }>
}

export interface ScoringConfigRecord {
  version: string
  weights: Record<ScoringWeightKey, number>
  active_signal_ids: string[]
  inert_reasons: Partial<
    Record<ScoringWeightKey, { code: string; message: string }>
  >
  metro_region_equivalences: Record<string, string[]>
}

export const SCORING_WEIGHT_KEYS = [
  'S-1',
  'S-2',
  'S-3',
  'S-4',
  'S-5',
  'S-6',
  'S-8',
] as const

export type ScoringWeightKey = (typeof SCORING_WEIGHT_KEYS)[number]

export interface RankedCandidatesQuery {
  session_id: string
  stage?: string
  min_score?: number
  confidence?: string
  sort?: string
}

export const getSession = () => requestJson<SessionRecord | null>('/api/session')

export const createSession = (label: string) =>
  requestJson<SessionRecord>('/api/session', {
    method: 'POST',
    body: JSON.stringify({ label }),
  })

export const getBrief = (sessionId: string) =>
  requestJson<BriefRecord | null>(
    `/api/briefs/current?session_id=${encodeURIComponent(sessionId)}`,
  )

export const saveBrief = (input: BriefInput, exists: boolean) =>
  requestJson<BriefRecord>(exists ? '/api/briefs/current' : '/api/briefs', {
    method: exists ? 'PUT' : 'POST',
    body: JSON.stringify(input),
  })

export const listSearches = (sessionId: string) =>
  requestJson<SearchRun[]>(
    `/api/searches?session_id=${encodeURIComponent(sessionId)}`,
  )

export const getSearch = (runId: string) =>
  requestJson<SearchDetail>(`/api/searches/${encodeURIComponent(runId)}`)

export const runSearch = (input: SearchInput) =>
  requestJson<{ job_id: string; search_run_id: string }>('/api/searches', {
    method: 'POST',
    body: JSON.stringify(input),
  })

export const listCandidatePool = (sessionId: string) =>
  requestJson<CandidateRecord[]>(
    `/api/candidate-pool?session_id=${encodeURIComponent(sessionId)}`,
  )

export const listCandidates = (query: RankedCandidatesQuery) => {
  const params = new URLSearchParams({ session_id: query.session_id })
  if (query.stage) params.set('stage', query.stage)
  if (query.min_score !== undefined) {
    params.set('min_score', String(query.min_score))
  }
  if (query.confidence) params.set('confidence', query.confidence)
  if (query.sort) params.set('sort', query.sort)
  return requestJson<RankedCandidateRecord[]>(`/api/candidates?${params}`)
}

export const acceptPhaseGateA = (note: string) =>
  requestJson<PhaseGateRecord>('/api/session/gates/A', {
    method: 'POST',
    body: JSON.stringify({ note }),
  })

export const acceptPhaseGateB = (evidenceIds: string[], note?: string) =>
  requestJson<PhaseGateRecord>('/api/session/gates/B', {
    method: 'POST',
    body: JSON.stringify({ evidence_ids: evidenceIds, note: note || null }),
  })

export const getScoringConfig = async () =>
  validateScoringConfig(await requestJson<unknown>('/api/weights'))

export const updateScoringConfig = (input: {
  expected_version: string
  weights: Record<ScoringWeightKey, number>
  metro_region_equivalences: Record<string, string[]>
}) =>
  requestJson<ScoringConfigRecord>('/api/weights/current', {
    method: 'PUT',
    body: JSON.stringify(input),
  })

function validateScoringConfig(value: unknown): ScoringConfigRecord {
  if (!value || typeof value !== 'object') {
    throw new Error('Scoring configuration contract error: response must be an object.')
  }
  const record = value as Record<string, unknown>
  if (!record.weights || typeof record.weights !== 'object') {
    throw new Error('Scoring configuration contract error: weights must be an object.')
  }
  const keys = Object.keys(record.weights as object)
  const unexpected = keys.filter(
    (key) => !SCORING_WEIGHT_KEYS.includes(key as ScoringWeightKey),
  )
  const missing = SCORING_WEIGHT_KEYS.filter((key) => !keys.includes(key))
  if (unexpected.length || missing.length) {
    throw new Error(
      `Scoring configuration contract error: ${unexpected.length ? `unexpected weight keys ${unexpected.join(', ')}` : ''}${unexpected.length && missing.length ? '; ' : ''}${missing.length ? `missing weight keys ${missing.join(', ')}` : ''}.`,
    )
  }
  for (const key of SCORING_WEIGHT_KEYS) {
    const weight = (record.weights as Record<string, unknown>)[key]
    if (typeof weight !== 'number' || !Number.isFinite(weight) || weight < 0) {
      throw new Error(
        `Scoring configuration contract error: ${key} weight must be a finite nonnegative number.`,
      )
    }
  }
  return value as ScoringConfigRecord
}

export const getCandidate = (candidateId: string) =>
  requestJson<CandidateDetail>(
    `/api/candidates/${encodeURIComponent(candidateId)}`,
  )

export const getProfileSections = () =>
  requestJson<string[]>('/api/profile-sections')

export const enrichCandidate = (candidateId: string, sections: string[]) =>
  requestJson<{ job_id: string; estimated_navigations: number }>(
    `/api/candidates/${encodeURIComponent(candidateId)}/enrich`,
    {
      method: 'POST',
      body: JSON.stringify({ sections }),
    },
  )

export const getCandidateSection = (
  candidateId: string,
  sectionName: string,
) =>
  requestJson<CandidateSection>(
    `/api/candidates/${encodeURIComponent(candidateId)}/sections/${encodeURIComponent(sectionName)}`,
  )

export const startCompanyLookup = (sessionId: string, slug: string) =>
  requestJson<{ job_id: string; lookup_id: string }>(
    '/api/companies/urn-lookup',
    {
      method: 'POST',
      body: JSON.stringify({ session_id: sessionId, slug }),
    },
  )

export const getCompanyLookup = (lookupId: string) =>
  requestJson<CompanyLookup>(
    `/api/companies/urn-lookups/${encodeURIComponent(lookupId)}`,
  )
