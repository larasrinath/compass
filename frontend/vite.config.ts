import react from '@vitejs/plugin-react'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'

import {
  backendProxyTarget,
  frontendBinding,
  loopbackOnlyPlugin,
} from './loopback-host.mjs'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const backendEnv = { ...loadEnv(mode, projectRoot, ''), ...process.env }
  const frontend = frontendBinding(backendEnv)
  return {
    plugins: [react(), loopbackOnlyPlugin(frontend)],
    server: {
      host: frontend.host,
      port: frontend.port,
      strictPort: true,
      proxy: {
        '/api': backendProxyTarget(backendEnv),
      },
    },
    preview: {
      host: frontend.host,
      port: frontend.port,
      strictPort: true,
    },
  }
})
