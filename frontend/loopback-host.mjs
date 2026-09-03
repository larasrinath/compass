function normalizeHost(host) {
  if (typeof host !== 'string') return null

  let candidate = host.trim().toLowerCase()
  if (candidate === 'localhost') return candidate
  if (candidate.startsWith('[') || candidate.endsWith(']')) {
    if (!(candidate.startsWith('[') && candidate.endsWith(']'))) return null
    candidate = candidate.slice(1, -1)
  }
  if (candidate === '::1' || candidate === '0:0:0:0:0:0:0:1') return '::1'

  const octets = candidate.split('.')
  if (
    octets.length === 4 &&
    octets.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255) &&
    Number(octets[0]) === 127
  ) {
    return octets.map(Number).join('.')
  }
  return null
}

export function assertLoopbackHost(host, surface) {
  const normalized = normalizeHost(host)
  if (normalized === null) {
    throw new Error(
      `${surface} host must be an explicit loopback address; received ${String(host)}`,
    )
  }
  return normalized
}

function validatedPort(rawPort, surface) {
  if (!/^\d+$/.test(String(rawPort))) {
    throw new Error(`${surface} port must be an integer; received ${rawPort}`)
  }
  const port = Number(rawPort)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`${surface} port must be between 1 and 65535; received ${rawPort}`)
  }
  return port
}

function urlHost(host) {
  return host.includes(':') ? `[${host}]` : host
}

export function backendProxyTarget(env = process.env) {
  const surface = 'Dashboard backend proxy'
  const host = assertLoopbackHost(env.HOST ?? '127.0.0.1', surface)
  const port = validatedPort(env.PORT ?? '8787', surface)
  return `http://${urlHost(host)}:${port}`
}

export function frontendBinding(env = process.env) {
  const surface = 'Vite frontend'
  const host = assertLoopbackHost(env.FRONTEND_HOST ?? '127.0.0.1', surface)
  const port = validatedPort(env.FRONTEND_PORT ?? '5173', surface)
  const authority = host.includes(':') ? `[${host}]` : host
  return { host, port, origin: `http://${authority}:${port}` }
}

export function loopbackOnlyPlugin(expectedFrontend) {
  return {
    name: 'linkedin-dashboard-loopback-only',
    configResolved(config) {
      const serverHost = assertLoopbackHost(
        config.server.host,
        'Vite development server',
      )
      const previewHost = assertLoopbackHost(
        config.preview.host,
        'Vite preview server',
      )
      if (
        expectedFrontend !== undefined &&
        (serverHost !== expectedFrontend.host ||
          config.server.port !== expectedFrontend.port ||
          previewHost !== expectedFrontend.host ||
          config.preview.port !== expectedFrontend.port)
      ) {
        throw new Error(
          'Vite listener must match configured FRONTEND_HOST and FRONTEND_PORT',
        )
      }
    },
  }
}
