import type { BriefInput } from './api/client'

export interface SearchSettings {
  network: string[]
  companyId: string
}

export const NETWORKS = [
  { value: 'F', label: '1st-degree connections' },
  { value: 'S', label: '2nd-degree connections' },
  { value: 'O', label: '3rd-degree and beyond' },
] as const

export function defaultSearchKeywords(brief: Partial<BriefInput> | null | undefined) {
  if (!brief) return ''
  const titles = brief.target_titles?.map(item => item.term) ?? []
  const filters = [...(brief.required_credentials ?? []), ...(brief.required_skills ?? [])].map(item => item.term)
  return [...new Set([...titles, ...filters, ...(brief.positive_keywords ?? [])].map(term => term.trim()).filter(Boolean))].slice(0, 4).join(' ')
}

const key = (briefId: string) => `compass:search-settings:${briefId}`
export function readSearchSettings(briefId?: string): SearchSettings {
  const fallback = { network: ['F', 'S'], companyId: '' }
  if (!briefId) return fallback
  try {
    const saved = JSON.parse(window.localStorage.getItem(key(briefId)) ?? 'null')
    if (!saved || typeof saved.companyId !== 'string' ||
      !Array.isArray(saved.network) || !saved.network.every((value: unknown) => NETWORKS.some(option => option.value === value))) return fallback
    return { network: saved.network, companyId: saved.companyId }
  } catch { return fallback }
}

export function saveSearchSettings(briefId: string, settings: SearchSettings) {
  window.localStorage.setItem(key(briefId), JSON.stringify(settings))
}
