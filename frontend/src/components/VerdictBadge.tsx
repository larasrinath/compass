import type { ClaimVerdict } from '../api/client'

const labels: Record<ClaimVerdict | 'mixed', string> = {
  matched: 'Matched',
  not_matched: 'No exact match',
  unknown: 'Not checked',
  contradicted: 'Conflicting evidence',
  mixed: 'Partial match',
}

export function VerdictBadge({ verdict }: { verdict: ClaimVerdict | 'mixed' }) {
  return <span className={`verdict-badge ${verdict}`}>{labels[verdict]}</span>
}
