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
