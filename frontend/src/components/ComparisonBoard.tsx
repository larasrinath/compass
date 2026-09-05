import { useQueries } from '@tanstack/react-query'
import { getCandidate, type RankedCandidateRecord } from '../api/client'

const verdictLabels = {
  matched: 'Evidence found',
  not_matched: 'No exact match',
  unknown: 'Not checked',
  contradicted: 'Conflicting evidence',
} as const

export function ComparisonBoard({ candidates, onRemove, onOpen }: {
  candidates: RankedCandidateRecord[]
  onRemove: (id: string) => void
  onOpen: (id: string) => void
}) {
  const profiles = useQueries({ queries: candidates.map(candidate => ({
    queryKey: ['candidate', candidate.id],
    queryFn: () => getCandidate(candidate.id),
  })) })
  const rows = new Map<string, { label: string; signal: string }>()
  for (const profile of profiles) {
    for (const signal of profile.data?.signals ?? []) {
      for (const claim of signal.claims) {
        rows.set(`${signal.signal_id}:${claim.claim_key}`, { label: claim.display_term, signal: signal.label })
      }
    }
  }
  return <section id="comparison-board" className="comparison-board" aria-label="Side-by-side comparison">
    <div className="section-heading"><div><h2>Compare the evidence</h2><p>The same criteria, side by side. Open a profile to check the source.</p></div></div>
    {profiles.map((profile, index) => profile.isError ? <p role="alert" key={candidates[index].id}>Could not load evidence for {candidates[index].display_name || candidates[index].username}. <button className="text-action" onClick={() => void profile.refetch()} type="button">Retry</button></p> : null)}
    <div className="comparison-scroll" tabIndex={0} role="region" aria-label="Candidate criteria table">
      <table className="comparison-table">
        <thead><tr><th scope="col">Criterion</th>{candidates.map(candidate => <th scope="col" key={candidate.id}>
          <button className="comparison-remove" onClick={() => onRemove(candidate.id)} aria-label={`Remove ${candidate.display_name || candidate.username} from comparison`} type="button">×</button>
          <strong>{candidate.display_name || candidate.username}</strong><p>{candidate.headline || 'Profile details not downloaded yet'}</p>
          <button className="text-action" onClick={() => onOpen(candidate.id)} type="button">Open profile →</button>
        </th>)}</tr></thead>
        <tbody><tr><th scope="row">Profile location</th>{profiles.map((profile, index) => <td key={candidates[index].id}>{profile.data?.fields?.find(field => field.field_key === 'location' && field.provenance_available)?.value || 'Not available'}</td>)}</tr>{[...rows].map(([key, row]) => <tr key={key}><th scope="row"><span>{row.signal}</span>{row.label}</th>{profiles.map((profile, index) => {
          const claim = profile.data?.signals?.flatMap(signal => signal.claims.map(claim => ({ ...claim, key: `${signal.signal_id}:${claim.claim_key}` }))).find(claim => claim.key === key)
          const verdict = claim?.verdict ?? 'unknown'
          const source = claim?.evidence.find(evidence => evidence.availability.state === 'available')
          return <td key={candidates[index].id}>
            {profile.isError ? <div role="alert">Could not load evidence. <button className="text-action" onClick={() => void profile.refetch()} type="button">Retry</button></div> : profile.isPending ? <span role="status">Loading evidence…</span> : <>
              <span className={`comparison-verdict ${verdict}`}>{verdictLabels[verdict]}</span>
              <p>{source ? source.snippet : verdict === 'not_matched' ? 'No matching text found in the checked sections.' : verdict === 'unknown' ? 'More source evidence is needed.' : 'Open the profile for supporting details.'}</p>
            </>}
          </td>
        })}</tr>)}</tbody>
      </table>
    </div>
    {!rows.size ? <p role="status">{profiles.some(profile => profile.isPending) ? 'Loading profile evidence…' : 'No comparable claims available. Download profiles and add criteria to your search criteria.'}</p> : null}
    <p className="comparison-note">Matches use exact text and configured aliases. “No exact match” does not mean unqualified. Saved profile evidence does not independently verify a credential or its current validity.</p>
  </section>
}
