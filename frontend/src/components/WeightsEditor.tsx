import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getScoringConfig,
  SCORING_WEIGHT_KEYS,
  updateScoringConfig,
  type ScoringConfigRecord,
  type ScoringWeightKey,
} from '../api/client'

const SIGNAL_LABELS: Record<string, string> = {
  'S-1': 'Required skills',
  'S-2': 'Optional skills',
  'S-3': 'Experience depth',
  'S-4': 'Title similarity',
  'S-5': 'Industry relevance',
  'S-6': 'Location fit',
  'S-8': 'Required credentials',
}

export function WeightsEditor({
  onScoresChanged,
}: {
  onScoresChanged: () => void
}) {
  const config = useQuery({ queryKey: ['weights'], queryFn: getScoringConfig })
  return (
    <details className="weights-editor panel">
      <summary>
        <span>
          <strong>Scoring weights</strong>
          <small>Saved version {config.data?.version ?? 'loading'}</small>
        </span>
        <span>Edit</span>
      </summary>
      {config.isError ? (
        <p role="alert">
          {config.error instanceof Error
            ? config.error.message
            : 'Scoring configuration could not be loaded.'}
        </p>
      ) : config.data ? (
        <LoadedWeightsEditor
          config={config.data}
          key={config.data.version}
          onRefresh={() => config.refetch()}
          onScoresChanged={onScoresChanged}
        />
      ) : (
        <p aria-live="polite">Loading weights…</p>
      )}
    </details>
  )
}

function LoadedWeightsEditor({
  config,
  onRefresh,
  onScoresChanged,
}: {
  config: ScoringConfigRecord
  onRefresh: () => void
  onScoresChanged: () => void
}) {
  const client = useQueryClient()
  const [weights, setWeights] = useState<Record<ScoringWeightKey, number>>(
    config.weights,
  )
  const [metroText, setMetroText] = useState(() =>
    JSON.stringify(config.metro_region_equivalences, null, 2),
  )
  const save = useMutation({
    mutationFn: async () => {
      const parsed = JSON.parse(metroText) as Record<string, string[]>
      return updateScoringConfig({
        expected_version: config.version,
        weights,
        metro_region_equivalences: parsed,
      })
    },
    onSuccess: async () => {
      onScoresChanged()
      await Promise.all([
        client.invalidateQueries({ queryKey: ['weights'] }),
        client.invalidateQueries({ queryKey: ['ranked-candidates'] }),
        client.invalidateQueries({ queryKey: ['candidate'] }),
      ])
    },
  })
  return (
        <form
          onSubmit={(event) => {
            event.preventDefault()
            save.mutate()
          }}
        >
          <div className="weight-grid">
            {SCORING_WEIGHT_KEYS.map((signalId) => {
              const inert = config.inert_reasons[signalId]
              const credentialDisabled = signalId === 'S-8' && Boolean(inert)
              return (
                <label className="weight-control" key={signalId}>
                  <span>
                    <strong>{signalId} · {SIGNAL_LABELS[signalId] ?? signalId}</strong>
                    {inert ? (
                      <small>Saved, not currently applied: brief input is empty.</small>
                    ) : (
                      <small>Active scoring signal</small>
                    )}
                  </span>
                  <input
                    aria-label={`${SIGNAL_LABELS[signalId] ?? signalId} weight`}
                    disabled={credentialDisabled}
                    min="0"
                    onChange={(event) =>
                      setWeights((current) => ({
                        ...current,
                        [signalId]: Number(event.target.value),
                      }))
                    }
                    step="1"
                    type="number"
                    value={weights[signalId] ?? 0}
                  />
                </label>
              )
            })}
          </div>
          <p className="context-only-row">
            <strong>S-7 · Network context</strong>
            <span>Search only — not a scoring criterion.</span>
          </p>
          <label className="field">
            <span>Metro/region equivalences (JSON)</span>
            <textarea
              aria-describedby="metro-help"
              onChange={(event) => setMetroText(event.target.value)}
              rows={5}
              spellCheck={false}
              value={metroText}
            />
            <small id="metro-help">For example: {`{"Chicago":["Greater Chicago Area"]}`}</small>
          </label>
          {save.isError ? (
            <div className="form-error" role="alert">
              <strong>Weights were not saved.</strong>
              <span>
                {save.error instanceof SyntaxError
                  ? 'Metro equivalences must be valid JSON.'
                  : `${save.error.message} Refresh to load the current version, then review your edits.`}
              </span>
              <button onClick={onRefresh} type="button">Refresh current version</button>
            </div>
          ) : null}
          {save.isSuccess ? (
            <p aria-live="polite" className="queued-confirmation">
              New immutable version saved; candidate scores were refreshed.
            </p>
          ) : null}
          <button className="primary-action" disabled={save.isPending} type="submit">
            {save.isPending ? 'Saving…' : `Save from ${config.version}`}
          </button>
        </form>
  )
}
