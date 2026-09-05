import type { BriefRecord, RankedCandidateRecord } from '../api/client'
import { ConfidenceBand } from './ConfidenceBand'
import { ScoreBadge } from './ScoreBadge'
import { CompassIcon } from './CompassIcon'

export function CandidateRow({ candidate, onOpen, selected = false, comparisonDisabled = false, onCompare, brief }: {
  brief?: BriefRecord | null
  candidate: RankedCandidateRecord
  onOpen: (candidateId: string) => void
  selected?: boolean
  comparisonDisabled?: boolean
  onCompare?: (selected: boolean) => void
}) {
  const name = candidate.display_name || candidate.username
  const delta = candidate.delta
  const found = candidate.top_signals.filter(signal => signal.contribution > 0 && (signal.rollup === 'matched' || signal.rollup === 'mixed')).slice(0, 3)
  const checks = candidate.top_signals.filter(signal => signal.rollup !== 'matched').slice(0, 2)
  const matchLabel = (signal: RankedCandidateRecord['top_signals'][number]) => {
    if (!brief || signal.rollup !== 'matched') return signal.label
    const terms = signal.signal_id === 'S-1' ? brief.required_skills : signal.signal_id === 'S-2' ? brief.optional_skills : signal.signal_id === 'S-8' ? brief.required_credentials : []
    return terms.length ? terms.map(term => term.term).join(' · ') : signal.label
  }
  return (
    <article className={`result-person${selected ? ' is-selected' : ''}`}>
      <div className="result-person-header">
        <div className="result-initials" aria-hidden="true">{name.split(/\s+/).slice(0, 2).map(part => part[0]).join('')}</div>
        <div className="result-person-identity">
          <h3>{name}</h3>
          <p>{candidate.headline || 'Headline not found in the retrieved data'}</p>
          <span className="result-save-state">{candidate.score === null ? 'Evidence not yet available' : candidate.stage === 'enriched' ? 'Profile and extra sections saved' : 'Profile partially retrieved'}</span>
        </div>
      </div>
      <div className="result-evidence">
        <h4>Evidence found</h4>
        {found.length ? <ul>{found.map(signal => <li key={signal.signal_id}><span className="evidence-dot" aria-hidden="true" /><span className="result-signal-label">{matchLabel(signal)}{signal.rollup === 'mixed' ? ' · partial match' : ''}</span></li>)}</ul> : <p>{candidate.all_inert_attested ? 'Add role criteria to check this profile.' : 'No matching evidence available yet.'}</p>}
      </div>
      <div className="result-checks">
        <h4>Worth checking</h4>
        {checks.length ? <ul>{checks.map(signal => <li key={signal.signal_id}>{signal.label}: {signal.rollup === 'unknown' ? 'not checked yet.' : signal.rollup === 'not_matched' ? 'no exact match in saved text.' : signal.rollup === 'contradicted' ? 'conflicting evidence.' : 'only some criteria matched.'}</li>)}</ul> : <p>{candidate.confidence < 1 ? 'Some profile evidence is still missing.' : 'Confirm claims and dates in the source profile.'}</p>}
      </div>
      <details className="result-scoring">
        <summary>Scoring details</summary>
        <p>{candidate.stage === 'provisional' ? 'Provisional · partial retrieval' : 'Enriched · extra sections retrieved'}</p>
        <div className="result-scoring-summary"><ScoreBadge candidate={candidate} /><ConfidenceBand candidate={candidate} /></div>
        <p>{candidate.calculation_status === 'scored' ? 'Scored' : 'Not calculated'} · config {candidate.weights_version} · {candidate.active_signal_count} active signals
          {candidate.previous_score === null ? ' · no prior score' : ` · previous ${candidate.previous_score.toFixed(1)} · change ${delta !== null && delta >= 0 ? '+' : ''}${delta?.toFixed(1) ?? '—'}`}</p>
        {candidate.top_signals.length ? <ul className="top-signals" aria-label="Top scoring signals">{candidate.top_signals.slice(0, 3).map(signal => <li key={signal.signal_id}>{signal.label} <strong>+{signal.contribution.toFixed(1)}</strong></li>)}</ul> : <p>No top signals</p>}
        {candidate.non_scoring_hints.length ? <details className="non-scoring-hints"><summary>Search context</summary>{candidate.non_scoring_hints.map((hint, index) => <span key={`${hint.kind}-${index}`}>{hint.label}: {hint.value} · non-scoring</span>)}</details> : null}
      </details>
      <div className="result-person-footer">
        <button aria-label={`Open evidence for ${name}`} className="result-review" onClick={() => onOpen(candidate.id)} type="button">Review <CompassIcon name="arrow" size={18} /></button>
        {onCompare ? <label className={`result-compare${comparisonDisabled ? ' is-disabled' : ''}`}>
          <input aria-label={`Compare ${name}`} type="checkbox" checked={selected} disabled={comparisonDisabled} onChange={event => onCompare(event.target.checked)} />
          <CompassIcon name="compare" size={16} /><span>{selected ? 'Selected' : 'Compare'}</span>
        </label> : null}
      </div>
    </article>
  )
}
