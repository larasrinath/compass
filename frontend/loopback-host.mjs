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

export function loopbackOnlyPlugin() {
  return {
    name: 'linkedin-dashboard-loopback-only',
    configResolved(config) {
      assertLoopbackHost(config.server.host, 'Vite development server')
      assertLoopbackHost(config.preview.host, 'Vite preview server')
    },
  }
}
