// Playwright config for the technician PWA. Boots the FastAPI app with a demo
// seed and runs the specs against it. Chromium is launched with a fixed remote
// debugging port so the Lighthouse spec can attach.
const { defineConfig, devices } = require('@playwright/test');

const PORT = process.env.E2E_PORT || 8000;
const BASE = `http://localhost:${PORT}`;

module.exports = defineConfig({
  testDir: '.',
  timeout: 60000,
  expect: { timeout: 10000 },
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: BASE,
    trace: 'on-first-retry',
    // Pixel-2 sized viewport by default (mobile-first surface).
    ...devices['Pixel 5'],
  },
  projects: [
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
    { name: 'tablet', use: { ...devices['iPad (gen 7)'] } },
  ],
  webServer: {
    command:
      `SEED_DEMO=true SECRET_KEY=${process.env.SECRET_KEY || 'ci-e2e-secret-key-min-32-chars-long-000'} ` +
      `python3 -m uvicorn main:app --port ${PORT}`,
    cwd: '..',
    url: `${BASE}/api/health`,
    reuseExistingServer: !process.env.CI,
    timeout: 60000,
  },
});
