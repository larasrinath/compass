import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acceptPhaseGateA,
  enrichCandidate,
  getSearch,
  listCandidatePool,
  listSearches,
  runSearch,
} from '../api/client'
import type {
  BriefRecord,
  SearchRun,
  SearchRunStatus,
  SessionRecord,
} from '../api/client'
import { defaultSearchKeywords, readSearchSettings } from '../searchSettings'
import { ResultHeader } from '../components/ResultHeader'
import { CompassIcon } from '../components/CompassIcon'
import { QueueStatus } from '../components/QueueStatus'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

const GATE_A_ELIGIBLE_STATUSES = new Set<SearchRunStatus>([
  'ok',
  'partial',
  'rate_limited',
])

function gateAEligibilityMessage(searchRuns: SearchRun[]): string {
  if (searchRuns.some((run) => GATE_A_ELIGIBLE_STATUSES.has(run.status))) {
    return 'A saved search is ready for your review.'
  }
  if (!searchRuns.length) {
    return 'Complete a search to review your candidate list.'
  }
  const explanations: Record<
    Exclude<SearchRunStatus, 'ok' | 'partial' | 'rate_limited'>,
    string
  > = {
    queued: 'queued searches have not started',
    running: 'running searches have not finished',
    failed: 'failed searches produced no eligible result',
    interrupted: 'interrupted searches did not persist an eligible result',
    cancelled: 'cancelled searches did not persist an eligible result',
  }
  const reasons = [
    ...new Set(
      searchRuns.map(
        (run) => explanations[run.status as keyof typeof explanations],
      ),
    ),
  ]
  return `Comparison is locked: ${reasons.join('; ')}. Finish a search, then review the saved results.`
}

