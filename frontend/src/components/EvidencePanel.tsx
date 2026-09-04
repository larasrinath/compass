import type { ScoreSignalRecord } from '../api/client'
import { ALL_INERT_COPY, UNKNOWN_COPY, verdictCopy } from './scoringCopy'
import { SignalTable } from './SignalTable'

export { ALL_INERT_COPY, UNKNOWN_COPY } from './scoringCopy'

export function EvidencePanel({
  signals,
  allInert,
  verifiedEvidenceIds,
  onEvidenceOpen,
  onEvidenceVerified,
}: {
  signals: ScoreSignalRecord[]
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
          <h2 id="evidence-title">Why this score changed</h2>
        </div>
        <span>Opening is not verification</span>
      </div>
      <SignalTable signals={signals} />
      <div className="signal-claims">
        {signals.map((signal) => (
          <section aria-labelledby={`signal-${signal.id}`} className="signal-claim-group" key={signal.id}>
            <h3 id={`signal-${signal.id}`}>{signal.signal_id} · {signal.label}</h3>
            <ol>
              {signal.claims.map((claim) => (
                <li className={`claim-card ${claim.verdict}`} key={claim.id}>
                  <div className="claim-heading">
                    <strong>{claim.display_term}</strong>
                    <span className={`verdict-symbol ${claim.verdict}`}>
                      {claim.verdict === 'unknown'
                        ? `? ${UNKNOWN_COPY}`
                        : `${claim.verdict === 'matched' ? '✓' : claim.verdict === 'contradicted' ? '!' : '○'} ${verdictCopy(claim.verdict)}`}
                    </span>
                  </div>
                  {claim.verdict === 'unknown' ? (
                    <div
                      className="missing-evidence"
                    >
                      <span className="unknown-explanation">
                        This does not mean the candidate lacks this qualification.
                      </span>
                      {claim.missing_sections.map((missing) => (
                        <span key={`${missing.section_name}-${missing.reason}`}>
                          {missing.section_name.replaceAll('_', ' ')} · {missing.reason.replaceAll('_', ' ')}
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
                      {claim.evidence.map((evidence) =>
                        evidence.availability.state === 'available' ? (
                          <div className="profile-evidence" key={evidence.id}>
                            <button
                              className="evidence-link"
                              onClick={() => onEvidenceOpen(evidence.section_name, evidence.id)}
                              type="button"
                            >
                              “{evidence.snippet}”
                              <small>
                                {evidence.section_name.replaceAll('_', ' ')} · {evidence.matcher} · code points {evidence.span_start}:{evidence.span_end}
                              </small>
                            </button>
                            <label className="verify-control">
                              <input
                                checked={verifiedEvidenceIds.has(evidence.id)}
                                onChange={(event) => onEvidenceVerified(evidence.id, event.target.checked)}
                                type="checkbox"
                              />
                              I verified this exact source span
                            </label>
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
