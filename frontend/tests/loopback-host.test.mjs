import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createServer as createHttpServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { createServer } from 'vite'

import {
  assertLoopbackHost,
  backendProxyTarget,
} from '../loopback-host.mjs'

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

test('formats validated IPv4 and IPv6 backend proxy targets', () => {
  assert.equal(
    backendProxyTarget({ HOST: '127.0.0.2', PORT: '9000' }),
    'http://127.0.0.2:9000',
  )
  assert.equal(
    backendProxyTarget({ HOST: '[::1]', PORT: '9001' }),
    'http://[::1]:9001',
  )
  assert.throws(
    () => backendProxyTarget({ HOST: '0.0.0.0', PORT: '8787' }),
    /explicit loopback/,
  )
  assert.throws(
    () => backendProxyTarget({ HOST: '127.0.0.1', PORT: '65536' }),
    /between 1 and 65535/,
  )
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

test('Vite proxies to a real bracketed IPv6 backend', async () => {
  const backend = createHttpServer((request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' })
    response.end(JSON.stringify({ path: request.url, transport: 'ipv6' }))
  })
  await new Promise((resolve, reject) => {
    backend.once('error', reject)
    backend.listen(0, '::1', resolve)
  })
  const backendAddress = backend.address()
  assert.notEqual(backendAddress, null)
  assert.equal(typeof backendAddress, 'object')

  const originalHost = process.env.HOST
  const originalPort = process.env.PORT
  process.env.HOST = '::1'
  process.env.PORT = String(backendAddress.port)
  let viteServer

  try {
    viteServer = await createServer({
      root: frontendRoot,
      logLevel: 'silent',
      server: { port: 0, strictPort: false },
    })
    await viteServer.listen()
    const viteAddress = viteServer.httpServer?.address()
    assert.notEqual(viteAddress, null)
    assert.equal(typeof viteAddress, 'object')
    const response = await fetch(`http://127.0.0.1:${viteAddress.port}/api/probe`)
    assert.equal(response.status, 200)
    assert.deepEqual(await response.json(), {
      path: '/api/probe',
      transport: 'ipv6',
    })
  } finally {
    if (viteServer !== undefined) await viteServer.close()
    if (originalHost === undefined) delete process.env.HOST
    else process.env.HOST = originalHost
    if (originalPort === undefined) delete process.env.PORT
    else process.env.PORT = originalPort
    await new Promise((resolve, reject) => {
      backend.close((error) => (error ? reject(error) : resolve()))
    })
  }
})
