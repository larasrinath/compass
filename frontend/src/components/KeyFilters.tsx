import { useState } from 'react'
import type { BriefTerm } from '../api/client'
import { CompassIcon } from './CompassIcon'

export function KeyFilters({ skills, credentials, optionalSkills, skillErrors = [], credentialErrors = [], optionalSkillErrors = [], onSkillsChange, onCredentialsChange, onOptionalSkillsChange }: {
  skills: BriefTerm[]
  credentials: BriefTerm[]
  optionalSkills: BriefTerm[]
  skillErrors?: string[]
  credentialErrors?: string[]
  optionalSkillErrors?: string[]
  onSkillsChange: (values: BriefTerm[]) => void
  onCredentialsChange: (values: BriefTerm[]) => void
  onOptionalSkillsChange: (values: BriefTerm[]) => void
}) {
  const [nextTerm, setNextTerm] = useState('')
  const [kind, setKind] = useState('skill')
  const groups = [
    { kind: 'skill', field: 'required_skills', label: 'Skill', values: skills, errors: skillErrors, onChange: onSkillsChange },
    { kind: 'credential', field: 'required_credentials', label: 'Credential', values: credentials, errors: credentialErrors, onChange: onCredentialsChange },
    { kind: 'optional', field: 'optional_skills', label: 'Nice-to-have', values: optionalSkills, errors: optionalSkillErrors, onChange: onOptionalSkillsChange },
  ]

  function addFilter() {
    const term = nextTerm.trim()
    if (!term) return
    const group = groups.find(item => item.kind === kind)!
    if (!group.values.some(value => value.term.localeCompare(term, undefined, { sensitivity: 'accent' }) === 0)) {
      group.onChange([...group.values, { term, aliases: [] }])
    }
    setNextTerm('')
  }

  return (
    <div className="criteria-terms" role="group" aria-labelledby="key-filters-label">
      <span className="criteria-label" id="key-filters-label">Skills & keywords</span>
      <p className="criteria-hint">Skills, credentials and nice-to-haves to look for in each profile.</p>
      <div className="criteria-chips">
        {groups.flatMap(group => group.values.map((value, index) => (
          <div className="criteria-chip" data-field-prefix={group.field} key={`${group.field}-${index}`}>
            <input aria-label={`${group.label} filter ${index + 1}`} value={value.term}
              style={{ width: `${Math.max(4, Math.min(32, value.term.length + 1))}ch` }}
              onChange={event => group.onChange(group.values.map((item, row) => row === index ? { ...item, term: event.target.value } : item))}
              onKeyDown={event => { if (event.key === 'Enter') event.preventDefault() }} />
            {group.kind !== 'skill' ? <span className="criteria-chip-kind">{group.label}</span> : null}
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
            <option value="optional">Nice-to-have</option>
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
