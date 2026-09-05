import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getConfiguration, saveConfiguration, type AppConfiguration } from '../api/client'
import { WeightsEditor } from '../components/WeightsEditor'
import './settings.css'

export function SettingsPage({ onScoresChanged }: { onScoresChanged: () => void }) {
  const query = useQuery({ queryKey: ['configuration'], queryFn: getConfiguration })
  return <section className="settings-page">
    <header className="settings-heading"><h1>Settings</h1><p>Manage downloads and scoring preferences.</p></header>
    {query.isPending ? <p role="status">Loading settings…</p> : query.isError ? <div className="form-error" role="alert">Settings could not be loaded. <button type="button" className="quiet-action" onClick={() => void query.refetch()}>Try again</button></div> : <SettingsForm initial={query.data} />}
    <WeightsEditor onScoresChanged={onScoresChanged} />
  </section>
}

function SettingsForm({ initial }: { initial: AppConfiguration }) {
  const [values, setValues] = useState(initial)
  const [saved, setSaved] = useState(initial)
  const client = useQueryClient()
  const dirty = JSON.stringify(values) !== JSON.stringify(saved)
  const mutation = useMutation({ mutationFn: saveConfiguration, onSuccess: data => {
    setSaved(data)
    client.setQueryData(['configuration'], data)
  } })
  function change<K extends keyof AppConfiguration>(key: K, value: AppConfiguration[K]) {
    mutation.reset()
    setValues(current => ({ ...current, [key]: value }))
  }
  function number(key: keyof AppConfiguration, label: string, help: string, min: number, max: number, step = 1) {
    return <label className="settings-row"><span className="settings-label"><span>{label}</span><small>{help}</small></span><input type="number" required min={min} max={max} step={step} value={values[key] as number} onChange={e => change(key, e.target.value === '' ? '' as unknown as number : Number(e.target.value))} /></label>
  }
  function toggle(key: 'automatic_downloads' | 'automatic_pagination', label: string, help: string) {
    return <label className="settings-row"><span className="settings-label"><span>{label}</span><small>{help}</small></span><input type="checkbox" checked={values[key]} onChange={e => change(key, e.target.checked)} /></label>
  }
  return <form className="settings-sheet" onSubmit={e => { e.preventDefault(); mutation.mutate(values) }}>
    <fieldset disabled={mutation.isPending} className="settings-group" aria-labelledby="settings-downloads"><h2 id="settings-downloads">Downloads</h2>
      {toggle('automatic_downloads', 'Download profiles automatically', 'Download and score people found by new searches.')}
      <label className="settings-row"><span className="settings-label"><span>Profiles at a time</span><small>Up to two with the current connector.</small></span><select value={values.profile_concurrency} onChange={e => change('profile_concurrency', Number(e.target.value))}><option value={1}>1</option><option value={2}>2</option></select></label>
      {number('inter_call_delay_seconds', 'Pause between reads', 'Wait after a completed read, in seconds.', 0, 60, 0.5)}
    </fieldset>
    <fieldset disabled={mutation.isPending} className="settings-group" aria-labelledby="settings-batches"><h2 id="settings-batches">Search batches</h2>
      {number('download_batch_limit', 'New downloads per batch', 'Previously saved profiles do not count toward this limit.', 1, 1000)}
      {toggle('automatic_pagination', 'Continue through search pages', 'Fetch the next page until results end or a batch limit is reached.')}
      {number('search_page_limit', 'Maximum search pages', 'Stop discovery after this many pages.', 1, 1000)}
    </fieldset>
    <fieldset disabled={mutation.isPending} className="settings-group" aria-labelledby="settings-retries"><h2 id="settings-retries">Retry timing</h2>
      {number('busy_retry_seconds', 'When the connector is busy', 'Seconds to wait before retrying.', 1, 300)}
      {number('timeout_retry_seconds', 'When a request times out', 'Seconds to wait before retrying. Use 0 for no additional wait.', 0, 300)}
    </fieldset>
    <div className="settings-actions"><span role="status">{mutation.isSuccess && !dirty ? 'Settings saved.' : dirty ? 'Unsaved changes' : 'Changes apply to upcoming reads and new search batches.'}</span><button type="button" className="quiet-action" disabled={!dirty || mutation.isPending} onClick={() => { setValues(saved); mutation.reset() }}>Discard changes</button><button type="submit" className="primary-action" disabled={!dirty || mutation.isPending}>{mutation.isPending ? 'Saving…' : 'Save settings'}</button></div>
    {mutation.isError ? <p className="form-error" role="alert">{mutation.error.message}</p> : null}
  </form>
}
