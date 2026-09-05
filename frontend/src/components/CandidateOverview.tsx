import { useState, type ReactNode } from 'react'
import { getCandidateSection, type CandidateDetail, type ParsedFieldRecord } from '../api/client'
import { CompassIcon } from './CompassIcon'

function sectionLabel(name: string) {
  return name === 'main_profile' ? 'Profile overview' : name.replaceAll('_', ' ').replace(/^./, char => char.toUpperCase())
}

function SavedSection({ candidateId, name, savedAt, onOpen }: { candidateId: string; name: string; savedAt: string; onOpen: () => void }) {
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  async function download() {
    setSaving(true)
    setError('')
    try {
      const section = await getCandidateSection(candidateId, name)
      const url = URL.createObjectURL(new Blob([section.raw_text], { type: 'text/plain;charset=utf-8' }))
      const link = document.createElement('a')
      link.href = url
      link.download = `compass-${candidateId}-${name}.txt`
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      setError('This saved section could not be downloaded. Try again.')
    } finally { setSaving(false) }
  }
  const date = new Date(savedAt)
  return <li className="profile-file">
    <p>{sectionLabel(name)}</p>
    <small>Saved profile text{Number.isNaN(date.getTime()) ? '' : ` · ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`}</small>
    <div className="profile-file-actions">
      <button className="quiet-action" type="button" onClick={onOpen}>View source <CompassIcon name="arrow" size={14} /></button>
      <button className="quiet-action" type="button" disabled={saving} onClick={() => void download()}><CompassIcon name="download" size={14} />{saving ? 'Saving…' : 'Download text'}</button>
    </div>
    {error ? <p className="field-error" role="alert">{error}</p> : null}
  </li>
}

export function CandidateOverview({ candidate, rankingUnlocked, onSourceOpen, onCompare, comparing, comparisonFull, scoreSummary }: {
  candidate: CandidateDetail
  scoreSummary?: ReactNode
  rankingUnlocked: boolean
  onSourceOpen: (section: string, fieldId?: string) => void
  onCompare?: () => void
  comparing?: boolean
  comparisonFull?: boolean
}) {
  const fields = candidate.fields.filter(field => field.provenance_available && field.value)
  const field = (key: string) => fields.find(item => item.field_key === key)?.value
  const name = candidate.display_name || candidate.username
  const headline = field('headline') || candidate.score?.headline
  const jobs = new Map<number, Record<string, ParsedFieldRecord>>()
  for (const item of fields) {
    const match = item.field_key.match(/^experience\.(\d+)\.(title|company|dates|description)$/)
    if (!match) continue
    const index = Number(match[1])
    jobs.set(index, { ...jobs.get(index), [match[2]]: item })
  }
  const claims = rankingUnlocked ? (candidate.signals ?? []).flatMap(signal => signal.claims) : []
  const strengths = claims.filter(claim => claim.verdict === 'matched')
  const questions = claims.filter(claim => claim.verdict !== 'matched')
  return <>
    <header className="profile-heading">
      <span className="profile-initials" aria-hidden="true">{name.split(/\s+/).slice(0, 2).map(word => word[0]).join('')}</span>
      <div>
        <h1 id="candidate-title">{name}</h1>
        {headline ? <p>{headline}</p> : null}
        <small>{field('location') ? <><CompassIcon name="pin" size={14} />{field('location')}<span>·</span></> : null}{Object.keys(candidate.available_sections).length ? 'Profile saved locally' : 'Profile not downloaded'}</small>
      </div>
    </header>
    <div className="profile-overview">
      {scoreSummary}
      <div className="profile-actions">
        <a className="primary-action" href={candidate.profile_url} target="_blank" rel="noreferrer">Open LinkedIn profile <CompassIcon name="arrow" size={16} /></a>
        {onCompare && rankingUnlocked && candidate.score ? <button className="quiet-action" type="button" aria-pressed={comparing} disabled={!comparing && comparisonFull} onClick={onCompare}><CompassIcon name="compare" size={16} />{comparing ? 'Comparing' : 'Compare'}</button> : null}
      </div>
      {!comparing && comparisonFull ? <p className="profile-muted">Three people selected. Remove one from comparison to add this person.</p> : null}
      <section className="profile-section" aria-labelledby="profile-fit-title">
        <h2 id="profile-fit-title">Why they might fit</h2>
        {strengths.length ? <ul className="profile-observations">{strengths.map(claim => {
          const evidence = claim.evidence.find(item => item.availability.state === 'available')
          return <li key={claim.id}>
            <div className="profile-observation-heading"><p>{claim.display_term}</p><span className="profile-trust">Profile mention</span></div>
            {evidence ? <><p className="profile-muted">“{evidence.snippet}” · {sectionLabel(evidence.section_name)}</p><button className="profile-text-action" type="button" onClick={() => onSourceOpen(evidence.section_name, evidence.id)}>View evidence <CompassIcon name="arrow" size={14} /></button></> : <p className="profile-muted">Supporting source is unavailable. Check the original profile.</p>}
          </li>
        })}</ul> : <p className="profile-muted">{rankingUnlocked ? 'No supporting matches recorded yet. Review the saved profile below.' : 'Review the candidate pool to compare this profile with your criteria.'}</p>}
      </section>
      {questions.length ? <section className="profile-section" aria-labelledby="profile-check-title">
        <h2 id="profile-check-title">Worth checking</h2>
        <ul className="profile-observations profile-questions">{questions.map(claim => <li key={claim.id}>
          <div className="profile-observation-heading"><p>{claim.display_term}</p><span className="profile-trust">{claim.verdict === 'unknown' ? 'Not yet checked' : 'Needs review'}</span></div>
          <p className="profile-muted">{claim.verdict === 'unknown' ? 'The saved sections do not provide enough evidence to check this criterion.' : claim.verdict === 'not_matched' ? 'No exact match in the searched text. This does not establish that the qualification is missing.' : 'The saved evidence conflicts with this criterion. Review the source before deciding.'}</p>
        </li>)}</ul>
      </section> : null}
      <section className="profile-section" aria-labelledby="profile-career-title">
        <h2 id="profile-career-title"><CompassIcon name="career" size={16} />Career history</h2>
        {jobs.size ? <ol className="profile-timeline">{[...jobs].sort(([a], [b]) => a - b).map(([index, job]) => <li key={index}>
          <p>{job.title?.value || 'Role not listed'}{job.company ? ` · ${job.company.value}` : ''}</p>
          {job.dates ? <small>{job.dates.value}</small> : null}
        </li>)}</ol> : <p className="profile-muted">{candidate.available_sections.experience ? 'No career entries could be extracted. Open the saved experience text below.' : 'Career history has not been downloaded yet.'}</p>}
      </section>
      <section className="profile-section" aria-labelledby="profile-files-title">
        <h2 id="profile-files-title"><CompassIcon name="folder" size={16} />Evidence & downloads</h2>
        <p className="profile-muted">Saved sections stay on this computer. Download a text copy to keep separately.</p>
        {Object.keys(candidate.available_sections).length ? <ul className="profile-files">{Object.entries(candidate.available_sections).map(([section, info]) => <SavedSection key={section} candidateId={candidate.id} name={section} savedAt={info.retrieved_at} onOpen={() => onSourceOpen(section)} />)}</ul> : <p className="profile-muted">No profile sections have been saved yet.</p>}
      </section>
    </div>
  </>
}
