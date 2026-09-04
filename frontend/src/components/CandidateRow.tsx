import type { RankedCandidateRecord } from '../api/client'
import { ConfidenceBand } from './ConfidenceBand'
import { ScoreBadge } from './ScoreBadge'

export function CandidateRow({
  candidate,
  onOpen,
}: {
  candidate: RankedCandidateRecord
  onOpen: (candidateId: string) => void
}) {
  const delta = candidate.delta
  return (
    <article className="ranked-candidate">
      <div className="ranked-position" aria-hidden="true">
        {candidate.score === null ? '—' : candidate.score >= 80 ? 'A' : candidate.score >= 60 ? 'B' : 'C'}
      </div>
      <div className="ranked-identity">
        <div className="ranked-badges">
          <span className={`stage-badge ${candidate.stage}`}>
            {candidate.stage === 'provisional' ? '◐ Provisional' : '◆ Enriched'}
          </span>
          <ConfidenceBand candidate={candidate} />
        </div>
        <h3>{candidate.display_name || candidate.username}</h3>
        <p>
          Config {candidate.weights_version} · {candidate.active_signal_count} active signals
          {candidate.previous_score === null
            ? ' · no prior score'
            : ` · previous ${candidate.previous_score.toFixed(1)} · change ${delta !== null && delta >= 0 ? '+' : ''}${delta?.toFixed(1) ?? '—'}`}
        </p>
        {candidate.top_signals.length ? (
          <ul className="top-signals" aria-label="Top scoring signals">
            {candidate.top_signals.map((signal) => (
              <li key={signal.signal_id}>
                {signal.label} <strong>+{signal.contribution.toFixed(1)}</strong>
              </li>
            ))}
          </ul>
        ) : (
          <p className="no-signals">No top signals</p>
        )}
        {candidate.non_scoring_hints.length ? (
          <div className="non-scoring-hints" aria-label="Non-scoring context">
            {candidate.non_scoring_hints.map((hint, index) => (
              <span key={`${hint.kind}-${index}`}>
                {hint.label}: {hint.value} · non-scoring
              </span>
            ))}
          </div>
        ) : null}
      </div>
      <ScoreBadge candidate={candidate} />
      <button className="quiet-action" onClick={() => onOpen(candidate.id)} type="button">
        Open evidence for {candidate.display_name || candidate.username}
      </button>
    </article>
  )
}
