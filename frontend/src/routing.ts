export type AppRoute =
  | { view: 'brief' | 'search' | 'ranked' | 'saved'; candidateId: null }
  | { view: 'candidate'; candidateId: string }

export function parseAppRoute(pathname: string): AppRoute {
  const candidateMatch = pathname.match(/^\/candidates\/([^/]+)\/?$/)
  if (candidateMatch) {
    try {
      return {
        view: 'candidate',
        candidateId: decodeURIComponent(candidateMatch[1]),
      }
    } catch {
      return { view: 'brief', candidateId: null }
    }
  }
  if (pathname === '/saved') return { view: 'saved', candidateId: null }
  if (pathname === '/search') return { view: 'search', candidateId: null }
  if (pathname === '/candidates') return { view: 'ranked', candidateId: null }
  return { view: 'brief', candidateId: null }
}

export function pathForRoute(route: AppRoute): string {
  if (route.view === 'candidate') {
    return `/candidates/${encodeURIComponent(route.candidateId)}`
  }
  if (route.view === 'ranked') return '/candidates'
  if (route.view === 'saved') return '/saved'
  if (route.view === 'search') return '/search'
  return '/brief'
}
