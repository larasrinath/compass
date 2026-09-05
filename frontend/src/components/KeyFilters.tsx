import { useState } from 'react'
import type { BriefTerm } from '../api/client'
import { CompassIcon } from './CompassIcon'

export function KeyFilters({ skills, credentials, skillErrors = [], credentialErrors = [], onSkillsChange, onCredentialsChange }: {
  skills: BriefTerm[]
  credentials: BriefTerm[]
  skillErrors?: string[]
  credentialErrors?: string[]
  onSkillsChange: (values: BriefTerm[]) => void
  onCredentialsChange: (values: BriefTerm[]) => void
}) {
  const [nextTerm, setNextTerm] = useState('')
  const [kind, setKind] = useState('skill')
  const groups = [
    { field: 'required_skills', label: 'Skill', values: skills, errors: skillErrors, onChange: onSkillsChange },
    { field: 'required_credentials', label: 'Credential', values: credentials, errors: credentialErrors, onChange: onCredentialsChange },
  ]

  function addFilter() {
    const term = nextTerm.trim()
    if (!term) return
    const group = groups[kind === 'skill' ? 0 : 1]
    if (!group.values.some(value => value.term.localeCompare(term, undefined, { sensitivity: 'accent' }) === 0)) {
      group.onChange([...group.values, { term, aliases: [] }])
    }
    setNextTerm('')
  }

  return (
    <div className="criteria-terms" role="group" aria-labelledby="key-filters-label">
      <span className="criteria-label" id="key-filters-label">Skills & key filters</span>
      <p className="criteria-hint">Skills and credentials you want to see in each profile.</p>
      <div className="criteria-chips">
        {groups.flatMap(group => group.values.map((value, index) => (
          <div className="criteria-chip" data-field-prefix={group.field} key={`${group.field}-${index}`}>
            <input aria-label={`${group.label} filter ${index + 1}`} value={value.term}
              style={{ width: `${Math.max(4, Math.min(32, value.term.length + 1))}ch` }}
              onChange={event => group.onChange(group.values.map((item, row) => row === index ? { ...item, term: event.target.value } : item))}
              onKeyDown={event => { if (event.key === 'Enter') event.preventDefault() }} />
            {group.field === 'required_credentials' ? <span className="criteria-chip-kind">Credential</span> : null}
            <button type="button" aria-label={`Remove ${value.term || `${group.label} filter ${index + 1}`}`} onClick={() => group.onChange(group.values.filter((_, row) => row !== index))}><CompassIcon name="close" size={14} /></button>
          </div>
        )))}
        <div className="criteria-chip criteria-chip-add criteria-filter-add" onBlur={event => {
          if (!event.currentTarget.contains(event.relatedTarget)) addFilter()
        }}>
          <input aria-label="New key filter" placeholder="Add a filter" value={nextTerm} onChange={event => setNextTerm(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addFilter() } }} />
          <select aria-label="Filter type" value={kind} onChange={event => setKind(event.target.value)}>
            <option value="skill">Skill</option>
            <option value="credential">Credential</option>
          </select>
          <button type="button" aria-label="Add filter" onClick={addFilter}><CompassIcon name="plus" size={18} /></button>
        </div>
      </div>
      {groups.filter(group => group.errors.length).map(group => (
        <div data-field-prefix={group.field} key={group.field}>
          <ul className="field-errors" role="alert">{group.errors.map(error => <li key={error}>{error}</li>)}</ul>
          <p className="criteria-hint">Remove the affected filter and add it again to clear any saved alternate names.</p>
        </div>
      ))}
    </div>
  )
}
