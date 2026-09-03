import { useMemo, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type {
  BriefInput,
  BriefRecord,
  BriefTerm,
  SessionRecord,
} from '../api/client'
import { ApiError, saveBrief } from '../api/client'
import { TermEditor } from '../components/TermEditor'
import { focusBriefError } from './briefErrorFocus'

const EMPTY_FORM: Omit<BriefInput, 'session_id'> = {
  job_description: '',
  required_skills: [],
  optional_skills: [],
  target_titles: [],
  location: '',
  industries: [],
  positive_keywords: [],
  negative_keywords: [],
  message_tone: 'Professional and concise',
}

function parseKeywords(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((term) => term.trim())
    .filter(Boolean)
}

interface ProtectedTermDetail {
  field: string
  term: string
}

function protectedTerms(detail: unknown): ProtectedTermDetail[] {
  if (!detail || typeof detail !== 'object') return []
  const values = (detail as { offending_terms?: unknown }).offending_terms
  if (!Array.isArray(values)) return []
  return values.filter(
    (item): item is ProtectedTermDetail =>
      Boolean(
        item &&
          typeof item === 'object' &&
          typeof (item as ProtectedTermDetail).field === 'string' &&
          typeof (item as ProtectedTermDetail).term === 'string',
      ),
  )
}

export function BriefPage({
  session,
  current,
}: {
  session: SessionRecord
  current: BriefRecord | null | undefined
}) {
  const queryClient = useQueryClient()
  const alertRef = useRef<HTMLDivElement>(null)
  const initial = useMemo(
    () =>
      current
        ? {
            job_description: current.job_description,
            required_skills: current.required_skills,
            optional_skills: current.optional_skills,
            target_titles: current.target_titles,
            location: current.location,
            industries: current.industries,
            positive_keywords: current.positive_keywords,
            negative_keywords: current.negative_keywords,
            message_tone: current.message_tone,
          }
        : EMPTY_FORM,
    [current],
  )
  const [description, setDescription] = useState(initial.job_description)
  const [required, setRequired] = useState<BriefTerm[]>(initial.required_skills)
  const [optional, setOptional] = useState<BriefTerm[]>(initial.optional_skills)
  const [titles, setTitles] = useState<BriefTerm[]>(initial.target_titles)
  const [location, setLocation] = useState(initial.location)
  const [industries, setIndustries] = useState<BriefTerm[]>(initial.industries)
  const [positive, setPositive] = useState(initial.positive_keywords.join('\n'))
  const [negative, setNegative] = useState(initial.negative_keywords.join('\n'))
  const [tone, setTone] = useState(initial.message_tone)
  const [dirty, setDirty] = useState(false)

  const mutation = useMutation({
    mutationFn: (payload: BriefInput) => saveBrief(payload, Boolean(current)),
    onSuccess: async () => {
      setDirty(false)
      await queryClient.invalidateQueries({ queryKey: ['brief', session.id] })
    },
    onError: (error) => {
      const detail = error instanceof ApiError ? error.detail : null
      const first = protectedTerms(detail)[0]?.field.split('.')[0]
      requestAnimationFrame(() => {
        focusBriefError(first, alertRef.current)
      })
    },
  })

  function markDirty() {
    setDirty(true)
  }

  const fieldErrors = protectedTerms(
    mutation.error instanceof ApiError ? mutation.error.detail : null,
  ).reduce<Record<string, string[]>>((result, item) => {
    const field = item.field.split('.')[0] ?? item.field
    result[field] = [
      ...(result[field] ?? []),
      `Remove protected criterion “${item.term}”.`,
    ]
    return result
  }, {})

  return (
    <section aria-labelledby="brief-title" className="workspace-page">
      <div className="page-intro">
        <div>
          <p className="eyebrow">Step 1 · Role brief</p>
          <h1 id="brief-title">Define what you are looking for.</h1>
          <p>
            This saved brief guides later review. Saving never starts a search.
          </p>
        </div>
        <div className="version-card">
          <strong>{current ? `Saved version ${current.version}` : 'Not saved'}</strong>
          <span>
            {dirty
              ? 'Unsaved changes'
              : current
                ? `Saved ${new Date(current.created_at).toLocaleString()}`
                : 'Complete the brief to continue'}
          </span>
        </div>
      </div>

      {mutation.isError ? (
        <div className="form-error" ref={alertRef} role="alert" tabIndex={-1}>
          <strong>Brief was not saved.</strong>
          <span>{mutation.error.message}</span>
        </div>
      ) : null}

      <form
        className="brief-form"
        onChange={markDirty}
        onSubmit={(event) => {
          event.preventDefault()
          mutation.mutate({
            session_id: session.id,
            job_description: description,
            required_skills: required,
            optional_skills: optional,
            target_titles: titles,
            location,
            industries,
            positive_keywords: parseKeywords(positive),
            negative_keywords: parseKeywords(negative),
            message_tone: tone,
          })
        }}
      >
        <fieldset className="form-section">
          <legend>Role context</legend>
          <label className="field field-wide">
            <span>Job description</span>
            <textarea
              id="job-description"
              onChange={(event) => setDescription(event.target.value)}
              required
              rows={8}
              value={description}
            />
          </label>
          <label className="field">
            <span>Location</span>
            <input
              onChange={(event) => setLocation(event.target.value)}
              placeholder="Chicago or United States"
              value={location}
            />
          </label>
          <label className="field">
            <span>Message tone for a later milestone</span>
            <input onChange={(event) => setTone(event.target.value)} value={tone} />
          </label>
        </fieldset>

        <fieldset className="form-section criteria-grid">
          <legend>Criteria</legend>
          <TermEditor
            errors={fieldErrors.target_titles}
            field="target_titles"
            label="Target titles"
            onChange={(values) => {
              setTitles(values)
              markDirty()
            }}
            values={titles}
          />
          <TermEditor
            errors={fieldErrors.required_skills}
            field="required_skills"
            label="Required skills"
            onChange={(values) => {
              setRequired(values)
              markDirty()
            }}
            values={required}
          />
          <TermEditor
            errors={fieldErrors.optional_skills}
            field="optional_skills"
            label="Nice-to-have skills"
            onChange={(values) => {
              setOptional(values)
              markDirty()
            }}
            values={optional}
          />
          <TermEditor
            errors={fieldErrors.industries}
            field="industries"
            label="Industries"
            onChange={(values) => {
              setIndustries(values)
              markDirty()
            }}
            values={industries}
          />
          <label className="field" data-field-prefix="positive_keywords">
            <span>Positive keywords</span>
            <textarea
              onChange={(event) => setPositive(event.target.value)}
              placeholder="One per line"
              rows={4}
              value={positive}
            />
            {fieldErrors.positive_keywords?.map((error) => (
              <span className="field-error" key={error} role="alert">
                {error}
              </span>
            ))}
          </label>
          <label className="field" data-field-prefix="negative_keywords">
            <span>Exclusions / negative keywords</span>
            <textarea
              onChange={(event) => setNegative(event.target.value)}
              placeholder="One per line"
              rows={4}
              value={negative}
            />
            {fieldErrors.negative_keywords?.map((error) => (
              <span className="field-error" key={error} role="alert">
                {error}
              </span>
            ))}
          </label>
        </fieldset>

        <div className="sticky-actions">
          <span aria-live="polite">
            {mutation.isSuccess && !dirty
              ? `Version ${mutation.data.version} saved. No search was started.`
              : dirty
                ? 'Unsaved changes'
                : 'Ready'}
          </span>
          <button className="primary-action" disabled={mutation.isPending} type="submit">
            {mutation.isPending ? 'Saving…' : current ? 'Save new version' : 'Save brief'}
          </button>
        </div>
      </form>
    </section>
  )
}
