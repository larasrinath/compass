import type { CandidateSection } from '../api/client'

export function RawTextViewer({
  section,
  selectedFieldId,
}: {
  section: CandidateSection
  selectedFieldId: string | null
}) {
  const selected = section.spans.find((span) => span.id === selectedFieldId)
  if (selected && !selected.provenance_available) {
    return (
      <div className="provenance-withheld" role="status">
        <strong>Provenance withheld</strong>
        <p>
          This source run overlaps private diagnostic material, so its content and
          highlight are not exposed.
        </p>
      </div>
    )
  }
  const points = Array.from(section.raw_text)
  const start = selected?.span_start
  const end = selected?.span_end
  const hasHighlight =
    start !== null &&
    start !== undefined &&
    end !== null &&
    end !== undefined &&
    start >= 0 &&
    end > start &&
    end <= points.length
  return (
    <pre aria-label={`Raw ${section.section_name.replaceAll('_', ' ')} profile text`}>
      {hasHighlight ? (
        <>
          {points.slice(0, start).join('')}
          <mark>{points.slice(start, end).join('')}</mark>
          {points.slice(end).join('')}
        </>
      ) : (
        section.raw_text
      )}
    </pre>
  )
}
