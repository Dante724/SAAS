import { defineConfig, devices } from "@playwright/test";

// E2E / visual-regression config. Runs against a RUNNING app (your dev server), not the
// sandbox. Point it with BASE_URL (default http://127.0.0.1:8000) and sign-in creds via
// E2E_EMAIL / E2E_PASSWORD. See e2e/README.md.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000, toHaveScreenshot: { maxDiffPixelRatio: 0.02 } },
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.BASE_URL || "http://127.0.0.1:8000",
    viewport: { width: 1440, height: 900 },
    storageState: "e2e/.auth/state.json",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, dependencies: ["setup"] },
  ],
});
