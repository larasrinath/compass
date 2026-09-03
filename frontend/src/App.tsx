import { useMutation, useQuery } from '@tanstack/react-query'
import { getHealth, getMcpStatus, resumeQueue } from './api/client'
import { useJobEvents } from './hooks/useJobEvents'
import './App.css'

function StatusDot({ healthy }: { healthy: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={healthy ? 'status-dot healthy' : 'status-dot'}
    />
  )
}

function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 15_000,
  })
  const mcp = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMcpStatus,
    retry: false,
  })
  const queue = useJobEvents()
  const resume = useMutation({ mutationFn: resumeQueue })

  const backendHealthy = health.data?.status === 'ok'

  return (
    <div className="app-shell">
      <header className="topbar">
        <div aria-hidden="true" className="brand-mark">
          in
        </div>
        <div>
          <p className="eyebrow">Private sourcing workspace</p>
          <h1>LinkedIn Dashboard</h1>
        </div>
        <div className="local-badge">Local only</div>
      </header>

      <main>
        <section className="hero-panel">
          <div>
            <p className="eyebrow">Foundation milestone</p>
            <h2>A careful workspace for one focused search.</h2>
            <p className="lede">
              Search, verify, compare, and shortlist candidates with evidence.
              Nothing is sent without your final review and explicit action.
            </p>
          </div>
          <div className="step-chip">M1 · Connected queue</div>
        </section>

        <section
          aria-busy={health.isPending}
          aria-label="System readiness"
          aria-live="polite"
          className="status-grid"
        >
          <article className="status-card">
            <div className="status-heading">
              <StatusDot healthy={backendHealthy} />
              <h3>Dashboard API</h3>
            </div>
            <p>
              {health.isPending && 'Checking the loopback service…'}
              {health.isError && 'Backend unavailable. Start the FastAPI service.'}
              {backendHealthy && 'Connected on the local machine.'}
              {health.data?.status === 'degraded' &&
                'Connected, but the database is unavailable.'}
            </p>
          </article>

          <article className="status-card">
            <div className="status-heading">
              <StatusDot healthy={health.data?.database === 'ok'} />
              <h3>Private database</h3>
            </div>
            <p>
              {health.data?.database === 'ok'
                ? 'Writable and stored outside the repository.'
                : 'Waiting for a writable local database.'}
            </p>
          </article>

          <article className="status-card">
            <div className="status-heading">
              <StatusDot healthy={mcp.data?.reachable === true} />
              <h3>LinkedIn MCP</h3>
            </div>
            <p>
              {mcp.isPending && 'Checking through the serialized queue…'}
              {mcp.data?.reachable &&
                `${mcp.data.tools.length} tools available. Server remains independently managed.`}
              {mcp.isError && 'Status probe failed. The dashboard will not start the server.'}
              {mcp.data && !mcp.data.reachable &&
                `Unavailable (${mcp.data.last_error_class ?? 'unknown'}).`}
            </p>
          </article>
        </section>

        <section aria-live="polite" className="queue-panel">
          <div className="queue-heading">
            <div>
              <p className="eyebrow">Serialized activity</p>
              <h2>Queue status</h2>
            </div>
            <span className="event-state">
              Events {queue.connected ? 'connected' : 'reconnecting'}
            </span>
          </div>
          {queue.state === 'paused' ? (
            <div className="pause-banner" role="status">
              <div>
                <strong>Queue paused · {queue.pause_reason ?? 'operator hold'}</strong>
                <p>
                  {queue.resume_at
                    ? `Recommended resume after ${new Date(queue.resume_at).toLocaleString()}.`
                    : 'Resolve the local MCP condition, then resume explicitly.'}
                </p>
              </div>
              <button
                disabled={resume.isPending}
                onClick={() => resume.mutate()}
                type="button"
              >
                {resume.isPending ? 'Resuming…' : 'Resume queue'}
              </button>
            </div>
          ) : null}
          <div className="queue-list">
            {queue.jobs.length === 0 ? (
              <p className="queue-empty">No queued work. The browser slot is free.</p>
            ) : (
              queue.jobs.map((job) => (
                <article className="queue-job" key={job.id}>
                  <div>
                    <strong>{job.kind.replaceAll('_', ' ')}</strong>
                    <span>{job.state}</span>
                  </div>
                  <p>
                    {job.state === 'queued' || job.state === 'pending'
                      ? `Position ${job.position ?? '—'} of ${job.depth}`
                      : `Running${job.percent == null ? '' : ` · ${Math.round(job.percent)}%`}`}
                  </p>
                </article>
              ))
            )}
          </div>
        </section>

        <section className="guardrail-panel">
          <div>
            <p className="eyebrow">Locked guardrails</p>
            <h2>Human judgment stays in charge.</h2>
          </div>
          <ul>
            <li>No bulk or scheduled messages</li>
            <li>Every match points to retrieved profile text</li>
            <li>Profile data remains local through matching</li>
            <li>Sending stays disabled until all phase gates pass</li>
          </ul>
        </section>
      </main>

      <footer>
        <span>Single operator</span>
        <span>Loopback only</span>
        <span>Send gate: {health.data?.send_enabled ? 'enabled' : 'off'}</span>
      </footer>
    </div>
  )
}

export default App
