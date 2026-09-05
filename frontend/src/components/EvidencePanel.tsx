import type { ReactNode } from 'react'
import type { ProfileEvidenceRecord, ScoreSignalRecord } from '../api/client'
import {
  ALL_INERT_COPY,
  UNKNOWN_COPY,
  missingReasonCopy,
} from './scoringCopy'
import { VerdictBadge } from './VerdictBadge'
import { SignalTable } from './SignalTable'

export { ALL_INERT_COPY, UNKNOWN_COPY } from './scoringCopy'

export function EvidencePanel({
  signals,
  allInert,
  verifiedEvidenceIds,
  onEvidenceOpen,
  onEvidenceVerified,
  showSummary = true,
  renderSource,
  selectedEvidenceId,
}: {
  selectedEvidenceId?: string | null
  renderSource?: (evidence: ProfileEvidenceRecord, label: string) => ReactNode
  signals: ScoreSignalRecord[]
  showSummary?: boolean
  allInert: boolean
  verifiedEvidenceIds: Set<string>
  onEvidenceOpen: (sectionName: string, evidenceId: string) => void
  onEvidenceVerified: (evidenceId: string, verified: boolean) => void
}) {
  if (allInert) {
    return (
      <section className="panel evidence-panel" aria-labelledby="evidence-title">
        <p className="eyebrow">Evidence</p>
        <h2 id="evidence-title">No active scoring criteria</h2>
        <p>{ALL_INERT_COPY}</p>
      </section>
    )
  }
  return (
    <section className="panel evidence-panel" aria-labelledby="evidence-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Evidence, claim by claim</p>
          <h2 id="evidence-title">{showSummary ? 'Why this score changed' : 'Review against your criteria'}</h2>
        </div>
        <span>{verifiedEvidenceIds.size} sources checked</span>
      </div>
      <p className="profile-muted">Start with the criteria that matter most. Open a passage to check what the person did, in which role, and whether it supports the match.</p>
      {showSummary ? <SignalTable signals={signals} /> : null}
      <div className="signal-claims">
        {signals.filter(signal => signal.claims.length > 0).map((signal) => (
          <section aria-labelledby={`signal-${signal.id}`} className="signal-claim-group" key={signal.id}>
            <h3 id={`signal-${signal.id}`}>{signal.label}</h3>
            <ol>
              {signal.claims.map((claim, claimIndex) => (
                <li className={`claim-card ${claim.verdict}`} key={claim.id}>
                  <div className="claim-heading">
                    <strong>{claim.display_term}</strong>
                    <VerdictBadge verdict={claim.verdict} />
                  </div>
                  {claim.verdict === 'unknown' ? (
                    <div
                      className="missing-evidence"
                    >
                      <span>Evidence {UNKNOWN_COPY}.</span>
                      <span className="unknown-explanation">
                        This does not mean the candidate lacks this qualification.
                      </span>
                      {claim.missing_sections.map((missing) => (
                        <span key={`${missing.section_name}-${missing.reason}`}>
                          {missing.section_name.replaceAll('_', ' ')} · {missingReasonCopy(missing.reason)}
                        </span>
                      ))}
                    </div>
                  ) : claim.verdict === 'not_matched' ? (
                    <div className="coverage-evidence">
                      <p>Searched every required retrieved section; no exact match was found.</p>
                      {claim.coverage.map((coverage) => (
                        <span key={coverage.section_name}>
                          {coverage.section_name.replaceAll('_', ' ')} · terms {coverage.normalized_terms.concat(coverage.aliases).join(', ')} · matcher {coverage.matcher_version}
                        </span>
                      ))}
                    </div>
                  ) : (
                    <div className="profile-evidence-list">
                      {claim.evidence.map((evidence, evidenceIndex) =>
                        evidence.availability.state === 'available' ? (
                          <div className="profile-evidence" key={evidence.id}>
                            <button
                              className="evidence-link"
                              aria-expanded={renderSource ? selectedEvidenceId === evidence.id : undefined}
                              onClick={() => onEvidenceOpen(evidence.section_name, evidence.id)}
                              type="button"
                            >
                              “{evidence.snippet}”
                              <small>
                                {evidence.section_name.replaceAll('_', ' ')} · {verifiedEvidenceIds.has(evidence.id) ? 'Source checked' : 'Open source to check'}
                              </small>
                            </button>
                            {renderSource ? (selectedEvidenceId === evidence.id ? renderSource(evidence, `I verified this exact source span for ${claim.display_term} in ${evidence.section_name.replaceAll('_', ' ')} (${signal.signal_id}, claim ${claimIndex + 1}, evidence ${evidenceIndex + 1})`) : null) : <label className="verify-control">
                              <input
                                aria-label={`I verified this exact source span for ${claim.display_term} in ${evidence.section_name.replaceAll('_', ' ')} (${signal.signal_id}, claim ${claimIndex + 1}, evidence ${evidenceIndex + 1})`}
                                checked={verifiedEvidenceIds.has(evidence.id)}
                                onChange={(event) => onEvidenceVerified(evidence.id, event.target.checked)}
                                type="checkbox"
                              />
                              I verified this exact source span
                            </label>}
                          </div>
                        ) : evidence.availability.state === 'masked' ? (
                          <div className="provenance-withheld" key={evidence.id} role="status">
                            <strong>Evidence withheld</strong>
                            <span>{evidence.availability.reason} It cannot be opened or verified.</span>
                          </div>
                        ) : (
                          <div className="raw-purged" key={evidence.id} role="status">
                            <strong>
                              Raw text purged on{' '}
                              {new Date(evidence.availability.purged_at).toLocaleString()}
                            </strong>
                            <span>{evidence.availability.reason} This evidence cannot be opened or verified.</span>
                          </div>
                        ),
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          </section>
        ))}
      </div>
    </section>
  )
}
