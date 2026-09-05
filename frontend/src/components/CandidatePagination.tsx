import { CompassIcon } from './CompassIcon'

export const CANDIDATES_PER_PAGE = 30

export function CandidatePagination({ page, pageCount, onChange, bottom = false }: {
  page: number
  pageCount: number
  onChange: (page: number) => void
  bottom?: boolean
}) {
  if (pageCount <= 1) return null
  return <nav className="candidate-pagination" aria-label={bottom ? 'Candidate pages, bottom' : 'Candidate pages'}>
    <button className="quiet-action" type="button" disabled={page === 1} onClick={() => onChange(page - 1)}><CompassIcon name="back" size={16} />Previous</button>
    <span aria-live={bottom ? undefined : 'polite'}>Page {page} of {pageCount}</span>
    <button className="quiet-action" type="button" disabled={page === pageCount} onClick={() => onChange(page + 1)}>Next<CompassIcon name="arrow" size={16} /></button>
  </nav>
}
