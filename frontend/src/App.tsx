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
import { SavedSearchesPage } from './pages/SavedSearchesPage'
import { SearchPage } from './pages/SearchPage'
import { parseAppRoute, pathForRoute, type AppRoute } from './routing'
import {
  useNewRevisionEffect,
  type EvidenceVerification,
} from './scoreVerification'
import { CandidateDrawer } from './components/CandidateDrawer'
import { CompassIcon } from './components/CompassIcon'
import './fonts.css'
import './App.css'
import './compass.css'
import './search-setup.css'
import './results.css'
import './saved-searches.css'
import './candidate-profile.css'
import './controls.css'

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
  const [comparisonIds, setComparisonIds] = useState<string[]>([])
  const [sourceRun, setSourceRun] = useState<string | null>(null)
  const [candidateBackground, setCandidateBackground] = useState<AppRoute | null>(() => typeof window.history.state?.compassBackground === 'string' ? parseAppRoute(window.history.state.compassBackground) : null)
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
  const contentView = view === 'candidate' ? candidateBackground?.view ?? (rankingUnlocked ? 'ranked' : 'search') : view
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
    const background = next.view === 'candidate' ? (route.view === 'candidate' ? candidateBackground : route) : null
    const state = background ? { compassBackground: pathForRoute(background) } : null
    if (replace) window.history.replaceState(state, '', path)
    else window.history.pushState(state, '', path)
    setCandidateBackground(background)
    setRoute(next)
  }, [route, candidateBackground])
  const closeCandidate = () => {
    if (window.history.state?.compassBackground) window.history.back()
    else navigate({ view: rankingUnlocked ? 'ranked' : 'search', candidateId: null }, true)
  }
  useEffect(() => {
    if (queue.revision > 0) {
      void queryClient.invalidateQueries({ queryKey: ['session'] })
      void queryClient.invalidateQueries({ queryKey: ['ranked-candidates'] })
      void queryClient.invalidateQueries({ queryKey: ['candidates'] })
      void queryClient.invalidateQueries({ queryKey: ['searches'] })
    }
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
    const onPopState = () => {
      setRoute(parseAppRoute(window.location.pathname))
      setCandidateBackground(typeof window.history.state?.compassBackground === 'string' ? parseAppRoute(window.history.state.compassBackground) : null)
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    document.title =
      view === 'brief'
        ? 'Role brief · Compass'
        : view === 'candidate'
          ? 'Candidate detail · Compass'
          : view === 'ranked'
            ? 'Ranked evidence · Compass'
            : view === 'saved' ? 'Saved searches · Compass' : 'Find candidates · Compass'
  }, [view])

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <header className="topbar">
        <div aria-hidden="true" className="brand-mark">
          <img src="/favicon.svg?v=compass" width="28" height="28" alt="" />
        </div>
        <div>
          <p className="eyebrow">Personal recruiting</p>
          <strong className="brand-title">Compass</strong>
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
          {mcp.data?.reachable ? <button className="connection-recheck" disabled={mcp.isFetching} onClick={() => void mcp.refetch()} type="button">{mcp.isFetching ? 'Checking…' : 'Check connection'}</button> : null}
          {session.data ? (
            <span>
              Page reads {session.data.nav_used}/{session.data.nav_budget}
            </span>
          ) : null}
        </div>
      </header>

      <nav aria-label="Sourcing workflow" className="workflow-nav">
        <button
          aria-current={contentView === 'brief' ? 'page' : undefined}
          onClick={() => navigate({ view: 'brief', candidateId: null })}
          type="button"
        >
          <span className="nav-icon" aria-hidden="true"><CompassIcon name="brief" /></span><span className="nav-label">Role brief</span>
        </button>
        <button
          aria-current={contentView === 'search' ? 'page' : undefined}
          disabled={!brief.data}
          onClick={() => { setSourceRun(null); navigate({ view: 'search', candidateId: null }) }}
          type="button"
        >
          <span className="nav-icon" aria-hidden="true"><CompassIcon name="search" /></span><span className="nav-label">Find candidates</span>
        </button>
        <button aria-current={contentView === 'saved' ? 'page' : undefined} disabled={!session.data} onClick={() => navigate({ view: 'saved', candidateId: null })} type="button"><span className="nav-icon" aria-hidden="true"><CompassIcon name="folder" /></span><span className="nav-label">Saved searches</span></button>
        <button
          aria-current={contentView === 'ranked' ? 'page' : undefined}
          disabled={!rankingUnlocked}
          title={!rankingUnlocked ? 'Review the candidate list to unlock comparison' : undefined}
          onClick={() => navigate({ view: 'ranked', candidateId: null })}
          type="button"
        >
          <span className="nav-icon" aria-hidden="true"><CompassIcon name="compare" /></span><span className="nav-label">Compare matches</span>
        </button>
      </nav>

      <main id="main-content">
        {!mcp.data?.reachable ? <div className="connection-strip" role="status">
          <div>
            <strong>{mcp.isFetching ? 'Checking the LinkedIn connector…' : mcp.data?.reachable ? 'LinkedIn connector connected' : 'LinkedIn downloads are offline'}</strong>
            <span>{mcp.data?.reachable ? 'Search and download profiles on demand. Your work is saved locally.' : 'Saved candidates, evidence, and local scoring remain available. Start the LinkedIn connector, then check again.'}</span>
          </div>
          <button className="quiet-action" disabled={mcp.isFetching} onClick={() => void mcp.refetch()} type="button">
            {mcp.isFetching ? 'Checking…' : 'Check connection'}
          </button>
        </div> : null}
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
          contentView === 'brief' ? (
            brief.isPending ? <p role="status">Loading your role brief…</p> : brief.isError ? <div className="form-error" role="alert">Your role brief could not be loaded. <button className="quiet-action" onClick={() => void brief.refetch()} type="button">Try again</button></div> : <BriefPage
              retrievalReady={retrievalReady}
              queueRevision={queue.revision}
              current={brief.data}
              key={brief.data?.id ?? 'new-brief'}
              session={session.data}
              onSaved={() => navigate({ view: 'search', candidateId: null })}
            />
          ) : contentView === 'saved' ? (
            <SavedSearchesPage sessionId={session.data.id} onOpenCandidate={id => { navigate({ view: 'candidate', candidateId: id }) }} onSearch={() => { setSourceRun(null); navigate({ view: 'search', candidateId: null }) }} onOpenRun={id => { setSourceRun(id); navigate({ view: 'search', candidateId: null }) }} />
          ) : contentView === 'ranked' ? (
            <CandidatesPage
              brief={brief.data}
              onEditBrief={() => navigate({ view: 'brief', candidateId: null })}
              selectedForComparison={comparisonIds}
              onComparisonChange={setComparisonIds}
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
              key={`${brief.data?.id ?? 'loading'}:${sourceRun ?? 'all'}`}
              initialRunId={sourceRun}
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

      {view === 'candidate' && candidateId && session.data ? <CandidateDrawer onClose={closeCandidate}>
            <CandidateDetailPage
              key={candidateId}
              backDestination={contentView === 'saved' ? 'saved searches' : contentView === 'ranked' ? 'candidates' : 'search'}
              candidateId={candidateId}
              onBack={closeCandidate}
              comparing={comparisonIds.includes(candidateId)}
              comparisonFull={comparisonIds.length >= 3}
              onCompare={() => setComparisonIds(ids => ids.includes(candidateId) ? ids.filter(id => id !== candidateId) : ids.length < 3 ? [...ids, candidateId] : ids)}
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
      </CandidateDrawer> : null}

      <footer>
        <strong>Scores rank retrieved evidence, not people.</strong>
        <span>Saved on this computer</span>
        <span>Read-only LinkedIn access</span>
      </footer>
    </div>
  )
}

export default App
