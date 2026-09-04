import type { RankedCandidateRecord } from '../api/client'
import { UNKNOWN_COPY } from './scoringCopy'

const NO_CRITERIA = 'Not scored — no active scoring criteria'

export function ScoreBadge({ candidate }: { candidate: RankedCandidateRecord }) {
  if (candidate.all_inert_attested) {
    return (
      <div className="score-badge score-unknown">
        <strong>—</strong>
        <span>{NO_CRITERIA}</span>
      </div>
    )
  }
  if (candidate.score === null) {
    return (
      <div className="score-badge score-unknown">
        <strong>?</strong>
        <span>{UNKNOWN_COPY} · active criteria lack evidence</span>
      </div>
    )
  }
  return (
    <div className="score-badge">
      <strong>{candidate.score.toFixed(1)}</strong>
      <span>
        Range {candidate.score_lower?.toFixed(1)}–{candidate.score_upper?.toFixed(1)}
      </span>
    </div>
  )
}

export { NO_CRITERIA }
