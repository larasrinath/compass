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
  target_titles: BriefTerm[]
  location: string
  industries: BriefTerm[]
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
  stage: 'discovered'
  retrieval_status: string
  profile_urn: string | null
  profile_urn_is_scored: false
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

export const listCandidates = (sessionId: string) =>
  requestJson<CandidateRecord[]>(
    `/api/candidates?session_id=${encodeURIComponent(sessionId)}`,
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
