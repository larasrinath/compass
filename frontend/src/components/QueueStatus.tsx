import { useMutation } from '@tanstack/react-query'
import { resumeQueue } from '../api/client'
import type { ReturnTypeOfJobEvents } from '../hooks/useJobEvents'

export function QueueStatus({ queue }: { queue: ReturnTypeOfJobEvents }) {
  const resume = useMutation({ mutationFn: resumeQueue })

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
            <strong>Queue paused · {queue.pause_reason ?? 'operator hold'}</strong>
            <p>
              {queue.resume_at
                ? `Recommended resume after ${new Date(queue.resume_at).toLocaleString()}.`
                : 'Resolve the local MCP condition, then resume explicitly.'}
            </p>
          </div>
          <button
            disabled={resume.isPending}
            onClick={() => resume.mutate()}
            type="button"
          >
            {resume.isPending ? 'Resuming…' : 'Resume queue'}
          </button>
        </div>
      ) : null}
      <div className="queue-list">
        {queue.jobs.length === 0 ? (
          <p className="queue-empty">No queued work. The browser slot is free.</p>
        ) : (
          queue.jobs.map((job) => (
            <article className="queue-job" key={job.id}>
              <div>
                <strong>{job.kind.replaceAll('_', ' ')}</strong>
                <span>{job.state}</span>
              </div>
              <p>
                {job.state === 'queued' || job.state === 'pending'
                  ? `Position ${job.position ?? '—'} of ${job.depth}`
                  : job.percent == null
                    ? 'Running LinkedIn read'
                    : `Progress ${Math.round(job.percent)}%`}
              </p>
            </article>
          ))
        )}
      </div>
    </aside>
  )
}
