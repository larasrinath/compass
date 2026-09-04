import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  enrichCandidate,
  getCandidate,
  getCandidateSection,
  getProfileSections,
} from '../api/client'
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
  rankingUnlocked,
  sessionId,
  verifiedEvidence,
  onEvidenceVerified,
  onScoreInputsChanged,
}: {
  candidateId: string
  onBack: () => void
  backDestination: 'search' | 'candidates'
  queue: ReturnTypeOfJobEvents
  rankingUnlocked: boolean
  sessionId: string
  verifiedEvidence: ReadonlyMap<string, EvidenceVerification>
  onEvidenceVerified: (
    verification: EvidenceVerification,
    verified: boolean,
  ) => void
  onScoreInputsChanged: () => void
}) {
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
  return (
    <section aria-labelledby="candidate-title" className="workspace-page">
      <button className="quiet-action back-action" onClick={onBack} type="button">
        ← Back to {backDestination}
      </button>
      <div className="page-intro compact-intro">
        <div>
          <p className="eyebrow">Retrieved profile · {candidate.stage}</p>
          <h1 id="candidate-title">
            {candidate.display_name || candidate.username}
          </h1>
          <p>
            Parsed claims and score evidence stay linked to exact stored profile text.
            Opening evidence does not mark it verified.
          </p>
        </div>
        {rankingUnlocked && candidate.score ? (
          <div className="candidate-score-summary">
            <ScoreBadge candidate={candidate.score} />
            <ConfidenceBand candidate={candidate.score} />
            <span className={`stage-badge ${candidate.score.stage}`}>
              {candidate.score.stage === 'provisional' ? '◐ Provisional' : '◆ Enriched'}
            </span>
            <small>
              {Object.keys(candidate.available_sections).length} of 6 sections retrieved · config{' '}
              {candidate.score.weights_version}
            </small>
          </div>
        ) : (
          <div className="version-card">
            <strong>{candidate.retrieval_status.replaceAll('_', ' ')}</strong>
            <span>{Object.keys(candidate.available_sections).length} sections stored</span>
          </div>
        )}
      </div>

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

      <SectionAvailabilityMap available={candidate.available_sections} />

      {rankingUnlocked && (candidate.score || candidate.signals) ? (
        <EvidencePanel
          allInert={candidate.score?.all_inert_attested ?? false}
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

      {rankingUnlocked && candidate.score_history?.length ? (
        <section className="panel score-history" aria-labelledby="score-history-title">
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
        </section>
      ) : null}

      {contextHints.length ? (
        <section className="panel context-panel" aria-labelledby="context-title">
          <p className="eyebrow">Context only</p>
          <h2 id="context-title">Search and routing hints</h2>
          <p>These details are displayed for workflow context and carry zero scoring weight.</p>
          <dl>
            {contextHints.map((hint, index) => (
              <div key={`${hint.kind}-${index}`}>
                <dt>{hint.label}</dt><dd>{hint.value} · non-scoring</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      <section className="panel promotion-panel" aria-labelledby="promotion-title">
        <div>
          <p className="eyebrow">
            {candidate.stage === 'discovered'
              ? 'Stage 1 · explicit retry'
              : 'Stage 2 · explicit action'}
          </p>
          <h2 id="promotion-title">
            {candidate.stage === 'discovered'
              ? 'Retry main profile and experience'
              : 'Retrieve up to three more sections'}
          </h2>
          {candidate.stage !== 'discovered' ? (
            <p>
              Main profile is implicit, so three promoted sections keep this call to
              four navigations.
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
      </section>

      <section className="panel" aria-labelledby="stored-sections-title">
        <p className="eyebrow">Stored source sections</p>
        <h2 id="stored-sections-title">Open any retrieved raw section</h2>
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
          <p className="eyebrow">Deterministic parser</p>
          <h2 id="parsed-title">Parsed fields</h2>
          {candidate.fields.length ? (
            <ol className="parsed-fields">
              {candidate.fields.map((field) => (
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
                  <small>{field.field_key}</small>
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
    </section>
  )
}
