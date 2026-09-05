export type AppRoute =
  | { view: 'learn'; candidateId: null; chapter: string | null }
  | { view: 'brief' | 'search' | 'ranked' | 'saved'; candidateId: null }
  | { view: 'candidate'; candidateId: string }

export function parseAppRoute(pathname: string): AppRoute {
  if (/^\/how-it-works\/?$/.test(pathname)) return { view: 'learn', candidateId: null, chapter: null }
  const chapterMatch = pathname.match(/^\/how-it-works\/([^/]+)\/?$/)
  if (chapterMatch) {
    try { return { view: 'learn', candidateId: null, chapter: decodeURIComponent(chapterMatch[1]) } }
    catch { return { view: 'learn', candidateId: null, chapter: null } }
  }
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
  if (route.view === 'learn') return route.chapter ? `/how-it-works/${encodeURIComponent(route.chapter)}` : '/how-it-works'
  if (route.view === 'candidate') {
    return `/candidates/${encodeURIComponent(route.candidateId)}`
  }
  if (route.view === 'ranked') return '/candidates'
  if (route.view === 'saved') return '/saved'
  if (route.view === 'search') return '/search'
  return '/brief'
}
