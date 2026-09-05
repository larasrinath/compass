import type { ReactNode } from 'react'
import { splitLocations } from '../locations'
import type { BriefRecord } from '../api/client'
import { CompassIcon } from './CompassIcon'

export function ResultHeader({ brief, titleId, fallback, subtitle, onEdit, compact = false, action }: {
  brief?: BriefRecord | null
  titleId: string
  fallback: string
  subtitle: string
  onEdit?: () => void
  compact?: boolean
  action?: ReactNode
}) {
  const title = brief?.target_titles?.map(item => item.term).join(' · ') || brief?.required_credentials?.map(item => item.term).join(' · ') || fallback
  const criteria = brief ? [...(brief.target_titles ?? []).map(item => item.term), ...(brief.required_skills ?? []).map(item => item.term),
    ...(brief.optional_skills ?? []).map(item => `${item.term} · nice-to-have`),
    ...(brief.required_experience_months == null ? [] : [`${Number((brief.required_experience_months / 12).toFixed(2))}+ years`]),
    ...splitLocations(brief.location), ...(brief.industries ?? []).map(item => item.term), ...(brief.required_credentials ?? []).map(item => item.term)] : []
  return <header className={`results-header${compact ? ' results-header-compact' : ''}`}>
    {onEdit && !compact ? <button type="button" className="compass-back" onClick={onEdit}><CompassIcon name="back" size={16} /> Search criteria</button> : null}
    <div className="results-title-row">
      <div className="results-title"><h1 id={titleId} title={title}>{title}</h1><p>{subtitle}</p></div>
      <div className="results-header-actions">
        <details className="trust-markers"><summary>Trust markers</summary><div>
          <strong>Read the evidence behind a match</strong>
          <p>Evidence found means saved profile text matches a criterion. It does not independently verify a claim.</p>
          <p>Not checked means the necessary evidence is missing. No exact match means the saved text did not match your terms or aliases.</p>
          <p>Confidence measures evidence availability, not a person’s suitability.</p>
        </div></details>
        {onEdit ? <button type="button" className="quiet-action" onClick={onEdit}>Adjust criteria</button> : null}
      </div>
    </div>
    <div className="results-context-row">
      {criteria.length ? <ul className="results-criteria" aria-label="Saved role criteria">{[...new Set(criteria)].map(item => <li key={item}>{item}</li>)}</ul> : null}
      {action}
    </div>
  </header>
}
