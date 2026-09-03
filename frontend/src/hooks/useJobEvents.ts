import { useEffect, useState } from 'react'
import type { QueueJob, QueueSnapshot } from '../api/client'
import { reduceQueueEvent } from './jobEventState.ts'

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

export function useJobEvents() {
  const [snapshot, setSnapshot] = useState<QueueSnapshot>(EMPTY_SNAPSHOT)
  const [connected, setConnected] = useState(false)
  const [revision, setRevision] = useState(0)
  const [lastEventAt, setLastEventAt] = useState<string | null>(null)

  useEffect(() => {
    const source = new EventSource('/api/events')

    source.addEventListener('open', () => setConnected(true))
    source.addEventListener('error', () => setConnected(false))
    source.addEventListener('snapshot', (raw) => {
      const incoming = parseEvent<QueueSnapshot>(raw as MessageEvent<string>)
      if (incoming) {
        setSnapshot((current) => reduceQueueEvent(current, 'snapshot', incoming))
        setLastEventAt(new Date().toISOString())
        setRevision((current) => current + 1)
      }
    })
    source.addEventListener('queue', (raw) => {
      const incoming = parseEvent<Partial<QueueSnapshot>>(
        raw as MessageEvent<string>,
      )
      if (incoming) {
        setSnapshot((current) => reduceQueueEvent(current, 'queue', incoming))
        setLastEventAt(new Date().toISOString())
        setRevision((current) => current + 1)
      }
    })
    for (const eventName of ['job', 'progress'] as const) {
      source.addEventListener(eventName, (raw) => {
        const incoming = parseEvent<QueueJob>(raw as MessageEvent<string>)
        if (!incoming?.id) return
        setSnapshot((current) =>
          reduceQueueEvent(current, eventName, incoming),
        )
        setLastEventAt(new Date().toISOString())
        setRevision((current) => current + 1)
      })
    }

    return () => source.close()
  }, [])

  return { ...snapshot, connected, revision, lastEventAt }
}

export type ReturnTypeOfJobEvents = ReturnType<typeof useJobEvents>
