import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acceptPhaseGateB,
  listCandidates,
  type SessionRecord,
} from '../api/client'
import { ComparisonBoard } from '../components/ComparisonBoard'
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
  selectedForComparison,
  onComparisonChange,
}: {
  session: SessionRecord
  onCandidateOpen: (candidateId: string) => void
  verifiedEvidence: ReadonlyMap<string, EvidenceVerification>
  onEvidenceReconciled: (values: Map<string, EvidenceVerification>) => void
  onScoresChanged: () => void
  selectedForComparison?: string[]
  onComparisonChange?: (ids: string[]) => void
}) {
  const client = useQueryClient()
  const [localComparisonIds, setLocalComparisonIds] = useState<string[]>([])
  const comparisonIds = selectedForComparison ?? localComparisonIds
  const setComparisonIds = (update: string[] | ((ids: string[]) => string[])) => {
    const next = typeof update === 'function' ? update(comparisonIds) : update
    if (onComparisonChange) onComparisonChange(next)
    else setLocalComparisonIds(next)
  }
  const [stage, setStage] = useState('')
  const [confidence, setConfidence] = useState('')
  const [minimum, setMinimum] = useState('')
  const [sort, setSort] = useState('score_desc')
  const [hasReconciledCompleteDataset, setHasReconciledCompleteDataset] =
    useState(false)
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
  const isUnfiltered = stage === '' && confidence === '' && minimum === ''
  const hasCompleteIdentityDataset =
    isUnfiltered && candidates.isSuccess && !candidates.isFetching
  const currentScoreIdentities = new Set(
    (hasCompleteIdentityDataset ? candidates.data : []).map((candidate) =>
      scoreIdentityKey({
        sessionId: session.id,
        scoreId: candidate.score_id,
        inputFingerprint: candidate.input_fingerprint,
      }),
    ),
  )
  const eligibleEvidence = hasCompleteIdentityDataset
    ? reconcileEvidenceVerifications(verifiedEvidence, currentScoreIdentities)
    : new Map(verifiedEvidence)
  const scoreIdentitySignature = JSON.stringify(
    [...currentScoreIdentities].sort(),
  )
  const verificationSignature = JSON.stringify(
    [...verifiedEvidence]
      .map(([evidenceId, verification]) => [
        evidenceId,
        scoreIdentityKey(verification),
      ])
      .sort(([left], [right]) => left.localeCompare(right)),
  )
  useEffect(() => {
    if (!hasCompleteIdentityDataset) return
    onEvidenceReconciled(eligibleEvidence)
    setHasReconciledCompleteDataset(true)
    // These signatures bind the sink to a successful, idle, unfiltered identity set.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasCompleteIdentityDataset, scoreIdentitySignature, verificationSignature])
  const gateB = useMutation({
    mutationFn: () => acceptPhaseGateB([...eligibleEvidence.keys()], gateNote),
    onSuccess: async () => client.invalidateQueries({ queryKey: ['session'] }),
  })
  // The API owns the canonical ordering, including score ties and Unicode names.
  const orderedCandidates = candidates.data ?? []

  if (!session.phase_gates?.A) {
    return (
      <section className="workspace-page" aria-labelledby="ranking-locked-title">
        <div className="empty-card phase-lock" role="status">
          <p className="eyebrow">Review required</p>
          <h1 id="ranking-locked-title">Inspect the candidate pool before ranking.</h1>
          <p>Return to Find candidates, inspect extraction and dedupe, then confirm your review.</p>
        </div>
      </section>
    )
  }

  return (
    <section className="workspace-page ranked-page" aria-labelledby="ranked-title">
      <div className="page-intro compact-intro">
        <div>
          <p className="eyebrow">Your matches</p>
          <h1 id="ranked-title">Compare matches</h1>
          <p>
            Scores summarize only retrieved sections against your saved role brief.
            Missing information is shown as unknown.
          </p>
        </div>
        <div className="version-card">
          <strong>{candidates.data?.length ?? 0} ranked</strong>
          <span>Candidate list reviewed · Evidence review {session.phase_gates?.B ? 'complete' : 'pending'}</span>
        </div>
      </div>

      <div className="comparison-instructions"><strong>Choose up to 3 people to compare</strong><span>Select candidates below to see their evidence side by side.</span>{comparisonIds.length ? <a href="#comparison-board">View comparison ({comparisonIds.length}/3) ↓</a> : null}</div>
      {comparisonIds.length ? <ComparisonBoard
        candidates={orderedCandidates.filter(candidate => comparisonIds.includes(candidate.id))}
        onRemove={id => setComparisonIds(ids => ids.filter(value => value !== id))}
        onOpen={onCandidateOpen}
      /> : null}
      <WeightsEditor onScoresChanged={onScoresChanged} />

      <form className="ranking-filters panel" onSubmit={(event) => event.preventDefault()}>
        <p className="eyebrow">Filter and sort</p>
        <label className="field">
          <span>Retrieval stage</span>
          <select onChange={(event) => { setStage(event.target.value); setComparisonIds([]) }} value={stage}>
            <option value="">All stages</option>
            <option value="provisional">Provisional</option>
            <option value="enriched">Enriched</option>
          </select>
        </label>
        <label className="field">
          <span>Confidence</span>
          <select onChange={(event) => { setConfidence(event.target.value); setComparisonIds([]) }} value={confidence}>
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
            onChange={(event) => { setMinimum(event.target.value); setComparisonIds([]) }}
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
            <div className="comparison-candidate" key={candidate.id}>
              <label className="comparison-pick"><input type="checkbox" checked={comparisonIds.includes(candidate.id)} disabled={!comparisonIds.includes(candidate.id) && comparisonIds.length >= 3} onChange={event => setComparisonIds(ids => event.target.checked ? [...ids, candidate.id] : ids.filter(id => id !== candidate.id))} />Compare {candidate.display_name || candidate.username}</label>
              <CandidateRow candidate={candidate} onOpen={onCandidateOpen} />
            </div>
          ))}
        </div>
      ) : (
        <div className="empty-card">
          <h2>No candidates match these filters.</h2>
          <p>Clear a filter or retrieve more profile sections from the candidate pool.</p>
        </div>
      )}

      <details className="phase-gate-card panel simple-options"><summary>Evidence quality check <span>{eligibleEvidence.size} links checked</span></summary>
        <div>
          <p className="eyebrow">Evidence quality check</p>
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
            <button
              className="primary-action"
              disabled={
                eligibleEvidence.size < 10 ||
                gateB.isPending ||
                !hasReconciledCompleteDataset ||
                (isUnfiltered && !hasCompleteIdentityDataset)
              }
              type="submit"
            >
              {gateB.isPending ? 'Recording…' : 'Accept Gate B'}
            </button>
            {gateB.isError ? <p className="field-error" role="alert">{gateB.error.message}</p> : null}
          </form>
        )}
      </details>
    </section>
  )
}
