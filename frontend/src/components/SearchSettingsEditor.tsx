import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { getCompanyLookup, startCompanyLookup } from '../api/client'
import { NETWORKS, type SearchSettings } from '../searchSettings'

export function SearchSettingsEditor({ sessionId, value, onChange, retrievalReady, queueRevision }: {
  sessionId: string
  value: SearchSettings
  onChange: (value: SearchSettings) => void
  retrievalReady: boolean
  queueRevision: number
}) {
  const [companySlug, setCompanySlug] = useState('')
  const [lookupId, setLookupId] = useState<string | null>(null)
  const lookup = useQuery({ queryKey: ['company-lookup', lookupId], queryFn: () => getCompanyLookup(lookupId!), enabled: Boolean(lookupId) })
  useEffect(() => {
    if (queueRevision && lookupId) void lookup.refetch()
    // Queue events refresh the selected lookup without polling LinkedIn.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queueRevision, lookupId])
  const company = useMutation({ mutationFn: () => startCompanyLookup(sessionId, companySlug.trim()), onSuccess: result => setLookupId(result.lookup_id) })
  return <div className="brief-search-settings">
    <fieldset className="network-fieldset">
      <legend>Network distance</legend>
      <div className="network-options">{NETWORKS.map(option => <label key={option.value}>
        <input type="checkbox" checked={value.network.includes(option.value)} onChange={event => onChange({ ...value, network: event.target.checked ? [...value.network, option.value] : value.network.filter(item => item !== option.value) })} />
        <span>{option.label}</span>
      </label>)}</div>
      <p className="criteria-hint">Leave all unchecked to search any network. Network distance never affects match scores.</p>
    </fieldset>
    <div className="brief-company-fields">
    <label className="field"><span>Company ID — optional</span><input inputMode="numeric" pattern="[0-9]*" placeholder="Any company" value={value.companyId} onChange={event => onChange({ ...value, companyId: event.target.value })} /></label>
    <div className="brief-company-lookup">
      <label className="field"><span>Find a company ID</span><input value={companySlug} placeholder="Company name from its LinkedIn URL" onChange={event => setCompanySlug(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); if (companySlug.trim() && retrievalReady && !company.isPending) company.mutate() } }} /></label>
      <button className="quiet-action" type="button" disabled={!companySlug.trim() || !retrievalReady || company.isPending} onClick={() => company.mutate()}>{company.isPending ? 'Queueing…' : 'Look up company'}</button>
      {company.isError ? <p className="field-error" role="alert">{company.error.message}</p> : null}
      {lookup.isError ? <p className="field-error" role="alert">Company lookup could not be loaded. <button type="button" className="text-action" onClick={() => void lookup.refetch()}>Try again</button></p> : lookup.data ? <div className="brief-lookup-results" aria-live="polite">{lookup.data.candidates.length ? lookup.data.candidates.map(item => <button className="quiet-action" key={item.urn_id} type="button" onClick={() => onChange({ ...value, companyId: item.urn_id })}>Use {item.text} · {item.urn_id}</button>) : <p className="criteria-hint">{lookup.data.note ?? `Lookup ${lookup.data.status}.`}</p>}</div> : lookupId ? <p className="criteria-hint" role="status">Company lookup queued. Results will appear here.</p> : null}
    </div>
    </div>
  </div>
}
