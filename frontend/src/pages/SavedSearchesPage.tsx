import { useQuery } from '@tanstack/react-query'
import { listSearches } from '../api/client'

export function SavedSearchesPage({ sessionId, onOpenRun, onSearch }: {
  sessionId: string
  onOpenRun: (id: string) => void
  onSearch: () => void
}) {
  const searches = useQuery({ queryKey: ['searches', sessionId], queryFn: () => listSearches(sessionId) })
  return <section className="workspace-page saved-searches-page" aria-labelledby="saved-title">
    <div className="page-intro"><div><h1 id="saved-title">Saved searches</h1><p>Pick up where you left off. Results and source text stay on this computer.</p></div><button className="primary-action" onClick={onSearch} type="button">Find candidates</button></div>
    {searches.isPending ? <p role="status">Loading saved searches…</p> : searches.isError ? <div className="form-error" role="alert">Could not load saved searches. <button className="quiet-action" onClick={() => void searches.refetch()} type="button">Try again</button></div> : searches.data.length ? <div className="saved-search-list">{searches.data.map(run => <button className="saved-search-row" key={run.id} onClick={() => onOpenRun(run.id)} type="button">
      <span className="saved-search-icon" aria-hidden="true">⌕</span><span><strong>{run.keywords}</strong><small>{run.location || 'Any location'} · {new Date(run.created_at).toLocaleDateString()} · {run.person_reference_count} profile references</small></span><span className="saved-run-status">{run.status.replaceAll('_', ' ')}</span><span aria-hidden="true">→</span>
    </button>)}</div> : <div className="empty-card"><h2>Your searches will appear here</h2><p>Set up a role and run your first search to get started.</p></div>}
  </section>
}
