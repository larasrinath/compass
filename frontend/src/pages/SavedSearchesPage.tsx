import { useQuery } from '@tanstack/react-query'
import { listCandidatePool, listSearches, type SearchRun } from '../api/client'
import { CompassIcon } from '../components/CompassIcon'

function searchTitle(run: SearchRun) {
  // Query punctuation belongs to the search, not its display name.
  let title = run.keywords.replace(/["“”]/g, '').replace(/\s+/g, ' ').trim()
  const location = run.location?.trim()
  if (location && !/\b(?:AND|OR|NOT)\b|[()]/.test(title) &&
      title.toLocaleLowerCase().endsWith(` ${location.toLocaleLowerCase()}`)) {
    title = title.slice(0, -(location.length + 1)).trim()
  }
  return title || 'Candidate search'
}

function runSummary(run: SearchRun) {
  const date = new Date(run.created_at).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  })
  const status = {
    ok: '', partial: 'Partial results', rate_limited: 'Search limited',
    queued: 'Queued', running: 'Searching', failed: 'Search failed',
    interrupted: 'Interrupted', cancelled: 'Cancelled',
  }[run.status]
  return [
    run.location?.trim().replace(/^./, letter => letter.toLocaleUpperCase()),
    `${run.person_reference_count} profile references`,
    `last run ${date}`,
    status,
  ].filter(Boolean).join(' · ')
}

export function SavedSearchesPage({ sessionId, onOpenRun, onSearch, onOpenCandidate }: {
  sessionId: string
  onOpenRun: (id: string) => void
  onSearch: () => void
  onOpenCandidate?: (id: string) => void
}) {
  const searches = useQuery({
    queryKey: ['searches', sessionId],
    queryFn: () => listSearches(sessionId),
  })
  const profiles = useQuery({
    queryKey: ['candidates', sessionId],
    queryFn: () => listCandidatePool(sessionId),
    enabled: Boolean(searches.data?.length),
  })

  return (
    <section className="saved-searches-page" aria-labelledby="saved-title">
      <header className="saved-searches-heading">
        <h1 id="saved-title">Saved searches</h1>
        <p>Pick up where you left off. Each search keeps its results and saved profiles together.</p>
      </header>
      {searches.isPending ? <p role="status">Loading saved searches…</p> : searches.isError ? (
        <div className="form-error" role="alert">
          Could not load saved searches.
          <button className="quiet-action" onClick={() => void searches.refetch()} type="button">Try again</button>
        </div>
      ) : searches.data.length ? (
        <div className="saved-search-list">
          {searches.data.map(run => {
            const saved = (profiles.data ?? []).filter(candidate =>
              candidate.stage !== 'discovered' &&
              candidate.sources.some(source => source.search_run_id === run.id),
            )
            return (
              <section className="saved-search-card" key={run.id} aria-labelledby={`saved-run-${run.id}`}>
                <button className="saved-search-open" onClick={() => onOpenRun(run.id)} type="button">
                  <div className="saved-search-description">
                    <h2 id={`saved-run-${run.id}`}>{searchTitle(run)}</h2>
                    <p>{runSummary(run)}</p>
                  </div>
                  <span className="saved-search-link">Open results <CompassIcon name="chevron" size={16} /></span>
                </button>
                <div className="saved-search-profiles">
                  {profiles.isPending ? <p className="saved-search-note" role="status">Loading saved profiles…</p> : profiles.isError ? (
                    <p className="saved-search-note" role="alert">Saved profiles could not be loaded. <button className="text-action" type="button" onClick={() => void profiles.refetch()}>Try again</button></p>
                  ) : saved.length ? (
                    <ul>
                      {saved.slice(0, 3).map(candidate => (
                        <li key={candidate.id}>
                          <div className="saved-profile-description">
                            <p className="saved-profile-name">{candidate.display_name || candidate.username}</p>
                            <p className="saved-profile-meta"><span aria-hidden="true" className="saved-profile-dot" />{candidate.stage === 'stage2' ? 'Profile and extra sections saved' : 'Profile saved'} · available offline</p>
                          </div>
                          <button className="saved-profile-review" aria-label={`Review ${candidate.display_name || candidate.username}`} onClick={() => onOpenCandidate ? onOpenCandidate(candidate.id) : onOpenRun(run.id)} type="button">Review</button>
                        </li>
                      ))}
                    </ul>
                  ) : <p className="saved-search-note"><CompassIcon name="bookmark" size={16} /><span>No saved profiles yet. Open the results to save someone for review.</span></p>}
                </div>
              </section>
            )
          })}
        </div>
      ) : (
        <div className="saved-search-empty">
          <span className="saved-search-empty-icon"><CompassIcon name="search" /></span>
          <h2>No searches yet</h2>
          <p>Run a candidate search and it will be saved here automatically.</p>
          <button className="primary-action" onClick={onSearch} type="button">Find candidates</button>
        </div>
      )}
    </section>
  )
}
