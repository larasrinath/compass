import type { QueueJob, QueueSnapshot } from '../api/client'

export type QueueEventName = 'snapshot' | 'queue' | 'job' | 'progress'

function replaceJob(jobs: QueueJob[], incoming: QueueJob): QueueJob[] {
  const existing = jobs.some((job) => job.id === incoming.id)
  const next = existing
    ? jobs.map((job) =>
        job.id === incoming.id ? { ...job, ...incoming } : job,
      )
    : [...jobs, incoming]
  return next.filter((job) =>
    ['pending', 'queued', 'running'].includes(job.state),
  )
}

export function reduceQueueEvent(
  current: QueueSnapshot,
  eventName: QueueEventName,
  incoming: QueueSnapshot | Partial<QueueSnapshot> | QueueJob,
): QueueSnapshot {
  if (eventName === 'snapshot') return incoming as QueueSnapshot
  if (eventName === 'queue') {
    return { ...current, ...(incoming as Partial<QueueSnapshot>) }
  }
  return {
    ...current,
    jobs: replaceJob(current.jobs, incoming as QueueJob),
  }
}
