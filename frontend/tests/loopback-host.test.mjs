import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { createServer } from 'vite'

import { assertLoopbackHost } from '../loopback-host.mjs'

const frontendRoot = fileURLToPath(new URL('..', import.meta.url))
const vite = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))

test('accepts explicit IPv4 and IPv6 loopback hosts', () => {
  assert.equal(assertLoopbackHost('127.0.0.2', 'test'), '127.0.0.2')
  assert.equal(assertLoopbackHost('[::1]', 'test'), '::1')
})

test('rejects wildcard and implicit CLI hosts', () => {
  for (const host of ['0.0.0.0', '::', true, undefined]) {
    assert.throws(() => assertLoopbackHost(host, 'test'), /explicit loopback/)
  }
})

test('Vite startup rejects a non-loopback CLI override', () => {
  const result = spawnSync(process.execPath, [vite, '--host', '0.0.0.0'], {
    cwd: frontendRoot,
    encoding: 'utf8',
    timeout: 10_000,
  })

  assert.notEqual(result.status, 0)
  assert.equal(result.signal, null)
  assert.match(
    `${result.stdout}\n${result.stderr}`,
    /Vite development server host must be an explicit loopback address/,
  )
})

test('Vite starts with its configured loopback host', async () => {
  const server = await createServer({
    root: frontendRoot,
    logLevel: 'silent',
    server: { host: '127.0.0.1', port: 0, strictPort: false },
  })

  try {
    await server.listen()
    const address = server.httpServer?.address()
    assert.notEqual(address, null)
    assert.equal(typeof address, 'object')
    assert.equal(address.address, '127.0.0.1')
  } finally {
    await server.close()
  }
})
