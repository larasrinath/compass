import type { ClaimVerdict } from '../api/client'

export const UNKNOWN_COPY = 'not found in the retrieved data'
export const ALL_INERT_COPY =
  'Add a required or optional skill, experience minimum, target title, industry, target location, or required credential to calculate a score.'

export function verdictCopy(verdict: ClaimVerdict | 'mixed') {
  return verdict === 'unknown' ? UNKNOWN_COPY : verdict.replace('_', ' ')
}
