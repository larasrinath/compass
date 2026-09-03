import { useState } from 'react'
import type { BriefTerm } from '../api/client'

export function TermEditor({
  field,
  label,
  values,
  errors = [],
  onChange,
}: {
  field: string
  label: string
  values: BriefTerm[]
  errors?: string[]
  onChange: (values: BriefTerm[]) => void
}) {
  const [nextTerm, setNextTerm] = useState('')

  function addTerm() {
    const term = nextTerm.trim()
    if (!term) return
    if (
      values.some(
        (value) =>
          value.term.localeCompare(term, undefined, { sensitivity: 'accent' }) === 0,
      )
    ) {
      setNextTerm('')
      return
    }
    onChange([...values, { term, aliases: [] }])
    setNextTerm('')
  }

  return (
    <div
      aria-labelledby={`${field}-label`}
      className="term-editor"
      data-field-prefix={field}
      role="group"
    >
      <strong id={`${field}-label`}>{label}</strong>
      <p className="field-help">
        Add each term, then optional aliases. Aliases are comma separated.
      </p>
      {values.map((value, index) => (
        <div className="term-row" key={`term-${index}`}>
          <label className="field">
            <span>
              {label} term {index + 1}
            </span>
            <input
              onChange={(event) => {
                const updated = [...values]
                updated[index] = { ...value, term: event.target.value }
                onChange(updated)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.preventDefault()
              }}
              value={value.term}
            />
          </label>
          <label className="field">
            <span>
              Aliases for {value.term || `${label} term ${index + 1}`}
            </span>
            <input
              onChange={(event) => {
                const updated = [...values]
                updated[index] = {
                  ...value,
                  aliases: event.target.value
                    .split(',')
                    .map((alias) => alias.trimStart()),
                }
                onChange(updated)
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.preventDefault()
              }}
              placeholder="Alias one, alias two"
              value={value.aliases.join(', ')}
            />
          </label>
          <button
            aria-label={`Remove ${value.term || `${label} term ${index + 1}`}`}
            className="quiet-action"
            onClick={() => onChange(values.filter((_, row) => row !== index))}
            type="button"
          >
            Remove
          </button>
        </div>
      ))}
      <div className="term-add-row">
        <label className="field">
          <span>New {label.toLocaleLowerCase()} term</span>
          <input
            onChange={(event) => setNextTerm(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                addTerm()
              }
            }}
            value={nextTerm}
          />
        </label>
        <button className="quiet-action" onClick={addTerm} type="button">
          Add term
        </button>
      </div>
      {errors.length ? (
        <ul className="field-errors" role="alert">
          {errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
