import { useMemo, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type {
  BriefInput,
  BriefRecord,
  BriefTerm,
  SessionRecord,
} from '../api/client'
import { ApiError, saveBrief } from '../api/client'
import { CompassIcon } from '../components/CompassIcon'
import { TermEditor } from '../components/TermEditor'
import { defaultSearchKeywords, readSearchSettings, saveSearchSettings } from '../searchSettings'
import { SearchSettingsEditor } from '../components/SearchSettingsEditor'
import { KeyFilters } from '../components/KeyFilters'
import { focusBriefError } from './briefErrorFocus'

const EMPTY_FORM: Omit<BriefInput, 'session_id'> = {
  job_description: '',
  required_skills: [],
  optional_skills: [],
  required_experience_months: null,
  target_titles: [],
  location: '',
  industries: [],
  required_credentials: [],
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
  onSaved,
  retrievalReady = true,
  queueRevision = 0,
}: {
  session: SessionRecord
  current: BriefRecord | null | undefined
  onSaved?: () => void
  retrievalReady?: boolean
  queueRevision?: number
}) {
  const [searchSettings, setSearchSettings] = useState(() => readSearchSettings(current?.id))
  const [settingsError, setSettingsError] = useState('')
  const queryClient = useQueryClient()
  const alertRef = useRef<HTMLDivElement>(null)
  const initial = useMemo(
    () =>
      current
        ? {
            job_description: current.job_description,
            required_skills: current.required_skills,
            optional_skills: current.optional_skills,
            required_experience_months:
              current.required_experience_months ?? null,
            target_titles: current.target_titles,
            location: current.location,
            industries: current.industries,
            required_credentials: current.required_credentials ?? [],
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
  const [requiredExperienceMonths, setRequiredExperienceMonths] = useState<
    number | null
  >(initial.required_experience_months)
  const [titles, setTitles] = useState<BriefTerm[]>(initial.target_titles)
  const [location, setLocation] = useState(initial.location)
  const [industries, setIndustries] = useState<BriefTerm[]>(initial.industries)
  const [credentials, setCredentials] = useState<BriefTerm[]>(
    initial.required_credentials,
  )
  const [positive, setPositive] = useState(initial.positive_keywords.join('\n'))
  const [negative, setNegative] = useState(initial.negative_keywords.join('\n'))
  const tone = initial.message_tone
  const [dirty, setDirty] = useState(false)
  const [step, setStep] = useState<'describe' | 'review'>(current ? 'review' : 'describe')
  const positiveSet = new Set(parseKeywords(positive).map((term) => term.normalize('NFKC').toLowerCase()))
  const conflicts = parseKeywords(negative).filter((term) => positiveSet.has(term.normalize('NFKC').toLowerCase()))

  const mutation = useMutation({
    mutationFn: (payload: BriefInput) => saveBrief(payload, Boolean(current)),
    onSuccess: async (saved) => {
      try { saveSearchSettings(saved.id, searchSettings) }
      catch { setSettingsError('Your brief was saved, but search options could not be stored. Enable browser storage and save again.'); return }
      setSettingsError('')
      setDirty(false)
      await queryClient.invalidateQueries({ queryKey: ['brief', session.id] })
      onSaved?.()
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

  const editTerms = (setter: (values: BriefTerm[]) => void) => (values: BriefTerm[]) => {
    setter(values)
    markDirty()
  }
  const experienceLabel = requiredExperienceMonths === null ? 'Any'
    : `${Number((requiredExperienceMonths / 12).toFixed(2))}+ years`

  if (step === 'describe') return (
    <section className="compass-start" aria-labelledby="brief-title">
      <p className="compass-kicker">Candidate Compass</p>
      <h1 id="brief-title">Who are you looking for?</h1>
      <p className="compass-start-copy">Start with the role you have in mind. You’ll review everything before searching.</p>
      <form className="prompt-composer" onSubmit={event => { event.preventDefault(); if (description.trim()) { setStep('review'); requestAnimationFrame(() => document.getElementById('brief-title')?.focus()) } }}>
        <label className="sr-only" htmlFor="job-description">Job description</label>
        <textarea id="job-description" placeholder="e.g. A backend engineer with payments experience, Go and PostgreSQL, based in Berlin…" required rows={5} value={description} onChange={event => { setDescription(event.target.value); markDirty() }} />
        <div className="prompt-composer-footer"><span>Nothing searched yet — confirm your criteria first.</span><button className="primary-action" disabled={!description.trim()} type="submit">Set up search <CompassIcon name="arrow" size={16} /></button></div>
      </form>
      <p className="prompt-guidance">A role title, key skills, and location are a good place to start.</p>
      {current ? <button type="button" className="compass-back" onClick={() => setStep('review')}>Return to your criteria <CompassIcon name="arrow" size={16} /></button> : null}
    </section>
  )

  return (
    <section aria-labelledby="brief-title" className="compass-setup">
      <button className="compass-back" type="button" onClick={() => setStep('describe')}><CompassIcon name="back" size={18} /> Back to role description</button>
      <header className="compass-setup-heading">
        <h1 id="brief-title" tabIndex={-1}>Here’s what I’ll look for</h1>
        <blockquote>{description}</blockquote>
      </header>
      {settingsError ? <p className="form-error" role="alert">{settingsError}</p> : null}
      {mutation.isError ? <div className="form-error" ref={alertRef} role="alert" tabIndex={-1}><strong>Brief was not saved.</strong><span>{mutation.error.message}</span></div> : null}
      {conflicts.length > 0 ? <div className="form-error" role="alert"><strong>Some keywords appear in both lists.</strong><span>Remove {conflicts.map(term => `“${term}”`).join(', ')} from either Positive keywords or Exclusions before saving.</span></div> : null}
      <form className="compass-criteria-form" onChange={markDirty} onSubmit={event => {
        event.preventDefault()
        if (conflicts.length) return
        mutation.mutate({ session_id: session.id, job_description: description, required_skills: required, optional_skills: optional,
          required_experience_months: requiredExperienceMonths, target_titles: titles, location, industries, required_credentials: credentials,
          positive_keywords: parseKeywords(positive), negative_keywords: parseKeywords(negative), message_tone: tone })
      }}>
        <div className="criteria-sheet">
          {!current ? <p className="criteria-hint">Add the criteria from your description that you want to check against profiles.</p> : null}
          <TermEditor errors={fieldErrors.target_titles} field="target_titles" label="Target titles" placeholder="Add a role title" onChange={editTerms(setTitles)} values={titles} />
          <KeyFilters skills={required} credentials={credentials} optionalSkills={optional} optionalSkillErrors={fieldErrors.optional_skills} onOptionalSkillsChange={editTerms(setOptional)} skillErrors={fieldErrors.required_skills} credentialErrors={fieldErrors.required_credentials} onSkillsChange={editTerms(setRequired)} onCredentialsChange={editTerms(setCredentials)} />
          <div data-field-prefix="required_experience_months">
            <span className="criteria-label" id="experience-label">Minimum experience</span>
            <div className="experience-stepper" role="group" aria-labelledby="experience-label">
              <button type="button" aria-label="Decrease minimum experience by one year" disabled={requiredExperienceMonths === null} onClick={() => { const years = Math.ceil((requiredExperienceMonths ?? 0) / 12) - 1; setRequiredExperienceMonths(years > 0 ? years * 12 : null); markDirty() }}><CompassIcon name="minus" size={18} /></button>
              <output aria-live="polite">{experienceLabel}</output>
              <button type="button" aria-label="Increase minimum experience by one year" onClick={() => { setRequiredExperienceMonths((Math.floor((requiredExperienceMonths ?? 0) / 12) + 1) * 12); markDirty() }}><CompassIcon name="plus" size={18} /></button>
            </div>
          </div>
          <div className="criteria-columns">
            <label className="field"><span>Location</span><input placeholder="Any location" value={location} onChange={event => setLocation(event.target.value)} /></label>
            <TermEditor errors={fieldErrors.industries} field="industries" label="Industries" placeholder="Any industry" onChange={editTerms(setIndustries)} values={industries} />
          </div>
          <section className="criteria-optional" aria-labelledby="optional-title">
            <h2 id="optional-title">Optional preferences</h2>
            <div className="criteria-optional-fields">
              <label className="field" data-field-prefix="positive_keywords"><span>Positive keywords</span><textarea value={positive} onChange={event => setPositive(event.target.value)} rows={2} placeholder="One per line, or separated by commas" />{fieldErrors.positive_keywords?.map(error => <span className="field-error" key={error} role="alert">{error}</span>)}</label>
              <label className="field" data-field-prefix="negative_keywords"><span>Exclusions / negative keywords</span><textarea value={negative} onChange={event => setNegative(event.target.value)} rows={2} placeholder="One per line, or separated by commas" />{fieldErrors.negative_keywords?.map(error => <span className="field-error" key={error} role="alert">{error}</span>)}</label>
              <SearchSettingsEditor sessionId={session.id} value={searchSettings} onChange={value => { setSearchSettings(value); markDirty() }} suggestedKeywords={defaultSearchKeywords({ target_titles: titles, required_skills: required, required_credentials: credentials, positive_keywords: parseKeywords(positive) })} retrievalReady={retrievalReady} queueRevision={queueRevision} />
            </div>
          </section>
        </div>
        <div className="criteria-footer">
          <span aria-live="polite">{mutation.isSuccess && !dirty ? `Version ${mutation.data.version} saved. No search was started.` : dirty ? 'Unsaved changes' : current ? `Saved criteria · version ${current.version}` : 'Next: confirm your LinkedIn search'}</span>
          <button className="primary-action" disabled={mutation.isPending || conflicts.length > 0} type="submit">{mutation.isPending ? 'Saving…' : onSaved ? 'Continue to search' : current ? 'Save new version' : 'Save brief'}<CompassIcon name="arrow" size={16} /></button>
        </div>
      </form>
    </section>
  )
}
