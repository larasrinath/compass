import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createServer as createHttpServer } from 'node:http'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { createServer } from 'vite'

import {
  assertLoopbackHost,
  backendProxyTarget,
  frontendBinding,
  loopbackOnlyPlugin,
} from '../loopback-host.mjs'

const frontendRoot = fileURLToPath(new URL('..', import.meta.url))
const vite = fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url))

async function unusedPort(host) {
  const server = createHttpServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, host, resolve)
  })
  const address = server.address()
  assert.notEqual(address, null)
  assert.equal(typeof address, 'object')
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
  return address.port
}

test('accepts explicit IPv4 and IPv6 loopback hosts', () => {
  assert.equal(assertLoopbackHost('127.0.0.2', 'test'), '127.0.0.2')
  assert.equal(assertLoopbackHost('[::1]', 'test'), '::1')
  for (const host of ['0:0:0:0:0::1', '0:0::0:1', '::0:1']) {
    assert.equal(assertLoopbackHost(host, 'test'), '::1')
  }
})

test('rejects wildcard and implicit CLI hosts', () => {
  for (const host of [
    '0.0.0.0',
    '::',
    'localhost',
    'LOCALHOST',
    '[127.0.0.1]',
    '::1%lo0',
    '[::1%lo0]',
    '::ffff:127.0.0.1',
    '[::ffff:127.0.0.1]',
    true,
    undefined,
  ]) {
    assert.throws(() => assertLoopbackHost(host, 'test'), /explicit loopback/)
  }
})

test('Vite startup rejects scoped and mapped IPv6 overrides', () => {
  for (const host of ['::1%lo0', '::ffff:127.0.0.1']) {
    const result = spawnSync(process.execPath, [vite, '--host', host], {
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

test('validates frontend binding and brackets its IPv6 origin', () => {
  assert.deepEqual(
    frontendBinding({ FRONTEND_HOST: '127.0.0.2', FRONTEND_PORT: '5174' }),
    { host: '127.0.0.2', port: 5174, origin: 'http://127.0.0.2:5174' },
  )
  assert.deepEqual(
    frontendBinding({ FRONTEND_HOST: '[::1]', FRONTEND_PORT: '5175' }),
    { host: '::1', port: 5175, origin: 'http://[::1]:5175' },
  )
  assert.throws(
    () => frontendBinding({ FRONTEND_HOST: '0.0.0.0', FRONTEND_PORT: '5173' }),
    /explicit loopback/,
  )
  assert.throws(
    () => frontendBinding({ FRONTEND_HOST: '127.0.0.1', FRONTEND_PORT: '0' }),
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

test('Vite startup rejects a frontend port override', () => {
  const result = spawnSync(process.execPath, [vite, '--port', '5199'], {
    cwd: frontendRoot,
    encoding: 'utf8',
    timeout: 10_000,
    env: { ...process.env, FRONTEND_HOST: '127.0.0.1', FRONTEND_PORT: '5173' },
  })

  assert.notEqual(result.status, 0)
  assert.equal(result.signal, null)
  assert.match(
    `${result.stdout}\n${result.stderr}`,
    /must match configured FRONTEND_HOST and FRONTEND_PORT/,
  )
})

test('Vite starts with its configured loopback host', async () => {
  const server = await createServer({
    configFile: false,
    root: frontendRoot,
    logLevel: 'silent',
    plugins: [loopbackOnlyPlugin()],
    server: { host: '127.0.0.1', port: 0, strictPort: false },
    preview: { host: '127.0.0.1' },
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

test('Vite listener agrees with configured IPv4 and IPv6 frontend origins', async () => {
  const original = {
    HOST: process.env.HOST,
    PORT: process.env.PORT,
    FRONTEND_HOST: process.env.FRONTEND_HOST,
    FRONTEND_PORT: process.env.FRONTEND_PORT,
  }
  process.env.HOST = '127.0.0.1'
  process.env.PORT = '8787'

  try {
    for (const configuredHost of ['127.0.0.1', '::1']) {
      const port = await unusedPort(configuredHost)
      process.env.FRONTEND_HOST = configuredHost
      process.env.FRONTEND_PORT = String(port)
      const binding = frontendBinding(process.env)
      const server = await createServer({ root: frontendRoot, logLevel: 'silent' })
      try {
        await server.listen()
        const address = server.httpServer?.address()
        assert.notEqual(address, null)
        assert.equal(typeof address, 'object')
        assert.equal(address.address, binding.host)
        assert.equal(address.port, binding.port)
        const response = await fetch(binding.origin)
        assert.equal(response.status, 200)
      } finally {
        await server.close()
      }
    }
  } finally {
    for (const [key, value] of Object.entries(original)) {
      if (value === undefined) delete process.env[key]
      else process.env[key] = value
    }
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
  const originalFrontendHost = process.env.FRONTEND_HOST
  const originalFrontendPort = process.env.FRONTEND_PORT
  process.env.HOST = '::1'
  process.env.PORT = String(backendAddress.port)
  process.env.FRONTEND_HOST = '127.0.0.1'
  process.env.FRONTEND_PORT = String(await unusedPort('127.0.0.1'))
  let viteServer

  try {
    viteServer = await createServer({
      root: frontendRoot,
      logLevel: 'silent',
    })
    await viteServer.listen()
    const viteAddress = viteServer.httpServer?.address()
    assert.notEqual(viteAddress, null)
    assert.equal(typeof viteAddress, 'object')
    const frontend = frontendBinding(process.env)
    const response = await fetch(`${frontend.origin}/api/probe`)
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
    if (originalFrontendHost === undefined) delete process.env.FRONTEND_HOST
    else process.env.FRONTEND_HOST = originalFrontendHost
    if (originalFrontendPort === undefined) delete process.env.FRONTEND_PORT
    else process.env.FRONTEND_PORT = originalFrontendPort
    await new Promise((resolve, reject) => {
      backend.close((error) => (error ? reject(error) : resolve()))
    })
  }
})
