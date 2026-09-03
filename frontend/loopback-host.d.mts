import type { Plugin } from 'vite'

export function assertLoopbackHost(
  host: string | boolean | undefined,
  surface: string,
): string
export function loopbackOnlyPlugin(): Plugin
