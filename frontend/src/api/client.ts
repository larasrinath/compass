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
