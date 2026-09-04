import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createSession,
  getBrief,
  getHealth,
  getMcpStatus,
  getSession,
} from './api/client'
import { useJobEvents } from './hooks/useJobEvents'
import { BriefPage } from './pages/BriefPage'
import { CandidateDetailPage } from './pages/CandidateDetailPage'
import { CandidatesPage } from './pages/CandidatesPage'
import { SearchPage } from './pages/SearchPage'
import './App.css'

type View = 'brief' | 'search' | 'ranked' | 'candidate'

function StatusDot({ healthy }: { healthy: boolean }) {
  return (
    <span
      aria-hidden="true"
      className={healthy ? 'status-dot healthy' : 'status-dot'}
    />
  )
}

function App() {
  const queryClient = useQueryClient()
  const [view, setView] = useState<View>(() => {
    if (window.location.pathname === '/search') return 'search'
    if (window.location.pathname === '/candidates') return 'ranked'
    return 'brief'
  })
  const [sessionLabel, setSessionLabel] = useState('Focused candidate search')
  const [candidateId, setCandidateId] = useState<string | null>(null)
  const [verifiedEvidenceIds, setVerifiedEvidenceIds] = useState<Set<string>>(
    () => new Set(),
  )
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth })
  const mcp = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMcpStatus,
    retry: false,
  })
  const session = useQuery({ queryKey: ['session'], queryFn: getSession })
  const brief = useQuery({
    queryKey: ['brief', session.data?.id],
    queryFn: () => getBrief(session.data!.id),
    enabled: Boolean(session.data?.id),
  })
  const queue = useJobEvents()
  const start = useMutation({
    mutationFn: createSession,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
    },
  })

  useEffect(() => {
    document.title =
      view === 'brief'
        ? 'Role brief · LinkedIn Dashboard'
        : view === 'candidate'
          ? 'Candidate detail · LinkedIn Dashboard'
          : view === 'ranked'
            ? 'Ranked evidence · LinkedIn Dashboard'
            : 'Find candidates · LinkedIn Dashboard'
    const path =
      view === 'brief'
        ? '/brief'
        : view === 'candidate'
          ? '/candidate'
          : view === 'ranked'
            ? '/candidates'
            : '/search'
    if (window.location.pathname !== path) {
      window.history.replaceState(null, '', path)
    }
  }, [view])

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <div aria-hidden="true" className="brand-mark">
          in
        </div>
        <div>
          <p className="eyebrow">Private sourcing workspace</p>
          <strong className="brand-title">LinkedIn Dashboard</strong>
        </div>
        <div className="topbar-status">
          <span className="local-badge">Local only</span>
          <span>
            <StatusDot healthy={mcp.data?.reachable === true} />
            MCP{' '}
            {mcp.isPending
              ? 'checking'
              : mcp.data?.reachable
                ? 'ready'
                : 'unavailable'}
          </span>
          {session.data ? (
            <span>
              Navigation {session.data.nav_used}/{session.data.nav_budget}
            </span>
          ) : null}
        </div>
      </header>

      <nav aria-label="Sourcing workflow" className="workflow-nav">
        <button
          aria-current={view === 'brief' ? 'page' : undefined}
          onClick={() => setView('brief')}
          type="button"
        >
          <span>01</span> Role brief
        </button>
        <button
          aria-current={view === 'search' ? 'page' : undefined}
          disabled={!brief.data}
          onClick={() => setView('search')}
          type="button"
        >
          <span>02</span> Find candidates
        </button>
        <button
          aria-current={view === 'ranked' || view === 'candidate' ? 'page' : undefined}
          disabled={!session.data?.phase_gates?.A}
          onClick={() => setView('ranked')}
          type="button"
        >
          <span>03</span> Ranked evidence
        </button>
      </nav>

      <main id="main-content">
        {health.isError ? (
          <div className="blocking-banner" role="alert">
            Dashboard API unavailable. Your entered work stays in this browser.
          </div>
        ) : null}
        {!session.isPending && !session.data ? (
          <section className="first-run" aria-labelledby="first-run-title">
            <p className="eyebrow">One-time local workspace</p>
            <h1 id="first-run-title">Start a focused sourcing session.</h1>
            <p>
              Profile information is stored only in your owner-protected local
              database. This discovery milestone cannot send messages.
            </p>
            {start.isError ? (
              <div className="form-error" role="alert">
                {start.error.message}
              </div>
            ) : null}
            <form
              onSubmit={(event) => {
                event.preventDefault()
                start.mutate(sessionLabel)
              }}
            >
              <label className="field">
                <span>Session name</span>
                <input
                  onChange={(event) => setSessionLabel(event.target.value)}
                  required
                  value={sessionLabel}
                />
              </label>
              <button
                className="primary-action"
                disabled={start.isPending}
                type="submit"
              >
                {start.isPending ? 'Starting…' : 'Start local session'}
              </button>
            </form>
          </section>
        ) : session.data ? (
          view === 'brief' ? (
            <BriefPage
              current={brief.data}
              key={brief.data?.id ?? 'new-brief'}
              session={session.data}
            />
          ) : view === 'candidate' && candidateId ? (
            <CandidateDetailPage
              candidateId={candidateId}
              onBack={() =>
                setView(session.data?.phase_gates?.A ? 'ranked' : 'search')
              }
              onEvidenceVerified={(evidenceId, verified) =>
                setVerifiedEvidenceIds((current) => {
                  const next = new Set(current)
                  if (verified) next.add(evidenceId)
                  else next.delete(evidenceId)
                  return next
                })
              }
              queue={queue}
              verifiedEvidenceIds={verifiedEvidenceIds}
            />
          ) : view === 'ranked' ? (
            <CandidatesPage
              onCandidateOpen={(id) => {
                setCandidateId(id)
                setView('candidate')
              }}
              session={session.data}
              verifiedEvidenceIds={verifiedEvidenceIds}
            />
          ) : (
            <SearchPage
              brief={brief.data}
              onCandidateOpen={(id) => {
                setCandidateId(id)
                setView('candidate')
              }}
              onGateAChanged={() => setView('ranked')}
              queue={queue}
              session={session.data}
            />
          )
        ) : (
          <p aria-live="polite">Opening your local workspace…</p>
        )}
      </main>

      <footer>
        <strong>Scores rank retrieved evidence, not people.</strong>
        <span>Single operator</span>
        <span>Loopback only</span>
        <span>Send gate: off</span>
      </footer>
    </div>
  )
}

export default App
