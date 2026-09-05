import type { RankedCandidateRecord } from '../api/client'

export function ConfidenceBand({ candidate }: { candidate: RankedCandidateRecord }) {
  const percentage = Math.round(candidate.confidence * 100)
  if (candidate.all_inert_attested) {
    return <span className="confidence-band low">Low confidence (0%)</span>
  }
  if (candidate.confidence_band === null) {
    return <span className="confidence-band unavailable">No confidence band · 0%</span>
  }
  return (
    <span className={`confidence-band ${candidate.confidence_band}`}>
      {candidate.confidence_band} confidence · {percentage}%
    </span>
  )
}
