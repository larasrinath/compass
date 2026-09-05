import { defineConfig } from '@playwright/test'

const port = 5194

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  reporter: 'line',
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: 'chrome',
    headless: true,
    trace: 'retain-on-failure',
  },
  webServer: {
    command: `FRONTEND_PORT=${port} npm run dev`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
