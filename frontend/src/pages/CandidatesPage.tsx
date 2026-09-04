import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acceptPhaseGateB,
  listCandidates,
  type SessionRecord,
} from '../api/client'
import { CandidateRow } from '../components/CandidateRow'
import { WeightsEditor } from '../components/WeightsEditor'
import {
  reconcileEvidenceVerifications,
  scoreIdentityKey,
  type EvidenceVerification,
} from '../scoreVerification'

export function CandidatesPage({
  session,
  onCandidateOpen,
  verifiedEvidence,
  onEvidenceReconciled,
  onScoresChanged,
}: {
  session: SessionRecord
  onCandidateOpen: (candidateId: string) => void
  verifiedEvidence: ReadonlyMap<string, EvidenceVerification>
  onEvidenceReconciled: (values: Map<string, EvidenceVerification>) => void
  onScoresChanged: () => void
}) {
  const client = useQueryClient()
  const [stage, setStage] = useState('')
  const [confidence, setConfidence] = useState('')
  const [minimum, setMinimum] = useState('')
  const [sort, setSort] = useState('score_desc')
  const [gateNote, setGateNote] = useState('Exact evidence spans spot-checked against stored raw text.')
  const candidates = useQuery({
    queryKey: ['ranked-candidates', session.id, stage, confidence, minimum, sort],
    queryFn: () =>
      listCandidates({
        session_id: session.id,
        stage: stage || undefined,
        confidence: confidence || undefined,
        min_score: minimum === '' ? undefined : Number(minimum),
        sort,
      }),
    enabled: Boolean(session.phase_gates?.A),
  })
  const currentScoreIdentities = new Set(
    (candidates.data ?? []).map((candidate) =>
      scoreIdentityKey({
        sessionId: session.id,
        scoreId: candidate.score_id,
        inputFingerprint: candidate.input_fingerprint,
      }),
    ),
  )
  const eligibleEvidence = reconcileEvidenceVerifications(
    verifiedEvidence,
    currentScoreIdentities,
  )
  const scoreIdentitySignature = [...currentScoreIdentities].sort().join('|')
  useEffect(() => {
    onEvidenceReconciled(eligibleEvidence)
    // The signature represents the complete current score set; callbacks are state sinks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scoreIdentitySignature])
  const gateB = useMutation({
    mutationFn: () => acceptPhaseGateB([...eligibleEvidence.keys()], gateNote),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['session'] }),
  })
  const orderedCandidates = orderCandidates(candidates.data ?? [], sort)

  if (!session.phase_gates?.A) {
    return (
      <section className="workspace-page" aria-labelledby="ranking-locked-title">
        <div className="empty-card phase-lock" role="status">
          <p className="eyebrow">Phase Gate A required</p>
          <h1 id="ranking-locked-title">Inspect the candidate pool before ranking.</h1>
          <p>Return to Find candidates, inspect extraction and dedupe, then accept Gate A.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="workspace-page ranked-page" aria-labelledby="ranked-title">
      <div className="page-intro compact-intro">
        <div>
          <p className="eyebrow">Step 3 · Rank retrieved evidence</p>
          <h1 id="ranked-title">Compare explainable matches.</h1>
          <p>
            Scores summarize only retrieved sections against your saved role brief.
            Unknown evidence stays unknown and always sorts after numeric results.
          </p>
        </div>
        <div className="version-card">
          <strong>{candidates.data?.length ?? 0} ranked</strong>
          <span>Gate A accepted · Gate B {session.phase_gates?.B ? 'accepted' : 'open'}</span>
        </div>
      </div>

      <WeightsEditor onScoresChanged={onScoresChanged} />

      <form className="ranking-filters panel" onSubmit={(event) => event.preventDefault()}>
        <p className="eyebrow">Filter and sort</p>
        <label className="field">
          <span>Retrieval stage</span>
          <select onChange={(event) => setStage(event.target.value)} value={stage}>
            <option value="">All stages</option>
            <option value="provisional">Provisional</option>
            <option value="enriched">Enriched</option>
          </select>
        </label>
        <label className="field">
          <span>Confidence</span>
          <select onChange={(event) => setConfidence(event.target.value)} value={confidence}>
            <option value="">All confidence</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="field">
          <span>Minimum numeric score</span>
          <input
            inputMode="decimal"
            max="100"
            min="0"
            onChange={(event) => setMinimum(event.target.value)}
            placeholder="Any"
            type="number"
            value={minimum}
          />
        </label>
        <label className="field">
          <span>Sort order</span>
          <select onChange={(event) => setSort(event.target.value)} value={sort}>
            <option value="score_desc">Score, high to low</option>
            <option value="confidence_desc">Confidence, high to low</option>
            <option value="name_asc">Name, A to Z</option>
          </select>
        </label>
      </form>

      {candidates.isPending ? (
        <p aria-live="polite">Calculating ranked evidence…</p>
      ) : candidates.isError ? (
        <div className="form-error" role="alert">
          Ranked candidates could not be loaded. Confirm Gate A is still accepted.
        </div>
      ) : orderedCandidates.length ? (
        <div className="ranked-list" aria-label="Ranked candidates">
          {orderedCandidates.map((candidate) => (
            <CandidateRow candidate={candidate} key={candidate.id} onOpen={onCandidateOpen} />
          ))}
        </div>
      ) : (
        <div className="empty-card">
          <h2>No candidates match these filters.</h2>
          <p>Clear a filter or retrieve more profile sections from the candidate pool.</p>
        </div>
      )}

      <section className="phase-gate-card panel" aria-labelledby="gate-b-title">
        <div>
          <p className="eyebrow">Phase Gate B · matching</p>
          <h2 id="gate-b-title">Verify at least 10 exact evidence links</h2>
          <p>
            Open candidate evidence, compare each highlighted span with stored raw text,
            then check its separate verification box. Coverage and search context never count.
          </p>
        </div>
        {session.phase_gates?.B ? (
          <p className="gate-accepted" role="status">✓ Gate B accepted · {session.phase_gates.B.evidence_ids.length} evidence spans recorded</p>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault()
              gateB.mutate()
            }}
          >
            <p className="verification-count" aria-live="polite">
              <strong>{eligibleEvidence.size} / 10</strong> exact spans verified for current scores
            </p>
            <label className="field">
              <span>Verification note</span>
              <textarea onChange={(event) => setGateNote(event.target.value)} rows={2} value={gateNote} />
            </label>
            <button className="primary-action" disabled={eligibleEvidence.size < 10 || gateB.isPending} type="submit">
              {gateB.isPending ? 'Recording…' : 'Accept Gate B'}
            </button>
            {gateB.isError ? <p className="field-error" role="alert">{gateB.error.message}</p> : null}
          </form>
        )}
      </section>
    </section>
  )
}

function orderCandidates<T extends {
  id: string
  display_name: string | null
  score: number | null
  confidence: number
}>(candidates: T[], sort: string): T[] {
  return [...candidates].sort((left, right) => {
    if (left.score === null && right.score !== null) return 1
    if (left.score !== null && right.score === null) return -1
    if (sort === 'name_asc') {
      const byName = (left.display_name ?? '').localeCompare(right.display_name ?? '')
      if (byName !== 0) return byName
    } else if (sort === 'confidence_desc') {
      const byConfidence = right.confidence - left.confidence
      if (byConfidence !== 0) return byConfidence
    } else if (left.score !== null && right.score !== null) {
      const byScore = right.score - left.score
      if (byScore !== 0) return byScore
      const byConfidence = right.confidence - left.confidence
      if (byConfidence !== 0) return byConfidence
    }
    return left.id.localeCompare(right.id)
  })
}
