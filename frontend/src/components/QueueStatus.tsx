import { useMutation, useQueryClient } from '@tanstack/react-query'
import { cancelQueuedJob, resumeQueue } from '../api/client'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

export function QueueStatus({ queue }: { queue: ReturnTypeOfJobEvents }) {
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
    <aside aria-live="polite" className="queue-compact">
      <div className="section-heading">
        <div>
          <p className="eyebrow">One browser slot</p>
          <h2>Activity queue</h2>
        </div>
        <span className="event-state">
          {queue.connected
            ? 'Live updates connected'
            : `Reconnecting${
                queue.lastEventAt
                  ? ` · last update ${new Date(queue.lastEventAt).toLocaleTimeString()}`
                  : ''
              }`}
        </span>
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
      <div className="queue-list">
        {queue.jobs.length === 0 ? (
          <p className="queue-empty">No queued work. The browser slot is free.</p>
        ) : (
          queue.jobs.map((job) => (
            <article className="queue-job" key={job.id}>
              <div>
                <strong>{{ search_people: 'Candidate search', get_person_profile: 'Profile download', get_company_profile: 'Company lookup', 'tools/list': 'Connection check' }[job.kind] ?? job.kind.replaceAll('_', ' ')}</strong>
                <span>{job.state}</span>
              </div>
              <p>
                {job.state === 'queued' || job.state === 'pending'
                  ? `Position ${job.position ?? '—'} of ${job.depth}`
                  : job.percent == null
                    ? 'Running LinkedIn read'
                    : `Progress ${Math.round(job.percent)}%`}
              </p>
              {job.state === 'queued' || job.state === 'pending' ? <button className="quiet-action" disabled={cancel.isPending} onClick={() => cancel.mutate(job.id)} type="button">Cancel queued task</button> : null}
            </article>
          ))
        )}
      </div>
    </aside>
  )
}
