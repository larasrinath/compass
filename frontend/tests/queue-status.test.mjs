import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { reduceQueueEvent } from '../src/hooks/jobEventState.ts'

const root = fileURLToPath(new URL('..', import.meta.url))
const queueComponent = readFileSync(
  `${root}/src/components/QueueStatus.tsx`,
  'utf8',
)
const hook = readFileSync(`${root}/src/hooks/useJobEvents.ts`, 'utf8')

test('queue events reconnect natively and preserve ordered event types', () => {
  assert.match(hook, /new EventSource\('\/api\/events'\)/)
  assert.match(hook, /'snapshot'/)
  assert.match(hook, /\['job', 'progress'\]/)
  assert.match(hook, /source\.close\(\)/)
  assert.doesNotMatch(hook, /setInterval|setTimeout/)
})

test('canonical snapshots reconcile positions after a middle cancellation', () => {
  const job = (id, state, position, depth) => ({
    id,
    kind: 'search_people',
    state,
    position,
    depth,
    error_class: null,
    correlation_id: `correlation-${id}`,
  })
  const before = {
    state: 'active',
    pause_reason: null,
    resume_at: null,
    counts: { running: 1, queued: 2 },
    jobs: [
      job('job-1', 'running', null, 3),
      job('job-2', 'queued', 1, 3),
      job('job-3', 'queued', 2, 3),
    ],
  }

  const withoutMiddle = reduceQueueEvent(
    before,
    'job',
    job('job-2', 'cancelled', null, 2),
  )
  assert.equal(withoutMiddle.jobs.find(({ id }) => id === 'job-3').position, 2)

  const reconciled = reduceQueueEvent(withoutMiddle, 'snapshot', {
    state: 'active',
    pause_reason: null,
    resume_at: null,
    counts: { running: 1, queued: 1, cancelled: 1 },
    jobs: [job('job-1', 'running', null, 2), job('job-3', 'queued', 1, 2)],
  })

  assert.deepEqual(
    reconciled.jobs.map(({ id, position, depth }) => ({ id, position, depth })),
    [
      { id: 'job-1', position: null, depth: 2 },
      { id: 'job-3', position: 1, depth: 2 },
    ],
  )
})

test('queue UI exposes position, pause deadline, and explicit resume', () => {
  assert.match(queueComponent, /Activity queue/)
  assert.match(queueComponent, /Position \$\{job\.position/)
  assert.match(queueComponent, /queue\.resume_at/)
  assert.match(queueComponent, /Resume queue/)
  assert.match(queueComponent, /Live updates connected/)
})
