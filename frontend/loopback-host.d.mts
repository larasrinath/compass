import type { Plugin } from 'vite'

export function assertLoopbackHost(
  host: string | boolean | undefined,
  surface: string,
): string
export function backendProxyTarget(
  env?: Partial<Record<'HOST' | 'PORT', string | undefined>>,
): string
export function frontendBinding(
  env?: Partial<Record<'FRONTEND_HOST' | 'FRONTEND_PORT', string | undefined>>,
): { host: string; port: number; origin: string }
export function loopbackOnlyPlugin(expectedFrontend?: {
  host: string
  port: number
}): Plugin
