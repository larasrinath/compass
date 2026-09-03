import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('..', import.meta.url))
const app = readFileSync(`${root}/src/App.tsx`, 'utf8')
const hook = readFileSync(`${root}/src/hooks/useJobEvents.ts`, 'utf8')

test('queue events reconnect natively and preserve ordered event types', () => {
  assert.match(hook, /new EventSource\('\/api\/events'\)/)
  assert.match(hook, /'snapshot'/)
  assert.match(hook, /\['job', 'progress'\]/)
  assert.match(hook, /source\.close\(\)/)
  assert.doesNotMatch(hook, /setInterval|setTimeout/)
})

test('queue UI exposes position, pause deadline, and explicit resume', () => {
  assert.match(app, /Queue status/)
  assert.match(app, /Position \$\{job\.position/)
  assert.match(app, /queue\.resume_at/)
  assert.match(app, /Resume queue/)
  assert.match(app, /Events \{queue\.connected \? 'connected' : 'reconnecting'\}/)
})
