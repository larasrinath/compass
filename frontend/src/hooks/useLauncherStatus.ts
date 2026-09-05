import { useQuery } from '@tanstack/react-query'

type Status = { managed: boolean; phase: string }

export function useLauncherStatus() {
  return useQuery({
    queryKey: ['launcher'],
    queryFn: async (): Promise<Status | null> => {
      const response = await fetch('/api/launcher')
      if (response.status === 404) return null
      if (!response.ok) throw new Error('Startup status is unavailable.')
      return response.json()
    },
    retry: false,
    refetchInterval: query => query.state.data?.managed ? 2000 : false,
  })
}

