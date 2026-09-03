import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  enrichCandidate,
  getCandidate,
  getCandidateSection,
  getProfileSections,
} from '../api/client'
import { QueueStatus } from '../components/QueueStatus'
import { RawTextViewer } from '../components/RawTextViewer'
import { SectionAvailabilityMap } from '../components/SectionAvailabilityMap'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

export function CandidateDetailPage({
  candidateId,
  onBack,
  queue,
}: {
  candidateId: string
  onBack: () => void
  queue: ReturnTypeOfJobEvents
}) {
  const queryClient = useQueryClient()
  const enrichmentErrorRef = useRef<HTMLParagraphElement>(null)
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
      await queryClient.invalidateQueries({
        queryKey: ['candidate', candidateId],
      })
    },
    onError: () =>
      requestAnimationFrame(() => enrichmentErrorRef.current?.focus()),
  })

  useEffect(() => {
    if (queue.revision > 0) {
      void queryClient.invalidateQueries({
        queryKey: ['candidate', candidateId],
      })
      void queryClient.invalidateQueries({
        queryKey: ['candidate-section', candidateId],
      })
    }
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
  const promotedSections =
    profileSections.data?.filter((section) => section !== 'experience') ?? []
  return (
    <section aria-labelledby="candidate-title" className="workspace-page">
      <button className="quiet-action back-action" onClick={onBack} type="button">
        ← Back to candidate pool
      </button>
      <div className="page-intro compact-intro">
        <div>
          <p className="eyebrow">Retrieved profile · {candidate.stage}</p>
          <h1 id="candidate-title">
            {candidate.display_name || candidate.username}
          </h1>
          <p>
            Parsed claims stay linked to the exact stored profile text. No score or
            shortlist decision exists in this milestone.
          </p>
        </div>
        <div className="version-card">
          <strong>{candidate.retrieval_status.replaceAll('_', ' ')}</strong>
          <span>{Object.keys(candidate.available_sections).length} sections stored</span>
        </div>
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
