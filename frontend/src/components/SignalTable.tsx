import type { ScoreSignalRecord } from '../api/client'
import { VerdictBadge } from './VerdictBadge'

export function SignalTable({ signals }: { signals: ScoreSignalRecord[] }) {
  return (
    <div className="signal-table-wrap" role="region" aria-label="Scoring signal comparison" tabIndex={0}>
      <table className="signal-table">
        <caption>How retrieved evidence contributes to this score</caption>
        <thead>
          <tr>
            <th scope="col">Signal</th>
            <th scope="col">Result</th>
            <th scope="col">Weight</th>
            <th scope="col">Contribution</th>
            <th scope="col">Availability</th>
          </tr>
        </thead>
        <tbody>
          {signals.map((signal) => (
            <tr key={signal.id}>
              <th scope="row">{signal.signal_id} · {signal.label}</th>
              <td><VerdictBadge verdict={signal.rollup} /></td>
              <td>{signal.weight}</td>
              <td>{signal.contribution.toFixed(1)}</td>
              <td>{Math.round(signal.availability * 100)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
