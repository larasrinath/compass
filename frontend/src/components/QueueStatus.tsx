import { useId, useState } from 'react'
import { CompassIcon } from './CompassIcon'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cancelQueuedJob, resumeQueue } from '../api/client'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

export function QueueStatus({ queue }: { queue: ReturnTypeOfJobEvents }) {
  const [expanded, setExpanded] = useState(false)
  const [page, setPage] = useState(1)
  const tasksId = useId()
  const waiting = queue.jobs.filter(job => job.state === 'queued' || job.state === 'pending')
  const runningJobs = queue.jobs.filter(job => job.state === 'running')
  const running = runningJobs[0]
  const pageCount = Math.max(1, Math.ceil(queue.jobs.length / 10))
  const currentPage = Math.min(page, pageCount)
  const labels: Record<string, string> = { search_people: 'Candidate search', get_person_profile: 'Profile download', get_company_profile: 'Company lookup', 'tools/list': 'Connection check' }
  const groups = Object.entries(waiting.reduce<Record<string, number>>((counts, job) => {
    counts[job.kind] = (counts[job.kind] ?? 0) + 1
    return counts
  }, {}))
  const client = useQueryClient()
  const refresh = async () => { await client.invalidateQueries({ queryKey: ['mcp-status'] }) }
  const resume = useMutation({ mutationFn: resumeQueue, onSuccess: refresh })
  const cancel = useMutation({ mutationFn: cancelQueuedJob })
  const reasons: Record<string, string> = {
    TRANSPORT: 'The LinkedIn connector is not reachable. Start it, then use Check connection above.',
    SESSION_REQUIRED: 'Sign in to LinkedIn in the connector browser, then resume downloads.',
    AUTH_REQUIRED: 'Sign in to LinkedIn in the connector browser, then resume downloads.',
    BROWSER_SETUP: 'The connector browser needs attention. Check its window before resuming.',
    BROWSER_BUSY: 'Another task is using the LinkedIn browser. Wait for it to finish before resuming.',
    RATE_LIMITED: 'LinkedIn has limited requests. Wait until the suggested time before resuming.',
    RATE_LIMIT: 'LinkedIn has limited requests. Wait until the suggested time before resuming.',
  }
  if (queue.connected && queue.state === 'active' && queue.jobs.length === 0) return null

  return (
    <aside aria-label="Activity queue" className="queue-compact">
      <div className="queue-overview">
        <div className="queue-summary" role="status">
          <h2>Activity queue</h2>
          <p className="queue-current">
            {queue.state === 'paused' ? 'Paused' : !queue.connected ? 'Reconnecting…' : running
              ? runningJobs.length > 1 ? `Downloading ${runningJobs.length} profiles` : { search_people: 'Finding candidates', get_person_profile: 'Downloading a profile', get_company_profile: 'Looking up a company', 'tools/list': 'Checking connection' }[running.kind] ?? 'Working'
              : waiting.length ? 'Preparing next task' : 'No tasks waiting'}
            {waiting.length > 0 && <span> · {waiting.length.toLocaleString()} waiting</span>}
          </p>
          {groups.length > 0 && <p className="queue-breakdown">{groups.map(([kind, count]) =>
            `${count.toLocaleString()} ${{ search_people: count === 1 ? 'search page' : 'search pages', get_person_profile: count === 1 ? 'profile' : 'profiles', get_company_profile: count === 1 ? 'company lookup' : 'company lookups' }[kind] ?? 'tasks'}`
          ).join(' · ')}</p>}
          {!queue.connected && queue.lastEventAt && <p className="queue-breakdown">Last update {new Date(queue.lastEventAt).toLocaleTimeString()}</p>}
        </div>
        {queue.jobs.length > 0 && <button className="queue-toggle" type="button" aria-expanded={expanded} aria-controls={tasksId}
          onClick={() => { setExpanded(!expanded); setPage(1) }}>
          {expanded ? 'Hide tasks' : 'View tasks'} <CompassIcon name="chevron" size={16} />
        </button>}
      </div>
      {queue.state === 'paused' ? (
        <div className="pause-banner" role="alert">
          <div>
            <strong>Downloads paused</strong>
            <p>{reasons[queue.pause_reason ?? ''] ?? 'Downloads need your attention. Resolve the connector issue, then resume.'}</p>
            <p>
              {queue.resume_at
                ? `Recommended resume after ${new Date(queue.resume_at).toLocaleString()}.`
                : 'Your saved work is available while downloads are paused.'}
            </p>
          </div>
          <button
            disabled={resume.isPending}
            onClick={() => resume.mutate()}
            type="button"
          >
            {resume.isPending ? 'Resuming…' : 'Resume downloads'}
          </button>
        </div>
      ) : null}
      {resume.isError || cancel.isError ? <p className="field-error" role="alert">{resume.error?.message ?? cancel.error?.message}</p> : null}
      {expanded && queue.jobs.length > 0 && <div id={tasksId} className="queue-details">
        <div className="queue-list">
          {queue.jobs.slice((currentPage - 1) * 10, currentPage * 10).map((job) => {
            const canCancel = job.state === 'queued' || job.state === 'pending'
            return <article className="queue-job" key={job.id}>
              <div>
                <span className="queue-task-name">{labels[job.kind] ?? job.kind.replaceAll('_', ' ')}</span>
                <span>{canCancel ? `Position ${job.position ?? '—'}` : queue.state === 'paused' ? 'Paused' : 'Running'}</span>
              </div>
              {canCancel && <button className="queue-cancel" aria-label={`Cancel ${labels[job.kind] ?? 'task'} at position ${job.position ?? 'unknown'}`}
                disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)} type="button">Cancel</button>}
            </article>
          })}
        </div>
        {pageCount > 1 && <nav className="queue-pages" aria-label="Activity pages">
          <span>{(currentPage - 1) * 10 + 1}–{Math.min(currentPage * 10, queue.jobs.length)} of {queue.jobs.length.toLocaleString()} tasks</span>
          <button type="button" disabled={currentPage === 1} onClick={() => setPage(currentPage - 1)} aria-label="Previous tasks"><CompassIcon name="back" size={16} /></button>
          <button type="button" disabled={currentPage === pageCount} onClick={() => setPage(currentPage + 1)} aria-label="Next tasks"><CompassIcon name="arrow" size={16} /></button>
        </nav>}
      </div>}
    </aside>
  )
}
