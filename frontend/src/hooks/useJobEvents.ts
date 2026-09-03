import { useEffect, useState } from 'react'
import type { QueueJob, QueueSnapshot } from '../api/client'

const EMPTY_SNAPSHOT: QueueSnapshot = {
  state: 'active',
  pause_reason: null,
  resume_at: null,
  counts: {},
  jobs: [],
}

function parseEvent<T>(event: MessageEvent<string>): T | null {
  try {
    return JSON.parse(event.data) as T
  } catch {
    return null
  }
}

function replaceJob(jobs: QueueJob[], incoming: QueueJob): QueueJob[] {
  const existing = jobs.find((job) => job.id === incoming.id)
  const next = existing
    ? jobs.map((job) =>
        job.id === incoming.id ? { ...job, ...incoming } : job,
      )
    : [...jobs, incoming]
  return next.filter((job) =>
    ['pending', 'queued', 'running'].includes(job.state),
  )
}

export function useJobEvents() {
  const [snapshot, setSnapshot] = useState<QueueSnapshot>(EMPTY_SNAPSHOT)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const source = new EventSource('/api/events')

    source.addEventListener('open', () => setConnected(true))
    source.addEventListener('error', () => setConnected(false))
    source.addEventListener('snapshot', (raw) => {
      const incoming = parseEvent<QueueSnapshot>(raw as MessageEvent<string>)
      if (incoming) setSnapshot(incoming)
    })
    source.addEventListener('queue', (raw) => {
      const incoming = parseEvent<Partial<QueueSnapshot>>(
        raw as MessageEvent<string>,
      )
      if (incoming) setSnapshot((current) => ({ ...current, ...incoming }))
    })
    for (const eventName of ['job', 'progress']) {
      source.addEventListener(eventName, (raw) => {
        const incoming = parseEvent<QueueJob>(raw as MessageEvent<string>)
        if (!incoming?.id) return
        setSnapshot((current) => ({
          ...current,
          jobs: replaceJob(current.jobs, incoming),
        }))
      })
    }

    return () => source.close()
  }, [])

  return { ...snapshot, connected }
}
