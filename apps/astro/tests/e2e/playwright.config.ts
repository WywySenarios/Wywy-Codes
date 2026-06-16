import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for agentic frontend screenshot tests.
 *
 * Visual regression tests compare page screenshots against checked-in baselines.
 * The webServer block auto-starts the Astro dev server for E2E runs.
 *
 * Snapshots are stored in tests/e2e/screenshots/ (created on first --update-snapshots).
 */
export default defineConfig({
  testDir: ".",
  snapshotDir: "./screenshots",
  /** Include the test file path in the snapshot name for disambiguation. */
  snapshotPathTemplate: "{snapshotDir}/{testFilePath}/{arg}{ext}",
  timeout: 30000,
  expect: {
    timeout: 10000,
    toHaveScreenshot: {
      threshold: 0.2,
      maxDiffPixelRatio: 0.02,
    },
  },
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:4321",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  /** Start the Astro dev server before tests run. */
  webServer: {
    command: "npm run dev",
    cwd: "../..",
    port: 4321,
    timeout: 30000,
    reuseExistingServer: !process.env.CI,
  },
});
