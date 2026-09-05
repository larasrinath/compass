import { createContext, useContext } from 'react'

export type LearnDestination = { name: 'learn'; chapter?: string } | { name: 'home' }
export const LearnNavigationContext = createContext<((next: LearnDestination) => void) | null>(null)

export function useLearnNavigation() {
  const navigate = useContext(LearnNavigationContext)
  if (!navigate) throw new Error('Learning pages require a navigation provider')
  return { navigate }
}
