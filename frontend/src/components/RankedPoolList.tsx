import type { CandidateRecord, RankedCandidateRecord } from '../api/client'
import { CompassIcon } from './CompassIcon'

function rankPool(candidates: CandidateRecord[], scores: RankedCandidateRecord[]) {
  const pool = new Map(candidates.map(candidate => [candidate.id, candidate]))
  const rows: { candidate: CandidateRecord; score: RankedCandidateRecord | null; rank: number | null }[] = []
  // Preserve the API's score-descending order, including its deterministic tie order.
  for (const score of scores) {
    const candidate = pool.get(score.id)
    if (!candidate || score.score === null || score.calculation_status !== 'scored') continue
    rows.push({ candidate, score, rank: rows.length + 1 })
    pool.delete(score.id)
  }
  for (const candidate of pool.values()) rows.push({ candidate, score: null, rank: null })
  return rows
}

export function RankedPoolList({ candidates, scores, nameFilter, onOpen, onSave, downloadsBlocked, savingId }: {
  candidates: CandidateRecord[]
  scores: RankedCandidateRecord[]
  nameFilter: string
  onOpen: (id: string) => void
  onSave: (id: string) => void
  downloadsBlocked: boolean
  savingId?: string
}) {
  const rows = rankPool(candidates, scores).filter(({ candidate }) =>
    `${candidate.display_name ?? ''} ${candidate.username}`.toLowerCase().includes(nameFilter.toLowerCase()),
  )
  return <div className="ranked-pool-list">
    <table aria-label="Candidates ranked by score">
      <thead><tr><th scope="col">Rank</th><th scope="col">Candidate</th><th scope="col">Score</th><th scope="col">Confidence</th><th scope="col"><span className="sr-only">Action</span></th></tr></thead>
      <tbody>{rows.map(({ candidate, score, rank }) => {
        const name = candidate.display_name || candidate.username
        return <tr key={candidate.id}>
          <td className="pool-rank">{rank ?? <span aria-label="Unranked">—</span>}</td>
          <td className="pool-person"><p>{name}</p>{score?.headline ? <span>{score.headline}</span> : null}<small>{candidate.active_job_id ? 'Retrieval queued' : candidate.retrieval_status === 'failed' ? 'Retrieval failed' : candidate.stage === 'discovered' ? 'Awaiting download' : 'Profile saved'} · {candidate.source_count} {candidate.source_count === 1 ? 'search' : 'searches'}</small></td>
          <td className="pool-score" data-label="Score">{score ? <><strong>{score.score!.toFixed(1)}</strong><small>out of 100</small></> : <span className="pool-unscored">Not scored</span>}</td>
          <td className="pool-confidence" data-label="Confidence">{score ? <><span className={`pool-confidence-band ${score.confidence_band ?? 'unavailable'}`}>{Math.round(score.confidence * 100)}%</span><small>{score.confidence_band ? `${score.confidence_band[0].toUpperCase()}${score.confidence_band.slice(1)} confidence` : 'Evidence availability'}</small></> : <span className="pool-unscored">—</span>}</td>
          <td className="pool-row-action">{candidate.active_job_id ? <span className="pool-unscored">Queued</span> : candidate.stage === 'discovered' && candidate.retrieval_status !== 'failed' ? <button className="quiet-action" type="button" disabled={downloadsBlocked || savingId === candidate.id} onClick={() => onSave(candidate.id)} aria-label={`Download profile for ${name}`}>{savingId === candidate.id ? 'Queueing…' : 'Download profile'}</button> : <button className="result-review" type="button" onClick={() => onOpen(candidate.id)} aria-label={`Review ${name}`}>Review <CompassIcon name="arrow" size={16} /></button>}</td>
        </tr>
      })}</tbody>
    </table>
    <p className="ranked-pool-note">Highest score first. Unscored profiles appear last. Confidence reflects available evidence.</p>
  </div>
}
