import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acceptPhaseGateA,
  enrichCandidate,
  getCompanyLookup,
  getSearch,
  listCandidatePool,
  listSearches,
  runSearch,
  startCompanyLookup,
} from '../api/client'
import type {
  BriefRecord,
  SearchRun,
  SearchRunStatus,
  SessionRecord,
} from '../api/client'
import { QueueStatus } from '../components/QueueStatus'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

const NETWORKS = [
  { value: 'F', label: '1st-degree connections' },
  { value: 'S', label: '2nd-degree connections' },
  { value: 'O', label: '3rd-degree and beyond' },
] as const

const GATE_A_ELIGIBLE_STATUSES = new Set<SearchRunStatus>([
  'ok',
  'partial',
  'rate_limited',
])

function gateAEligibilityMessage(searchRuns: SearchRun[]): string {
  if (searchRuns.some((run) => GATE_A_ELIGIBLE_STATUSES.has(run.status))) {
    return 'An eligible persisted search result is ready for inspection.'
  }
  if (!searchRuns.length) {
    return 'Gate A remains locked until a search persists an eligible result.'
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
  return `Gate A remains locked: ${reasons.join('; ')}. Eligible persisted statuses are ok, partial, or rate limited.`
}

export function SearchPage({
  session,
  brief,
  onCandidateOpen,
  onGateAChanged,
  queue,
}: {
  session: SessionRecord
  brief: BriefRecord | null | undefined
  onCandidateOpen: (candidateId: string) => void
  onGateAChanged?: () => void
  queue: ReturnTypeOfJobEvents
}) {
  const client = useQueryClient()
  const errorRef = useRef<HTMLDivElement>(null)
  const enrichmentErrorRef = useRef<HTMLDivElement>(null)
  const [keywords, setKeywords] = useState(() =>
    brief
      ? [
          ...brief.target_titles.map((item) => item.term),
          ...brief.positive_keywords,
        ]
          .slice(0, 4)
          .join(' ')
      : '',
  )
  const [location, setLocation] = useState(() => brief?.location ?? '')
  const [network, setNetwork] = useState<string[]>(['F', 'S'])
  const [companyId, setCompanyId] = useState('')
  const [companySlug, setCompanySlug] = useState('')
  const [lookupId, setLookupId] = useState<string | null>(null)
  const [selectedRun, setSelectedRun] = useState<string | null>(null)
  const [gateNote, setGateNote] = useState('Candidate extraction and dedupe inspected.')

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
  const lookup = useQuery({
    queryKey: ['company-lookup', lookupId],
    queryFn: () => getCompanyLookup(lookupId!),
    enabled: Boolean(lookupId),
  })

  useEffect(() => {
    if (queue.revision === 0) return
    void client.invalidateQueries({ queryKey: ['searches', session.id] })
    void client.invalidateQueries({ queryKey: ['candidates', session.id] })
    if (selectedRun) {
      void client.invalidateQueries({ queryKey: ['search', selectedRun] })
    }
    if (lookupId) {
      void client.invalidateQueries({ queryKey: ['company-lookup', lookupId] })
    }
  }, [client, lookupId, queue.revision, selectedRun, session.id])

  const search = useMutation({
    mutationFn: runSearch,
    onSuccess: async (result) => {
      setSelectedRun(result.search_run_id)
      await client.invalidateQueries({ queryKey: ['searches', session.id] })
    },
    onError: () => requestAnimationFrame(() => errorRef.current?.focus()),
  })
  const company = useMutation({
    mutationFn: () => startCompanyLookup(session.id, companySlug),
    onSuccess: (result) => setLookupId(result.lookup_id),
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

  function queuePosition(jobId: string): string | null {
    const job = queue.jobs.find((item) => item.id === jobId)
    if (!job) return null
    if (job.state === 'running') return 'Running LinkedIn people search'
    return `Queue position ${job.position ?? '—'} of ${job.depth}`
  }

  return (
    <section aria-labelledby="search-title" className="workspace-page">
      <div className="page-intro">
        <div>
          <p className="eyebrow">Step 2 · Find candidates</p>
          <h1 id="search-title">Build a pool with narrow searches.</h1>
          <p>
            Each run reads one results page. Several focused searches accumulate
            here; there is no hidden pagination.
          </p>
        </div>
        <div className="version-card">
          <strong>{candidates.data?.length ?? 0} discovered</strong>
          <span>Unscored · profiles not retrieved</span>
        </div>
      </div>

      <QueueStatus queue={queue} />

      {!brief ? (
        <div className="empty-card" role="status">
          <h2>Save a valid role brief first.</h2>
          <p>Search uses only the latest saved version, never unsaved edits.</p>
        </div>
      ) : (
        <div className="search-layout">
          <form
            className="search-form panel"
            onSubmit={(event) => {
              event.preventDefault()
              search.mutate({
                session_id: session.id,
                brief_id: brief.id,
                keywords,
                location: location || null,
                network: network.length ? network : null,
                current_company: companyId || null,
              })
            }}
          >
            <div className="section-heading">
              <div>
                <p className="eyebrow">Search parameters</p>
                <h2>Run one focused search</h2>
              </div>
              <span className="saved-brief-chip">Brief v{brief.version}</span>
            </div>
            {(search.isError || company.isError) && (
              <div className="form-error" ref={errorRef} role="alert" tabIndex={-1}>
                <strong>The queued action was not created.</strong>
                <span>{search.error?.message ?? company.error?.message}</span>
              </div>
            )}
            <label className="field field-wide">
              <span>Keywords</span>
              <input
                onChange={(event) => setKeywords(event.target.value)}
                required
                value={keywords}
              />
            </label>
            <label className="field">
              <span>Location</span>
              <input
                onChange={(event) => setLocation(event.target.value)}
                value={location}
              />
            </label>
            <label className="field">
              <span>Current company numeric URN ID</span>
              <input
                inputMode="numeric"
                onChange={(event) => setCompanyId(event.target.value)}
                pattern="[0-9]*"
                placeholder="For example 1115"
                value={companyId}
              />
            </label>
            <fieldset className="network-fieldset">
              <legend>Network distance from your account</legend>
              {NETWORKS.map((option) => (
                <label key={option.value}>
                  <input
                    checked={network.includes(option.value)}
                    onChange={(event) =>
                      setNetwork((current) =>
                        event.target.checked
                          ? [...current, option.value]
                          : current.filter((item) => item !== option.value),
                      )
                    }
                    type="checkbox"
                  />
                  <span>
                    <strong>{option.value}</strong> · {option.label}
                  </span>
                </label>
              ))}
              <p className="field-help">
                F and S are selected by default. Only F is reliably messageable. This
                search context is retained as provenance and never affects a score.
              </p>
            </fieldset>
            <button className="primary-action" disabled={search.isPending} type="submit">
              {search.isPending ? 'Queueing…' : 'Run search'}
            </button>
            {search.data ? (
              <p aria-live="polite" className="queued-confirmation">
                Queued job <code>{search.data.job_id}</code> · run{' '}
                <code>{search.data.search_run_id}</code>
              </p>
            ) : null}
          </form>

          <form
            className="lookup-card panel"
            onSubmit={(event) => {
              event.preventDefault()
              company.mutate()
            }}
          >
            <p className="eyebrow">Optional company lookup</p>
            <h2>Find a company URN</h2>
            <p>
              Enter the slug after /company/. This creates its own serialized read.
            </p>
            <label className="field">
              <span>Company slug</span>
              <input
                onChange={(event) => setCompanySlug(event.target.value)}
                placeholder="microsoft"
                required
                value={companySlug}
              />
            </label>
            <button disabled={company.isPending} type="submit">
              {company.isPending ? 'Queueing…' : 'Look up numeric ID'}
            </button>
            {lookup.data ? (
              <div aria-live="polite" className="lookup-result">
                {lookup.data.candidates.length ? (
                  lookup.data.candidates.map((item) => (
                    <button
                      key={item.urn_id}
                      onClick={() => setCompanyId(item.urn_id)}
                      type="button"
                    >
                      Use {item.urn_id} · {item.text}
                    </button>
                  ))
                ) : (
                  <p>{lookup.data.note ?? `Lookup ${lookup.data.status}.`}</p>
                )}
              </div>
            ) : lookupId ? (
              <p aria-live="polite">Company lookup queued. Waiting for its SSE update.</p>
            ) : null}
          </form>
        </div>
      )}

      <section aria-labelledby="runs-title" className="discovery-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Provenance</p>
            <h2 id="runs-title">Search run history</h2>
          </div>
          <span>{runs.data?.length ?? 0} total runs</span>
        </div>
        {runs.data?.length ? (
          <div className="run-grid">
            {runs.data.map((run) => (
              <button
                className={selectedRun === run.id ? 'run-card selected' : 'run-card'}
                key={run.id}
                onClick={() => setSelectedRun(run.id)}
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
      </section>

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

      <section aria-labelledby="pool-title" className="discovery-section">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Accumulated pool</p>
            <h2 id="pool-title">Discovered people</h2>
          </div>
          <span>Neutral first-seen order</span>
        </div>
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
        {candidates.data?.length ? (
          <div className="candidate-grid">
            {candidates.data.map((candidate) => (
              <article className="candidate-card" key={candidate.id}>
                <div>
                  <span className={`status-label ${candidate.stage}`}>
                    {candidate.stage === 'discovered' ? 'Discovered' : candidate.stage}
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
                    · found in {candidate.source_count} searches
                  </p>
                </div>
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
                    className="primary-action"
                    disabled={
                      enrich.isPending && enrich.variables === candidate.id
                    }
                    onClick={() => enrich.mutate(candidate.id)}
                    type="button"
                  >
                    Retrieve main profile + experience
                  </button>
                ) : (
                  <button
                    className="quiet-action"
                    onClick={() => onCandidateOpen(candidate.id)}
                    type="button"
                  >
                    Review retrieved details
                  </button>
                )}
                <a
                  href={candidate.profile_url}
                  rel="noopener noreferrer"
                  target="_blank"
                >
                  Open {candidate.display_name || candidate.username} on LinkedIn (new tab)
                </a>
                <details>
                  <summary>View {candidate.source_count} source searches</summary>
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
            <h3>No people discovered yet</h3>
            <p>A zero-person run still keeps its raw text and reference breakdown above.</p>
          </div>
        )}
        <div className="phase-gate-card panel">
          <div>
            <p className="eyebrow">Phase Gate A · discovery</p>
            <h3>Confirm extraction and dedupe before ranking</h3>
            <p>
              Inspect names, source searches, and repeated profiles above. Ranking stays
              unavailable until you explicitly accept this pool.
            </p>
          </div>
          {session.phase_gates?.A ? (
            <p className="gate-accepted" role="status">
              ✓ Gate A accepted · ranking unlocked
            </p>
          ) : (
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
                {gateA.isPending ? 'Recording…' : 'Accept Gate A and unlock ranking'}
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
          )}
        </div>
      </section>
    </section>
  )
}
