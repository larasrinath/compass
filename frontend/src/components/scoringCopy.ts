import type { ClaimVerdict, MissingReason } from '../api/client'

export const UNKNOWN_COPY = 'not found in the retrieved data'
export const ALL_INERT_COPY =
  'Add a required or optional skill, experience minimum, target title, industry, target location, or required credential to calculate a score.'

const MISSING_REASON_COPY: Record<MissingReason, string> = {
  not_requested: 'not requested',
  rate_limit: 'not retrieved because a rate limit stopped the request',
  fetch_error: 'could not be retrieved',
  unparseable: 'retrieved, but could not be parsed reliably',
}

export function missingReasonCopy(reason: MissingReason): string {
  return MISSING_REASON_COPY[reason]
}

export function verdictCopy(verdict: ClaimVerdict | 'mixed') {
  return verdict === 'unknown' ? UNKNOWN_COPY : verdict.replace('_', ' ')
}
