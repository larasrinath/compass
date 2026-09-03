import react from '@vitejs/plugin-react'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'

import { backendProxyTarget, loopbackOnlyPlugin } from './loopback-host.mjs'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const backendEnv = { ...loadEnv(mode, projectRoot, ''), ...process.env }
  return {
    plugins: [react(), loopbackOnlyPlugin()],
    server: {
      host: '127.0.0.1',
      port: 5173,
      strictPort: true,
      proxy: {
        '/api': backendProxyTarget(backendEnv),
      },
    },
    preview: {
      host: '127.0.0.1',
      port: 4173,
      strictPort: true,
    },
  }
})
