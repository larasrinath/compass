import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  enrichCandidate,
  getCandidate,
  getCandidateSection,
  getProfileSections,
} from '../api/client'
import { CandidateOverview } from '../components/CandidateOverview'
import { QueueStatus } from '../components/QueueStatus'
import { ConfidenceBand } from '../components/ConfidenceBand'
import { EvidencePanel } from '../components/EvidencePanel'
import { RawTextViewer } from '../components/RawTextViewer'
import { ScoreBadge } from '../components/ScoreBadge'
import { SectionAvailabilityMap } from '../components/SectionAvailabilityMap'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'
import type { EvidenceVerification } from '../scoreVerification'

export function CandidateDetailPage({
  candidateId,
  onBack,
  backDestination,
  queue,
  retrievalReady = true,
  rankingUnlocked,
  sessionId,
  verifiedEvidence,
  onEvidenceVerified,
  onScoreInputsChanged,
  onCompare,
  comparing,
  comparisonFull,
}: {
  onCompare?: () => void
  comparing?: boolean
  comparisonFull?: boolean
  candidateId: string
  onBack: () => void
  backDestination: 'search' | 'candidates' | 'saved searches'
  queue: ReturnTypeOfJobEvents
  retrievalReady?: boolean
  rankingUnlocked: boolean
  sessionId: string
  verifiedEvidence: ReadonlyMap<string, EvidenceVerification>
  onEvidenceVerified: (
    verification: EvidenceVerification,
    verified: boolean,
  ) => void
  onScoreInputsChanged: () => void
}) {
  const diagnosticsRef = useRef<HTMLDetailsElement>(null)
  const sourceRef = useRef<HTMLElement>(null)
  const queryClient = useQueryClient()
  const enrichmentErrorRef = useRef<HTMLParagraphElement>(null)
  const lastQueueRevision = useRef(queue.revision)
  const [selected, setSelected] = useState<string[]>([
    'education',
    'skills',
    'projects',
  ])
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null)
  const [selectedSectionName, setSelectedSectionName] = useState<string | null>(
    null,
  )
  const detail = useQuery({
    queryKey: ['candidate', candidateId],
    queryFn: () => getCandidate(candidateId),
  })
  const profileSections = useQuery({
    queryKey: ['profile-sections'],
    queryFn: getProfileSections,
    staleTime: Infinity,
  })
  const effectiveSelectedSection =
    selectedSectionName ??
    (detail.data ? Object.keys(detail.data.available_sections)[0] : null) ??
    null
  const rawSection = useQuery({
    queryKey: ['candidate-section', candidateId, effectiveSelectedSection],
    queryFn: () => getCandidateSection(candidateId, effectiveSelectedSection!),
    enabled: Boolean(effectiveSelectedSection),
  })
  const enrich = useMutation({
    mutationFn: (sections: string[]) => enrichCandidate(candidateId, sections),
    onSuccess: async () => {
      onScoreInputsChanged()
      await queryClient.invalidateQueries({
        queryKey: ['candidate', candidateId],
      })
    },
    onError: () =>
      requestAnimationFrame(() => enrichmentErrorRef.current?.focus()),
  })

  useEffect(() => {
    const previous = lastQueueRevision.current
    lastQueueRevision.current = queue.revision
    if (queue.revision <= previous) return
    void queryClient.invalidateQueries({
      queryKey: ['candidate', candidateId],
    })
    void queryClient.invalidateQueries({
      queryKey: ['candidate-section', candidateId],
    })
  }, [candidateId, queryClient, queue.revision])

  if (detail.isPending) return <p aria-live="polite">Opening stored profile…</p>
  if (detail.isError || !detail.data) {
    return (
      <div className="form-error" role="alert">
        Could not open this stored candidate.
      </div>
    )
  }
  const candidate = detail.data
  const currentScoreIdentity =
    rankingUnlocked && candidate.score
      ? {
          sessionId,
          scoreId: candidate.score.score_id,
          inputFingerprint: candidate.score.input_fingerprint,
        }
      : null
  const verifiedEvidenceIds = new Set(
    [...verifiedEvidence].flatMap(([evidenceId, verification]) =>
      currentScoreIdentity &&
      verification.sessionId === currentScoreIdentity.sessionId &&
      verification.scoreId === currentScoreIdentity.scoreId &&
      verification.inputFingerprint === currentScoreIdentity.inputFingerprint
        ? [evidenceId]
        : [],
    ),
  )
  const contextHints = [...(candidate.non_scoring_hints ?? [])]
  if (
    candidate.profile_urn &&
    !contextHints.some((hint) => hint.kind === 'profile_urn')
  ) {
    contextHints.push({
      kind: 'profile_urn',
      label: 'Profile identifier',
      value: candidate.profile_urn,
    })
  }
  const promotedSections =
    profileSections.data?.filter((section) => section !== 'experience') ?? []
  const hasScoreSignals = Boolean(candidate.signals?.length)
  const allInert = candidate.score?.all_inert_attested ?? false
  const scoringEmptyState =
    rankingUnlocked && !candidate.score && !hasScoreSignals
      ? candidate.scoring_empty_state
      : null
  return (
    <section aria-labelledby="candidate-title" className="candidate-profile">
      <CandidateOverview candidate={candidate} rankingUnlocked={rankingUnlocked} onCompare={onCompare} comparing={comparing} comparisonFull={comparisonFull} onSourceOpen={(section, fieldId) => {
        setSelectedSectionName(section)
        setSelectedFieldId(fieldId ?? null)
        if (diagnosticsRef.current) diagnosticsRef.current.open = true
        requestAnimationFrame(() => { sourceRef.current?.scrollIntoView({ block: 'start' }); sourceRef.current?.focus() })
      }} />
      <div className="profile-secondary">
      <QueueStatus queue={queue} />
      {candidate.errors.map((error, index) => (
        <div
          className="inline-error"
          key={`${error.section_name}-${index}`}
          role="alert"
        >
          <strong>{error.section_name.replaceAll('_', ' ')}</strong>
          <span>{error.error_message}</span>
        </div>
      ))}
      {candidate.profile_contract_error ? (
        <div className="inline-error" role="alert">
          <strong>Profile contract conflict</strong>
          <span>
            Retrieved identity or response data was quarantined and was not trusted.
          </span>
        </div>
      ) : null}
      {candidate.profile_urn_quarantined ? (
        <p className="cap-notice" role="status">
          Profile identifier routing is disabled while this identity conflict remains
          quarantined.
        </p>
      ) : null}

      <details className="panel promotion-panel" open={candidate.stage === 'discovered' ? true : undefined}>
        <summary>Download more profile information</summary>
        <div>
          <p className="eyebrow">
            {candidate.stage === 'discovered'
              ? 'Stage 1 · explicit retry'
              : 'Additional evidence'}
          </p>
          <h2 id="promotion-title">
            {candidate.stage === 'discovered'
              ? 'Retry main profile and experience'
              : 'Retrieve up to three more sections'}
          </h2>
          {candidate.stage !== 'discovered' ? (
            <p>
              Choose up to three sections to add more evidence to this profile.
            </p>
          ) : null}
        </div>
        {candidate.stage !== 'discovered' ? <fieldset>
          <legend>Promoted sections</legend>
          {promotedSections.map((section) => (
            <label key={section}>
              <input
                checked={selected.includes(section)}
                disabled={!selected.includes(section) && selected.length >= 3}
                onChange={(event) =>
                  setSelected((current) =>
                    event.target.checked
                      ? [...current, section]
                      : current.filter((item) => item !== section),
                  )
                }
                type="checkbox"
              />
              {section}
            </label>
          ))}
          {profileSections.isError ? (
            <span className="field-error" role="alert">
              Available profile sections could not be loaded.
            </span>
          ) : null}
        </fieldset> : null}
        <button
          className="primary-action"
          disabled={
            (candidate.stage !== 'discovered' && selected.length === 0) ||
            !retrievalReady ||
            enrich.isPending ||
            Boolean(candidate.active_job_id)
          }
          onClick={() =>
            enrich.mutate(
              candidate.stage === 'discovered' ? ['experience'] : selected,
            )
          }
          type="button"
        >
          {enrich.isPending
            ? 'Queueing…'
            : candidate.stage === 'discovered'
              ? 'Retry main profile + experience'
              : 'Retrieve selected sections'}
        </button>
        {enrich.isError ? (
          <p
            className="field-error"
            ref={enrichmentErrorRef}
            role="alert"
            tabIndex={-1}
          >
            {enrich.error.message}
          </p>
        ) : null}
      </details>

      <details className="profile-diagnostics" ref={diagnosticsRef}>
        <summary>Scoring & source details</summary>
        <div className="profile-diagnostic-content">
        <p className="profile-muted">Opening evidence does not mark it verified. Check the original text before confirming a source.</p>
        {rankingUnlocked && candidate.score ? <div className="candidate-score-summary">
          <ScoreBadge candidate={candidate.score} /><ConfidenceBand candidate={candidate.score} />
        </div> : null}
      <details className="simple-options"><summary>Downloaded sections <span>{Object.keys(candidate.available_sections).length} saved</span></summary><SectionAvailabilityMap available={candidate.available_sections} /></details>

      {rankingUnlocked && (hasScoreSignals || allInert) ? (
        <EvidencePanel
          allInert={allInert}
          onEvidenceOpen={(sectionName, evidenceId) => {
            setSelectedSectionName(sectionName)
            setSelectedFieldId(evidenceId)
          }}
          onEvidenceVerified={(evidenceId, verified) => {
            if (currentScoreIdentity) {
              onEvidenceVerified(
                { evidenceId, ...currentScoreIdentity },
                verified,
              )
            }
          }}
          signals={candidate.signals ?? []}
          verifiedEvidenceIds={verifiedEvidenceIds}
        />
      ) : null}

      {scoringEmptyState ? (
        <section
          aria-labelledby="scoring-empty-title"
          className="panel scoring-empty-state"
          role="status"
        >
          <p className="eyebrow">Scoring</p>
          <h2 id="scoring-empty-title">Score unavailable</h2>
          <p>{scoringEmptyState}</p>
        </section>
      ) : null}

      {rankingUnlocked && candidate.score_history?.length ? (
        <details className="panel score-history simple-options"><summary>Previous scores</summary>
          <p className="eyebrow">Immutable history</p>
          <h2 id="score-history-title">Current and previous scores</h2>
          <ol>
            {candidate.score_history.map((score) => (
              <li key={score.id}>
                <strong>{score.score === null ? 'Not scored' : score.score.toFixed(1)}</strong>
                <span>{score.current ? 'Current' : 'Previous'} · config {score.weights_version} · {new Date(score.computed_at).toLocaleString()}</span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      {contextHints.length ? (
        <details className="panel context-panel">
          <summary>Search details</summary>
          <p className="eyebrow">Context only</p>
          <h2>Search and routing hints</h2>
          <p>These details are displayed for workflow context and carry zero scoring weight.</p>
          <dl>
            {contextHints.map((hint, index) => (
              <div key={`${hint.kind}-${index}`}>
                <dt>{hint.label}</dt><dd>{hint.value} · non-scoring</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}

      <section className="panel" aria-labelledby="stored-sections-title" ref={sourceRef} tabIndex={-1}>
        <p className="eyebrow">Stored source sections</p>
        <h2 id="stored-sections-title">Review saved profile information</h2>
        {Object.keys(candidate.available_sections).length ? (
          <div
            aria-label="Stored profile sections"
            className="section-selector"
            role="group"
          >
            {Object.keys(candidate.available_sections).map((sectionName) => (
              <button
                aria-pressed={effectiveSelectedSection === sectionName}
                key={sectionName}
                onClick={() => {
                  setSelectedSectionName(sectionName)
                  setSelectedFieldId(null)
                }}
                type="button"
              >
                {sectionName.replaceAll('_', ' ')}
              </button>
            ))}
          </div>
        ) : (
          <p>No trusted profile section was stored for this attempt.</p>
        )}
      </section>

      <section aria-labelledby="parsed-title" className="detail-columns">
        <div className="panel parsed-panel">
          <p className="eyebrow">Extracted from this section</p>
          <h2 id="parsed-title">Profile details</h2>
          {candidate.fields.some((field) => field.section_name === effectiveSelectedSection) ? (
            <ol className="parsed-fields">
              {candidate.fields.filter((field) => field.section_name === effectiveSelectedSection).map((field) => (
                <li key={field.id}>
                  <button
                    aria-pressed={selectedFieldId === field.id}
                    className="parsed-field-action"
                    onClick={() => {
                      setSelectedFieldId(field.id)
                      setSelectedSectionName(field.section_name)
                    }}
                    type="button"
                  >
                  <small>{field.field_key.replace(/\.\d+\.?/g, ' · ').replaceAll('_', ' ')}</small>
                  <strong>{field.value ?? field.provenance_label}</strong>
                  {field.provenance_available ? (
                    <span>
                      {field.section_name.replaceAll('_', ' ')} · exact stored span{' '}
                      {field.span_start}:{field.span_end}
                    </span>
                  ) : (
                    <span>Source offsets withheld</span>
                  )}
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p>No deterministic fields were found in retrieved text.</p>
          )}
        </div>
        <div className="panel raw-placeholder">
          <p className="eyebrow">Stored source</p>
          <h2>Raw profile text</h2>
          {rawSection.data ? (
            <RawTextViewer
              section={rawSection.data}
              selectedFieldId={selectedFieldId}
            />
          ) : rawSection.isError ? (
            <p role="alert">The stored source section could not be opened.</p>
          ) : (
            <p>Select a stored section or parsed field to inspect its source text.</p>
          )}
        </div>
      </section>
        <button className="profile-text-action" onClick={onBack} type="button">Back to {backDestination}</button>
        </div>
      </details>
      </div>
    </section>
  )
}
