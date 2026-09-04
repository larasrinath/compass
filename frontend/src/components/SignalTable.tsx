import type { ScoreSignalRecord } from '../api/client'
import { verdictCopy } from './scoringCopy'

export function SignalTable({ signals }: { signals: ScoreSignalRecord[] }) {
  return (
    <div className="signal-table-wrap">
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
              <td><span className={`verdict-symbol ${signal.rollup}`}>{rollupSymbol(signal.rollup)} {verdictCopy(signal.rollup)}</span></td>
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

function rollupSymbol(rollup: ScoreSignalRecord['rollup']) {
  if (rollup === 'matched') return '✓'
  if (rollup === 'contradicted') return '!'
  if (rollup === 'not_matched') return '○'
  if (rollup === 'unknown') return '?'
  return '±'
}
