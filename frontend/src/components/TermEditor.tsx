import { useState } from 'react'
import type { BriefTerm } from '../api/client'
import { CompassIcon } from './CompassIcon'

export function TermEditor({ field, label, values, errors = [], onChange, hint, placeholder = 'Add a term' }: {
  field: string
  label: string
  values: BriefTerm[]
  errors?: string[]
  onChange: (values: BriefTerm[]) => void
  hint?: string
  placeholder?: string
}) {
  const [nextTerm, setNextTerm] = useState('')
  function addTerm() {
    const term = nextTerm.trim()
    if (!term) return
    if (!values.some(value => value.term.localeCompare(term, undefined, { sensitivity: 'accent' }) === 0)) {
      onChange([...values, { term, aliases: [] }])
    }
    setNextTerm('')
  }
  return (
    <div aria-labelledby={`${field}-label`} className="criteria-terms" data-field-prefix={field} role="group">
      <label className="criteria-label" id={`${field}-label`}>{label}</label>
      {hint ? <p className="criteria-hint">{hint}</p> : null}
      <div className="criteria-chips">
        {values.map((value, index) => (
          <div className="criteria-chip" key={`term-${index}`}>
            <input aria-label={`${label} term ${index + 1}`} style={{ width: `${Math.max(4, Math.min(32, value.term.length + 1))}ch` }} value={value.term}
              onChange={event => onChange(values.map((item, row) => row === index ? { ...item, term: event.target.value } : item))}
              onKeyDown={event => { if (event.key === 'Enter') event.preventDefault() }} />
            <button aria-label={`Remove ${value.term || `${label} term ${index + 1}`}`} onClick={() => onChange(values.filter((_, row) => row !== index))} type="button"><CompassIcon name="close" size={14} /></button>
          </div>
        ))}
        <div className="criteria-chip criteria-chip-add">
          <input aria-label={`New ${label.toLocaleLowerCase()} term`} placeholder={placeholder} value={nextTerm} onChange={event => setNextTerm(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addTerm() } }}
            onBlur={addTerm} />
          <button aria-label="Add term" onClick={addTerm} type="button"><CompassIcon name="plus" size={18} /></button>
        </div>
      </div>
      {values.length > 0 ? <details className="criteria-aliases" open={errors.length > 0 || undefined}>
        <summary>Alternate names{values.some(value => value.aliases.some(alias => alias.trim())) ? ' · saved' : ''}</summary>
        <p className="criteria-hint">Other names to match in a profile. Separate with commas.</p>
        {values.map((value, index) => <label className="field" key={index}>
          <span>Aliases for {value.term || `${label} term ${index + 1}`}</span>
          <input value={value.aliases.join(', ')} placeholder="e.g. PostgreSQL, Postgres"
            onChange={event => onChange(values.map((item, row) => row === index ? { ...item, aliases: event.target.value.split(',').map(alias => alias.trimStart()) } : item))}
            onKeyDown={event => { if (event.key === 'Enter') event.preventDefault() }} />
        </label>)}
      </details> : null}
      {errors.length ? <ul className="field-errors" role="alert">{errors.map(error => <li key={error}>{error}</li>)}</ul> : null}
    </div>
  )
}
