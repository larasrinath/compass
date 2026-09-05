import { useEffect } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

export function LauncherStatus({ phase }: { phase: string }) {
  const client = useQueryClient()
  useEffect(() => {
    if (phase === 'ready') {
      void client.invalidateQueries({ queryKey: ['mcp-status'] })
    }
  }, [phase, client])
  const login = useMutation({
    mutationFn: async () => {
      const response = await fetch('/api/launcher/login', { method: 'POST' })
      if (!response.ok) throw new Error('Could not start sign-in. Try again.')
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['launcher'] }),
  })
  if (phase === 'ready') return null
  const failed = phase === 'login_failed' || phase === 'failed'
  return <div className="connection-strip" role="status">
    <div>
      <strong>{failed ? 'LinkedIn needs your attention' : phase === 'signing_in' ? 'Sign in to LinkedIn' : 'Connecting to LinkedIn…'}</strong>
      <span>{failed ? 'Sign-in did not finish or the connection stopped. Try again; startup details are in .compass/connector.log.' : phase === 'signing_in' ? 'Finish signing in in the LinkedIn window that opens. You can set up your search criteria while you wait.' : 'Compass is starting the connection for you.'}</span>
      {login.isError && <span role="alert">{login.error.message}</span>}
    </div>
    {failed && <button type="button" className="quiet-action" disabled={login.isPending} onClick={() => login.mutate()}>{login.isPending ? 'Opening…' : 'Sign in to LinkedIn'}</button>}
  </div>
}