export function SearchPage({
  session,
  brief,
  onCandidateOpen,
  onGateAChanged,
  queue,
  retrievalReady = true,
  onEditBrief,
  initialRunId = null,
}: {
  session: SessionRecord
  brief: BriefRecord | null | undefined
  onCandidateOpen: (candidateId: string) => void
  onGateAChanged?: () => void
  queue: ReturnTypeOfJobEvents
  retrievalReady?: boolean
  onEditBrief?: () => void
  initialRunId?: string | null
}) {
  const client = useQueryClient()
  const errorRef = useRef<HTMLDivElement>(null)
  const enrichmentErrorRef = useRef<HTMLDivElement>(null)
  const [poolRun, setPoolRun] = useState<string | null>(initialRunId)
  const [nameFilter, setNameFilter] = useState('')
  const settings = readSearchSettings(brief?.id)
  const keywords = settings.keywords.trim() || defaultSearchKeywords(brief)
  const [selectedRun, setSelectedRun] = useState<string | null>(initialRunId)
  const [gateNote, setGateNote] = useState('')

  const runs = useQuery({
    queryKey: ['searches', session.id],
    queryFn: () => listSearches(session.id),
  })
  const candidates = useQuery({
    queryKey: ['candidates', session.id],
    queryFn: () => listCandidatePool(session.id),
  })
  const detail = useQuery({
    queryKey: ['search', selectedRun],
    queryFn: () => getSearch(selectedRun!),
    enabled: Boolean(selectedRun),
  })

  useEffect(() => {
    if (queue.revision === 0) return
    void client.invalidateQueries({ queryKey: ['searches', session.id] })
    void client.invalidateQueries({ queryKey: ['candidates', session.id] })
    if (selectedRun) {
      void client.invalidateQueries({ queryKey: ['search', selectedRun] })
    }
  }, [client, queue.revision, selectedRun, session.id])

  const search = useMutation({
    mutationFn: runSearch,
    onSuccess: async (result) => {
      setSelectedRun(result.search_run_id)
      setPoolRun(result.search_run_id)
      await client.invalidateQueries({ queryKey: ['searches', session.id] })
    },
    onError: () => requestAnimationFrame(() => errorRef.current?.focus()),
  })
  const enrich = useMutation({
    mutationFn: (candidateId: string) =>
      enrichCandidate(candidateId, ['experience']),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['candidates', session.id] })
    },
    onError: () =>
      requestAnimationFrame(() => enrichmentErrorRef.current?.focus()),
  })
  const gateA = useMutation({
    mutationFn: () => acceptPhaseGateA(gateNote),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['session'] })
      onGateAChanged?.()
    },
    onError: () => requestAnimationFrame(() => errorRef.current?.focus()),
  })
  const gateAEligible = Boolean(
    runs.data?.some((run) => GATE_A_ELIGIBLE_STATUSES.has(run.status)),
  )
  const gateAEligibility = gateAEligibilityMessage(runs.data ?? [])

  const positiveSet = new Set((brief?.positive_keywords ?? []).map((term) => term.normalize('NFKC').toLowerCase()))
  const hasConflicts = (brief?.negative_keywords ?? []).some((term) => positiveSet.has(term.normalize('NFKC').toLowerCase()))
  const downloadsBlocked = !retrievalReady || hasConflicts
  const filteredCandidates = (candidates.data ?? []).filter((candidate) => (!poolRun || candidate.sources.some(source => source.search_run_id === poolRun)) && `${candidate.display_name} ${candidate.username}`.toLowerCase().includes(nameFilter.toLowerCase()))

  function queuePosition(jobId: string): string | null {
    const job = queue.jobs.find((item) => item.id === jobId)
    if (!job) return null
    if (job.state === 'running') return 'Running LinkedIn people search'
    return `Queue position ${job.position ?? '—'} of ${job.depth}`
  }

  return (
    <section aria-labelledby="search-title" className="workspace-page search-workspace">
      <ResultHeader brief={brief} titleId="search-title" fallback="Find candidates" subtitle="Find people using your saved role brief, then save profiles to review." onEdit={onEditBrief} compact action={brief ? (
        <button className="primary-action" disabled={search.isPending || downloadsBlocked || !keywords} type="button" onClick={() => {
          if (downloadsBlocked || !keywords) return
          search.mutate({ session_id: session.id, brief_id: brief.id, keywords, location: brief.location || null, network: settings.network.length ? settings.network : null, current_company: settings.companyId || null })
        }}>{search.isPending ? 'Queueing…' : 'Run search'}<CompassIcon name="search" size={16} /></button>
      ) : null} />

      <QueueStatus queue={queue} />
      {hasConflicts ? <div className="form-error brief-conflict" role="alert"><strong>Your role brief has conflicting keywords.</strong><span>A keyword is both included and excluded. Correct the brief before searching.</span>{onEditBrief ? <button className="quiet-action" onClick={onEditBrief} type="button">Edit role brief</button> : null}</div> : null}

      {!brief ? (
        <div className="empty-card" role="status"><h2>Save a role brief first.</h2><p>Set up your search criteria on the Role brief page.</p>{onEditBrief ? <button className="quiet-action" type="button" onClick={onEditBrief}>Set up role brief</button> : null}</div>
      ) : !keywords ? <p className="field-help">Add a role title or key filter to your brief before searching.</p> : null}
      {search.isError ? <div className="form-error" ref={errorRef} role="alert" tabIndex={-1}><strong>Search was not queued.</strong><span>{search.error.message}</span></div> : null}
      {!retrievalReady ? <p className="field-help download-help">Downloads are paused or offline. Check the connector and resume paused downloads above. Saved candidates remain available.</p> : null}
      {search.data ? <p aria-live="polite" className="queued-confirmation">Search queued. Results will appear automatically in your candidate list.</p> : null}

      <section aria-labelledby="pool-title" className="discovery-section candidate-pool">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Saved candidates</p>
            <h2 id="pool-title">Candidate pool</h2>
          </div>
          <div className="pool-heading-meta"><span>{filteredCandidates.length} shown · first-seen order</span><div className="pool-heading-actions">{session.phase_gates?.A ? <><span className="pool-review-status">List reviewed</span>{onGateAChanged ? <button className="quiet-action" type="button" onClick={onGateAChanged}>Compare candidates <CompassIcon name="compare" size={16} /></button> : null}</> : gateAEligible ? <a href="#pool-review">Review list to compare →</a> : null}</div></div>
        </div>
        <div className="pool-filter-row"><label className="field pool-filter"><span>Results from</span><select value={poolRun ?? ''} onChange={event => { setPoolRun(event.target.value || null); setSelectedRun(event.target.value || null) }}><option value="">All saved searches</option>{runs.data?.map(run => <option key={run.id} value={run.id}>{run.keywords} · {new Date(run.created_at).toLocaleDateString()}</option>)}</select></label>
        <label className="field pool-filter"><span>Find a saved candidate</span><input placeholder="Search by name" value={nameFilter} onChange={(event) => setNameFilter(event.target.value)} /></label></div>
        {!session.phase_gates?.A ? (
        <details id="pool-review" className="pool-review phase-gate-card panel" open={gateAEligible && !session.phase_gates?.A}>
          <summary>Review candidate list & unlock comparison</summary>
          <div>
          <div>
            <p className="eyebrow">Candidate review</p>
            <h3>Check names and duplicates</h3>
            <p>
              Review names and source searches for duplicates, then confirm to compare candidates.
            </p>
          </div>
            <form
              onSubmit={(event) => {
                event.preventDefault()
                gateA.mutate()
              }}
            >
              <label className="field">
                <span>Inspection note</span>
                <textarea
                  onChange={(event) => setGateNote(event.target.value)}
                  required
                  rows={2}
                  value={gateNote}
                />
              </label>
              <button
                aria-describedby="gate-a-eligibility"
                className="primary-action"
                disabled={gateA.isPending || !gateAEligible}
                type="submit"
              >
                {gateA.isPending ? 'Recording…' : 'Confirm review & compare'}
              </button>
              <p id="gate-a-eligibility" role="status">
                {gateAEligibility}
              </p>
              {gateA.isError ? (
                <p className="field-error" role="alert">
                  {gateA.error.message}
                </p>
              ) : null}
            </form>
        </div>
        </details>
        ) : null}
      <details className="discovery-section search-history simple-options">
        <summary>Search history</summary>
        <div className="section-heading">
          <div>
            <p className="eyebrow">Provenance</p>
            <h2 id="runs-title">Search run history</h2>
          </div>
          <span>{runs.data?.length ?? 0} total runs</span>
        </div>
        {runs.isError ? <p className="form-error" role="alert">Search history could not be loaded.</p> : runs.data?.length ? (
          <div className="run-grid">
            {runs.data.map((run) => (
              <button
                className={selectedRun === run.id ? 'run-card selected' : 'run-card'}
                key={run.id}
                onClick={() => { setSelectedRun(run.id); setPoolRun(run.id) }}
                type="button"
              >
                <span className={`status-label ${run.status}`}>{run.status}</span>
                <strong>{run.keywords}</strong>
                <span>
                  {run.person_reference_count} of {run.reference_count} references were
                  people
                </span>
                <small>
                  {run.new_candidate_count} new · {run.existing_candidate_count} already
                  in pool
                </small>
                <small>
                  {run.location || 'Any location'} ·{' '}
                  {run.network.length ? run.network.join('/') : 'Any network'} ·{' '}
                  {run.current_company ? `Company ${run.current_company}` : 'Any company'}
                </small>
                <small>
                  {queuePosition(run.job_id) ??
                    new Date(run.created_at).toLocaleString()}
                </small>
              </button>
            ))}
          </div>
        ) : (
          <div className="empty-card">
            <h3>No searches run</h3>
            <p>Your first narrow search will appear here without replacing later runs.</p>
          </div>
        )}
            {detail.data ? (
        <section aria-labelledby="run-detail-title" className="run-detail panel">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Stored run</p>
              <h2 id="run-detail-title">{detail.data.keywords}</h2>
            </div>
            <strong>
              {detail.data.person_reference_count} of {detail.data.reference_count}{' '}
              references were people
            </strong>
          </div>
          <dl className="run-parameters">
            <div><dt>Location</dt><dd>{detail.data.location || 'Any location'}</dd></div>
            <div><dt>Network evidence</dt><dd>{detail.data.network.length ? detail.data.network.join(', ') : 'Any network'}</dd></div>
            <div><dt>Current company</dt><dd>{detail.data.current_company || 'Any company'}</dd></div>
            <div><dt>Created</dt><dd>{new Date(detail.data.created_at).toLocaleString()}</dd></div>
          </dl>
          {detail.data.reference_count === 15 ? (
            <p className="cap-notice">
              LinkedIn returned the shared 15-reference cap. Non-person references can
              use part of that limit; run another narrower search instead of assuming
              more pages exist.
            </p>
          ) : null}
          {detail.data.errors.map((error, index) => (
            <div className="inline-error" key={`${error.error_type}-${index}`} role="alert">
              <strong>{error.error_type.replaceAll('_', ' ')}</strong>
              <span>{error.error_message}</span>
            </div>
          ))}
          <div className="reference-breakdown">
            {Object.entries(detail.data.reference_kind_counts).map(([kind, count]) => (
              <span key={kind}>
                {kind}: {count}
              </span>
            ))}
          </div>
          <details>
            <summary>View raw search text</summary>
            <pre aria-label="Raw LinkedIn search results">
              {detail.data.raw_text || 'No raw search text returned.'}
            </pre>
          </details>
          <details>
            <summary>View ordered references</summary>
            <ol className="reference-list">
              {detail.data.references.map((reference) => (
                <li key={`${reference.position}-${reference.kind}`}>
                  <strong>{reference.kind}</strong>
                  <span>{reference.text ?? reference.url ?? reference.value ?? 'No label'}</span>
                </li>
              ))}
            </ol>
          </details>
        </section>
      ) : null}
      </details>
        {candidates.isError ? <div className="form-error" role="alert">Saved candidates could not be loaded. <button className="quiet-action" onClick={() => void candidates.refetch()} type="button">Try again</button></div> : null}
        {enrich.isError ? (
          <div
            className="form-error"
            ref={enrichmentErrorRef}
            role="alert"
            tabIndex={-1}
          >
            <strong>Profile retrieval was not queued.</strong>
            <span>{enrich.error.message}</span>
          </div>
        ) : null}
        {candidates.isPending ? <p role="status">Loading saved candidates…</p> : filteredCandidates.length ? (
          <div className="candidate-grid">
            {filteredCandidates.map((candidate) => (
              <article className="candidate-card" key={candidate.id}>
                <div className="discovery-person-heading"><span className="result-initials" aria-hidden="true">{(candidate.display_name || candidate.username).split(/\s+/).slice(0, 2).map(part => part[0]).join('')}</span><div>
                  <span className={`status-label ${candidate.stage}`}>
                    {candidate.stage === 'discovered' ? 'Discovered' : candidate.stage === 'stage1' ? 'Profile saved' : 'More details saved'}
                  </span>
                  <h3>{candidate.display_name || candidate.username}</h3>
                  <p>
                    {candidate.active_job_id
                      ? 'Profile retrieval queued'
                      : candidate.retrieval_status === 'failed'
                        ? 'Profile retrieval failed'
                        : candidate.stage === 'discovered'
                          ? 'Profile not retrieved'
                      : `Profile ${candidate.retrieval_status}`}{' '}
                    · found in {candidate.source_count} {candidate.source_count === 1 ? 'search' : 'searches'}
                  </p>
                </div></div>
                {candidate.active_job_id ? (
                  <button className="primary-action" disabled type="button">
                    Retrieval queued
                  </button>
                ) : candidate.retrieval_status === 'failed' ? (
                  <button
                    className="quiet-action"
                    onClick={() => onCandidateOpen(candidate.id)}
                    type="button"
                  >
                    Review retrieval failure
                  </button>
                ) : candidate.stage === 'discovered' ? (
                  <button
                    className="quiet-action save-profile-action"
                    aria-label="Download profile & experience"
                    disabled={
                      downloadsBlocked || (enrich.isPending && enrich.variables === candidate.id)
                    }
                    onClick={() => enrich.mutate(candidate.id)}
                    type="button"
                  >
                    Save profile
                  </button>
                ) : (
                  <button
                    className="quiet-action"
                    onClick={() => onCandidateOpen(candidate.id)}
                    type="button"
                  >
                    Review <CompassIcon name="arrow" size={16} />
                  </button>
                )}
                <a
                  href={candidate.profile_url}
                  rel="noopener noreferrer"
                  target="_blank"
                  aria-label={`Open ${candidate.display_name || candidate.username} on LinkedIn (new tab)`}
                >
                  LinkedIn profile ↗
                </a>
                <details>
                  <summary>{candidate.source_count} source {candidate.source_count === 1 ? 'search' : 'searches'}</summary>
                  <ol className="source-list">
                    {candidate.sources.map((source) => (
                      <li key={source.search_run_id}>
                        <strong>{source.keywords}</strong>
                        <span>{source.reference_context || source.reference_text}</span>
                        <span>
                          {new Date(source.created_at).toLocaleString()} · reference{' '}
                          {source.reference_position + 1} ·{' '}
                          {source.location || 'any location'} ·{' '}
                          {source.network_filter.length
                            ? `network ${source.network_filter.join('/')}`
                            : 'any network'}
                          {source.current_company
                            ? ` · company ${source.current_company}`
                            : ''}
                        </span>
                        <small>{source.notice}</small>
                      </li>
                    ))}
                  </ol>
                </details>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-card">
            <h3>{nameFilter ? 'No matching names' : 'Your next hire starts with a search'}</h3>
            <p>{nameFilter ? 'Try a different name or clear the filter.' : 'Run a focused LinkedIn search. Candidates are saved here with their source, ready to review and compare.'}</p>
          </div>
        )}

      </section>




    </section>
  )
}
