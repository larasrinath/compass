import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

import { loopbackOnlyPlugin } from './loopback-host.mjs'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), loopbackOnlyPlugin()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
    },
  },
  preview: {
    host: '127.0.0.1',
    port: 4173,
    strictPort: true,
  },
})
