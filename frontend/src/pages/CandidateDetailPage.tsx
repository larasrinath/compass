import { useEffect, useState } from 'react'
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
  const [selected, setSelected] = useState<string[]>([
    'education',
    'skills',
    'projects',
  ])
  const [selectedFieldId, setSelectedFieldId] = useState<string | null>(null)
  const detail = useQuery({
    queryKey: ['candidate', candidateId],
    queryFn: () => getCandidate(candidateId),
  })
  const profileSections = useQuery({
    queryKey: ['profile-sections'],
    queryFn: getProfileSections,
    staleTime: Infinity,
  })
  const selectedField = detail.data?.fields.find(
    (field) => field.id === selectedFieldId,
  )
  const rawSection = useQuery({
    queryKey: ['candidate-section', candidateId, selectedField?.section_name],
    queryFn: () => getCandidateSection(candidateId, selectedField!.section_name),
    enabled: Boolean(selectedField),
  })
  const enrich = useMutation({
    mutationFn: (sections: string[]) => enrichCandidate(candidateId, sections),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ['candidate', candidateId],
      })
    },
  })

  useEffect(() => {
    if (queue.revision > 0) {
      void queryClient.invalidateQueries({
        queryKey: ['candidate', candidateId],
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

      <SectionAvailabilityMap available={candidate.available_sections} />

      <section className="panel promotion-panel" aria-labelledby="promotion-title">
        <div>
          <p className="eyebrow">Stage 2 · explicit action</p>
          <h2 id="promotion-title">Retrieve up to three more sections</h2>
          <p>
            Main profile is implicit, so three promoted sections keep this call to
            four navigations.
          </p>
        </div>
        <fieldset>
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
        </fieldset>
        <button
          className="primary-action"
          disabled={
            selected.length === 0 ||
            enrich.isPending ||
            Boolean(candidate.active_job_id)
          }
          onClick={() => enrich.mutate(selected)}
          type="button"
        >
          {enrich.isPending ? 'Queueing…' : 'Retrieve selected sections'}
        </button>
        {enrich.isError ? (
          <p className="field-error" role="alert">
            {enrich.error.message}
          </p>
        ) : null}
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
                    onClick={() => setSelectedFieldId(field.id)}
                    type="button"
                  >
                  <small>{field.field_key}</small>
                  <strong>{field.value}</strong>
                  <span>
                    {field.section_name.replaceAll('_', ' ')} · exact stored span{' '}
                    {field.span_start}:{field.span_end}
                  </span>
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
            <p>Select a parsed field to inspect its exact source span.</p>
          )}
        </div>
      </section>
    </section>
  )
}
