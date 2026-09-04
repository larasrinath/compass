import { useCallback, useEffect, useState } from 'react'
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
import { parseAppRoute, pathForRoute, type AppRoute } from './routing'
import {
  useNewRevisionEffect,
  type EvidenceVerification,
} from './scoreVerification'
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
  const queryClient = useQueryClient()
  const [route, setRoute] = useState<AppRoute>(() =>
    parseAppRoute(window.location.pathname),
  )
  const [sessionLabel, setSessionLabel] = useState('Focused candidate search')
  const [verificationState, setVerificationState] = useState<{
    scope: string
    values: Map<string, EvidenceVerification>
  }>(() => ({ scope: '', values: new Map() }))
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth })
  const mcp = useQuery({
    queryKey: ['mcp-status'],
    queryFn: getMcpStatus,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: Infinity,
  })
  const session = useQuery({ queryKey: ['session'], queryFn: getSession })
  const brief = useQuery({
    queryKey: ['brief', session.data?.id],
    queryFn: () => getBrief(session.data!.id),
    enabled: Boolean(session.data?.id),
  })
  const queue = useJobEvents()
  const retrievalReady = mcp.data?.reachable === true && queue.state !== 'paused' && queue.connected
  const view = route.view
  const candidateId = route.candidateId
  const rankingUnlocked = Boolean(session.data?.phase_gates?.A)
  const verificationScope = `${session.data?.id ?? ''}:${brief.data?.id ?? ''}:${brief.data?.version ?? ''}`
  const verifiedEvidence =
    verificationState.scope === verificationScope
      ? verificationState.values
      : new Map<string, EvidenceVerification>()
  const clearEvidenceVerifications = useCallback(() => {
    setVerificationState({ scope: verificationScope, values: new Map() })
  }, [verificationScope])
  const reconcileEvidenceVerifications = useCallback(
    (values: Map<string, EvidenceVerification>) => {
      setVerificationState({ scope: verificationScope, values })
    },
    [verificationScope],
  )
  useNewRevisionEffect(queue.scoringRevision, clearEvidenceVerifications)
  const navigate = useCallback((next: AppRoute, replace = false) => {
    const path = pathForRoute(next)
    if (replace) window.history.replaceState(null, '', path)
    else window.history.pushState(null, '', path)
    setRoute(next)
  }, [])
  useEffect(() => {
    if (queue.revision > 0) void queryClient.invalidateQueries({ queryKey: ['session'] })
  }, [queryClient, queue.revision])

  useEffect(() => {
    if (window.location.pathname !== '/' || !session.isSuccess || !brief.isSuccess) return
    // Synchronize the initial browser URL with the asynchronously loaded session.
    // eslint-disable-next-line react/set-state-in-effect
    if (brief.data) navigate({ view: rankingUnlocked ? 'ranked' : 'search', candidateId: null }, true)
  }, [session.isSuccess, brief.isSuccess, brief.data, rankingUnlocked, navigate])

  const start = useMutation({
    mutationFn: createSession,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['session'] })
    },
  })

  useEffect(() => {
    const onPopState = () => setRoute(parseAppRoute(window.location.pathname))
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    document.title =
      view === 'brief'
        ? 'Role brief · LinkedIn Dashboard'
        : view === 'candidate'
          ? 'Candidate detail · LinkedIn Dashboard'
          : view === 'ranked'
            ? 'Ranked evidence · LinkedIn Dashboard'
            : 'Find candidates · LinkedIn Dashboard'
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
            Connector{' '}
            {mcp.isPending
              ? 'checking'
              : mcp.data?.reachable
                ? 'connected'
                : 'unavailable'}
          </span>
          {session.data ? (
            <span>
              Page reads {session.data.nav_used}/{session.data.nav_budget}
            </span>
          ) : null}
        </div>
      </header>

      <nav aria-label="Sourcing workflow" className="workflow-nav">
        <button
          aria-current={view === 'brief' ? 'page' : undefined}
          onClick={() => navigate({ view: 'brief', candidateId: null })}
          type="button"
        >
          <span>01</span> Role brief
        </button>
        <button
          aria-current={view === 'search' || (view === 'candidate' && !rankingUnlocked) ? 'page' : undefined}
          disabled={!brief.data}
          onClick={() => navigate({ view: 'search', candidateId: null })}
          type="button"
        >
          <span>02</span> Find candidates
        </button>
        <button
          aria-current={view === 'ranked' || (view === 'candidate' && rankingUnlocked) ? 'page' : undefined}
          disabled={!rankingUnlocked}
          title={!rankingUnlocked ? 'Review the candidate list to unlock comparison' : undefined}
          onClick={() => navigate({ view: 'ranked', candidateId: null })}
          type="button"
        >
          <span>03</span> Compare matches
        </button>
      </nav>

      <main id="main-content">
        <div className={mcp.data?.reachable ? 'connection-strip online' : 'connection-strip'} role="status">
          <div>
            <strong>{mcp.isFetching ? 'Checking the LinkedIn connector…' : mcp.data?.reachable ? 'LinkedIn connector connected' : 'LinkedIn downloads are offline'}</strong>
            <span>{mcp.data?.reachable ? 'Search and download profiles on demand. Your work is saved locally.' : 'Saved candidates, evidence, and local scoring remain available. Start the LinkedIn connector, then check again.'}</span>
          </div>
          <button className="quiet-action" disabled={mcp.isFetching} onClick={() => void mcp.refetch()} type="button">
            {mcp.isFetching ? 'Checking…' : 'Check connection'}
          </button>
        </div>
        {health.isError ? (
          <div className="blocking-banner" role="alert">
            Dashboard API unavailable. Your entered work stays in this browser.
          </div>
        ) : null}
        {session.isError ? (
          <div className="form-error" role="alert">Your saved workspace could not be loaded. <button className="quiet-action" onClick={() => void session.refetch()} type="button">Try again</button></div>
        ) : !session.isPending && !session.data ? (
          <section className="first-run" aria-labelledby="first-run-title">
            <p className="eyebrow">One-time local workspace</p>
            <h1 id="first-run-title">Your recruiting workspace</h1>
            <p>
              Set up a role, discover candidates, and compare their experience.
              Your saved profiles and evidence stay on this computer.
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
            brief.isPending ? <p role="status">Loading your role brief…</p> : brief.isError ? <div className="form-error" role="alert">Your role brief could not be loaded. <button className="quiet-action" onClick={() => void brief.refetch()} type="button">Try again</button></div> : <BriefPage
              current={brief.data}
              key={brief.data?.id ?? 'new-brief'}
              session={session.data}
              onSaved={() => navigate({ view: 'search', candidateId: null })}
            />
          ) : view === 'candidate' && candidateId ? (
            <CandidateDetailPage
              key={candidateId}
              backDestination={rankingUnlocked ? 'candidates' : 'search'}
              candidateId={candidateId}
              onBack={() =>
                navigate({
                  view: rankingUnlocked ? 'ranked' : 'search',
                  candidateId: null,
                })
              }
              onEvidenceVerified={(verification, verified) =>
                setVerificationState((current) => {
                  const values =
                    current.scope === verificationScope
                      ? new Map(current.values)
                      : new Map<string, EvidenceVerification>()
                  if (verified) values.set(verification.evidenceId, verification)
                  else values.delete(verification.evidenceId)
                  return { scope: verificationScope, values }
                })
              }
              onScoreInputsChanged={clearEvidenceVerifications}
              queue={queue}
              rankingUnlocked={rankingUnlocked}
              retrievalReady={retrievalReady}
              sessionId={session.data.id}
              verifiedEvidence={verifiedEvidence}
            />
          ) : view === 'ranked' ? (
            <CandidatesPage
              onEvidenceReconciled={reconcileEvidenceVerifications}
              onCandidateOpen={(id) => {
                navigate({ view: 'candidate', candidateId: id })
              }}
              onScoresChanged={clearEvidenceVerifications}
              session={session.data}
              verifiedEvidence={verifiedEvidence}
            />
          ) : (
            <SearchPage
              key={brief.data?.id ?? 'loading'}
              retrievalReady={retrievalReady}
              onEditBrief={() => navigate({ view: 'brief', candidateId: null })}
              brief={brief.data}
              onCandidateOpen={(id) => {
                navigate({ view: 'candidate', candidateId: id })
              }}
              onGateAChanged={() => navigate({ view: 'ranked', candidateId: null })}
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
        <span>Saved on this computer</span>
        <span>Read-only LinkedIn access</span>
      </footer>
    </div>
  )
}

export default App
