export interface ScoreIdentity {
  sessionId: string
  scoreId: string
  inputFingerprint: string
}

export interface EvidenceVerification extends ScoreIdentity {
  evidenceId: string
}

export function scoreIdentityKey(identity: ScoreIdentity): string {
  return `${identity.sessionId}\u0000${identity.scoreId}\u0000${identity.inputFingerprint}`
}

export function reconcileEvidenceVerifications(
  verifications: ReadonlyMap<string, EvidenceVerification>,
  currentScoreIdentities: ReadonlySet<string>,
): Map<string, EvidenceVerification> {
  return new Map(
    [...verifications].filter(([, verification]) =>
      currentScoreIdentities.has(scoreIdentityKey(verification)),
    ),
  )
}
