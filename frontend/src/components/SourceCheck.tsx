import { useQuery } from '@tanstack/react-query'
import { getCandidateSection, type ProfileEvidenceRecord } from '../api/client'
import { RawTextViewer } from './RawTextViewer'

/** A check belongs to the exact stored passage, never just to the preview snippet. */
export function SourceCheck({ candidateId, evidence, label, checked, onChange }: {
  candidateId: string
  evidence: ProfileEvidenceRecord
  label: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const source = useQuery({
    queryKey: ['candidate-section', candidateId, evidence.section_name],
    queryFn: () => getCandidateSection(candidateId, evidence.section_name),
  })
  const span = source.data?.spans.find(item => item.id === evidence.id)
  const canCheck = source.isSuccess && !source.isFetching &&
    source.data.candidate_id === candidateId &&
    source.data.profile_section_id === evidence.profile_section_id &&
    span?.profile_section_id === evidence.profile_section_id &&
    span.provenance_available && span.span_start === evidence.span_start &&
    span.span_end === evidence.span_end && evidence.span_start >= 0 &&
    evidence.span_end > evidence.span_start &&
    evidence.span_end <= Array.from(source.data.raw_text).length

  return <div className="source-check">
    <p className="source-check-prompt">Read the highlighted passage in context. Does it support the displayed result?</p>
    {source.isPending ? <p role="status">Opening saved source…</p> : source.isError ?
      <p role="alert">Could not open this source. <button className="profile-text-action" type="button" onClick={() => void source.refetch()}>Try again</button></p> :
      canCheck ? <RawTextViewer section={source.data} selectedFieldId={evidence.id} /> :
      <p role="status">This passage could not be linked to the current saved source. Reopen the profile before checking it.</p>}
    <label className="verify-control">
      <input type="checkbox" aria-label={label} checked={checked} disabled={!canCheck}
        onChange={event => onChange(event.target.checked)} />
      I checked this passage against the criterion
    </label>
    <p className="profile-muted">{checked ? 'Source checked for this score. The match score is unchanged.' : 'Leave unchecked if the passage is unclear or does not support the criterion.'}</p>
  </div>
}
