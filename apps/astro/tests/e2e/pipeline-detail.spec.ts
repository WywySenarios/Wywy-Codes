/**
 * E2E screenshot tests for the Pipeline Detail page with log file tabs.
 *
 * API responses are mocked via Playwright route interception so the tests
 * run against the Astro dev server alone (no Django backend needed).
 *
 * ── Screenshot baseline convention ──
 * Screenshot baselines (*.png) live in tests/e2e/screenshots/ and are
 * committed to the source tree.  Because source trees (Tier 1 per the
 * filesystem-permissions convention) are mounted read-only in containers,
 * baselines MUST be generated from the HOST, not from inside the container.
 *
 *   # From the host (apps/astro/):
 *   npx playwright test --update-snapshots --project=chromium \
 *     tests/e2e/pipeline-detail.spec.ts
 *
 * The behavioral assertion (toBeVisible) works in-container because it only
 * queries the live DOM, it does not write to disk.
 */

import { test, expect } from "@playwright/test";

/** Shared mock pipeline used by all tests in this file. */
const MOCK_PIPELINE = {
  id: "test-pipeline-id",
  invocation_name: "test-pipeline",
  status: "running",
  current_stage: "RED",
  iteration_count: 1,
  user_input_pending: false,
  user_input_request: null,
  pr_url: "",
  description: "A test pipeline for visual regression",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:01:00Z",
  stages: [
    { id: 1, name: "init", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 2, name: "RED", status: "running", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 3, name: "GREEN", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 4, name: "REFRACTOR", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 5, name: "compilance", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 6, name: "PR writer", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
  ],
};

const MOCK_LOG_FILES = {
  logs: ["orchestrator.log", "init.log", "RED.log", "GREEN.log"],
};

const MOCK_LOG_ENTRIES = {
  entries: [
    { ts: "2026-01-01T00:00:00.000Z", level: "INFO", msg: "Pipeline execution started", pipeline: "test-pipeline-id", stage: "-", src: "orchestrator" },
    { ts: "2026-01-01T00:00:01.000Z", level: "INFO", msg: "Workspace created", pipeline: "test-pipeline-id", stage: "init", src: "orchestrator" },
    { ts: "2026-01-01T00:00:02.000Z", level: "INFO", msg: "Stage RED starting", pipeline: "test-pipeline-id", stage: "RED", src: "orchestrator" },
  ],
};

/**
 * Per-file mock log entries so each tab returns distinct content.
 * Used by the happy-path test below to verify tab switching.
 *
 * The frontend derives log file names from pipeline stages via
 * logFilesFromStages(), so the keys must match the pattern
 * "orchestrator.log" plus "<stage_name>.log" for every stage in
 * MOCK_PIPELINE.
 */
const MOCK_ENTRIES_BY_FILE: Record<string, { entries: Array<Record<string, unknown>> }> = {
  "orchestrator.log": {
    entries: [
      { ts: "2026-01-01T00:00:00.000Z", level: "INFO", msg: "Orchestrator: pipeline created", pipeline: "test-pipeline-id", stage: "-", src: "orchestrator" },
      { ts: "2026-01-01T00:00:00.500Z", level: "INFO", msg: "Orchestrator: workspace initialised", pipeline: "test-pipeline-id", stage: "-", src: "orchestrator" },
    ],
  },
  "init.log": {
    entries: [
      { ts: "2026-01-01T00:00:01.000Z", level: "INFO", msg: "Init: preparing environment", pipeline: "test-pipeline-id", stage: "init", src: "agent" },
      { ts: "2026-01-01T00:00:02.000Z", level: "INFO", msg: "Init: configuration loaded", pipeline: "test-pipeline-id", stage: "init", src: "agent" },
    ],
  },
  "RED.log": {
    entries: [
      { ts: "2026-01-01T00:00:03.000Z", level: "INFO", msg: "RED: writing failing test", pipeline: "test-pipeline-id", stage: "RED", src: "agent" },
      { ts: "2026-01-01T00:00:04.000Z", level: "WARN", msg: "RED: linter warning", pipeline: "test-pipeline-id", stage: "RED", src: "agent" },
      { ts: "2026-01-01T00:00:05.000Z", level: "ERROR", msg: "RED: test suite failed", pipeline: "test-pipeline-id", stage: "RED", src: "agent" },
    ],
  },
  "GREEN.log": {
    entries: [
      { ts: "2026-01-01T00:00:06.000Z", level: "INFO", msg: "GREEN: making test pass", pipeline: "test-pipeline-id", stage: "GREEN", src: "agent" },
      { ts: "2026-01-01T00:00:07.000Z", level: "INFO", msg: "GREEN: all tests pass", pipeline: "test-pipeline-id", stage: "GREEN", src: "agent" },
    ],
  },
  "REFRACTOR.log": {
    entries: [
      { ts: "2026-01-01T00:00:08.000Z", level: "INFO", msg: "REFRACTOR: cleaning up", pipeline: "test-pipeline-id", stage: "REFRACTOR", src: "agent" },
    ],
  },
  "compilance.log": {
    entries: [
      { ts: "2026-01-01T00:00:09.000Z", level: "INFO", msg: "compilance: checking standards", pipeline: "test-pipeline-id", stage: "compilance", src: "agent" },
    ],
  },
  "PR writer.log": {
    entries: [],  // empty — tests empty-state rendering
  },
};

test.describe("Pipeline detail — log tabs", () => {
  test("renders pipeline detail with log file tabs", async ({ page }) => {
    // Mock all API responses the page depends on
    await page.route("**/api/pipelines/test-pipeline-id/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PIPELINE),
      });
    });

    await page.route("**/api/pipelines/test-pipeline-id/logs/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_LOG_FILES),
      });
    });

    await page.route("**/api/pipelines/test-pipeline-id/logs/entries/*", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_LOG_ENTRIES),
      });
    });

    await page.goto("/_spa/?id=test-pipeline-id");
    await page.waitForLoadState("networkidle");

    // Behavioral assertion: the page must render a tab list for log files
    await expect(page.locator('[role="tablist"]')).toBeVisible();

    // Visual regression: compare against the baseline screenshot.
    // BASELINE MUST BE GENERATED FROM HOST — see "Screenshot baseline convention" above.
    await expect(page).toHaveScreenshot("pipeline-detail-tabs.png", {
      fullPage: true,
      maxDiffPixelRatio: 0.02,
    });
  });
});

/**
 * Mock pipeline with no stages.
 *
 * Used by the "no stages" test below.  When a pipeline has no stages the
 * LogTabs component must still render the tab switching menu with at
 * least an "orchestrator" tab so that server logs are always visible
 * and the DOM does not restructure when stages are later added.
 */
const MOCK_PIPELINE_NO_STAGES = {
  ...MOCK_PIPELINE,
  current_stage: null,
  stages: [],
};

/**
 * Happy-path E2E tests for the log file tab experience.
 *
 * These tests define the specification for log tabs: each tab corresponds to a
 * log file, clicking a tab shows that file's entries in the LogViewer, and
 * the entries match the content returned by the logs/entries API endpoint.
 *
 * RED phase: These will fail until shadCN-style tabs and the LogViewer-per-tab
 * wiring are implemented end-to-end.
 */
test.describe("Pipeline detail — log tab content per stage", () => {
  test("clicking each log tab displays that file's entries and hides the previous", async ({ page }) => {
    // ── Mock pipeline detail ────────────────────────────────────────────
    await page.route("**/api/pipelines/test-pipeline-id/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PIPELINE),
      });
    });

    // ── Mock log file listing ───────────────────────────────────────────
    await page.route("**/api/pipelines/test-pipeline-id/logs/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_LOG_FILES),
      });
    });

    // ── Mock per-file log entries ───────────────────────────────────────
    // Each log file returns distinct entries so we can assert the right
    // panel content is visible after clicking a tab.
    await page.route("**/api/pipelines/test-pipeline-id/logs/entries/**", async (route) => {
      const url = route.request().url();
      // Extract the log filename from the URL path
      const match = url.match(/logs\/entries\/(.+?)\/?$/);
      const filename = match ? decodeURIComponent(match[1]) : "orchestrator.log";
      const body = MOCK_ENTRIES_BY_FILE[filename] ?? { entries: [] };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });

    // ── Navigate and wait for initial render ────────────────────────────
    await page.goto("/_spa/?id=test-pipeline-id");
    await page.waitForLoadState("networkidle");

    // ── Verify all log tabs are rendered ────────────────────────────────
    // The frontend derives log file names from pipeline stages via
    // logFilesFromStages(): 1 orchestrator.log + 1 server.log + N stage .log files.
    // MOCK_PIPELINE has 6 stages → 8 log tabs total.
    const tabList = page.locator('[role="tablist"]');
    await expect(tabList).toBeVisible({ timeout: 10000 });
    const tabs = page.getByRole("tab");
    await expect(tabs).toHaveCount(8);

    // ── Tab labels ──────────────────────────────────────────────────────
    await expect(tabs.nth(0)).toHaveText("orchestrator");
    await expect(tabs.nth(1)).toHaveText("server");
    await expect(tabs.nth(2)).toHaveText("init");
    await expect(tabs.nth(3)).toHaveText("RED");
    await expect(tabs.nth(4)).toHaveText("GREEN");
    await expect(tabs.nth(5)).toHaveText("REFRACTOR");
    await expect(tabs.nth(6)).toHaveText("compilance");
    await expect(tabs.nth(7)).toHaveText("PR writer");

    // ── Tab 0: orchestrator (default-selected) ─────────────────────────
    await expect(page.getByText("Orchestrator: pipeline created")).toBeVisible();
    await expect(page.getByText("Orchestrator: workspace initialised")).toBeVisible();
    await expect(page.getByText("Orchestrator: pipeline created")).toHaveCount(1);

    // ── Tab 1: server (no mock entries) ─────────────────────────────────
    await tabs.nth(1).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("No log entries yet.")).toBeVisible();

    // ── Tab 2: init ─────────────────────────────────────────────────────
    await tabs.nth(2).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("Init: preparing environment")).toBeVisible();
    await expect(page.getByText("Init: configuration loaded")).toBeVisible();
    await expect(page.getByText("Init: preparing environment")).toHaveCount(1);

    // ── Tab 3: RED ──────────────────────────────────────────────────────
    await tabs.nth(3).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("RED: writing failing test")).toBeVisible();
    await expect(page.getByText("RED: linter warning")).toBeVisible();
    await expect(page.getByText("RED: test suite failed")).toBeVisible();

    // ── Tab 4: GREEN ────────────────────────────────────────────────────
    await tabs.nth(4).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("GREEN: making test pass")).toBeVisible();
    await expect(page.getByText("GREEN: all tests pass")).toBeVisible();

    // ── Tab 5: REFRACTOR ────────────────────────────────────────────────
    await tabs.nth(5).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("REFRACTOR: cleaning up")).toBeVisible();

    // ── Tab 6: compilance ───────────────────────────────────────────────
    await tabs.nth(6).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("compilance: checking standards")).toBeVisible();

    // ── Tab 7: PR writer (empty log file) ───────────────────────────────
    await tabs.nth(7).click();
    await page.waitForTimeout(500);
    await expect(page.getByText("No log entries yet.")).toBeVisible();
  });

  test("raw toggle works inside each log tab", async ({ page }) => {
    // Same mocks as above but only the RED tab is needed
    await page.route("**/api/pipelines/test-pipeline-id/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PIPELINE),
      });
    });

    await page.route("**/api/pipelines/test-pipeline-id/logs/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_LOG_FILES),
      });
    });

    await page.route("**/api/pipelines/test-pipeline-id/logs/entries/**", async (route) => {
      const url = route.request().url();
      const match = url.match(/logs\/entries\/(.+?)\/?$/);
      const filename = match ? decodeURIComponent(match[1]) : "orchestrator.log";
      const body = MOCK_ENTRIES_BY_FILE[filename] ?? { entries: [] };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });

    await page.goto("/_spa/?id=test-pipeline-id");
    await page.waitForLoadState("networkidle");

    // Switch to RED tab and click "Raw" toggle
    const redTab = page.getByRole("tab", { name: "RED" });
    await redTab.click();
    await page.waitForTimeout(500);

    const rawButton = page.getByRole("button", { name: /raw/i });
    await expect(rawButton).toBeVisible();
    await rawButton.click();

    // Raw mode should show the JSON representation
    await expect(page.getByText(/RED: writing failing test/)).toBeVisible();
  });

  test("always shows tablist with orchestrator tab when pipeline has no stages", async ({ page }) => {
    // ── Mock pipeline detail with NO stages ─────────────────────────────
    // Simulates a queued / newly-created pipeline where stages have not
    // been created yet.  The LogTabs component must always render the tab
    // switching menu — even when logFiles is empty — to avoid a fragile
    // DOM restructuring when the pipeline later acquires stages.
    await page.route("**/api/pipelines/test-pipeline-id/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PIPELINE_NO_STAGES),
      });
    });

    await page.route("**/api/pipelines/test-pipeline-id/logs/entries/**", async (route) => {
      const url = route.request().url();
      const match = url.match(/logs\/entries\/(.+?)\/?$/);
      const filename = match ? decodeURIComponent(match[1]) : "orchestrator.log";
      const body = MOCK_ENTRIES_BY_FILE[filename] ?? { entries: [] };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });

    await page.goto("/_spa/?id=test-pipeline-id");
    await page.waitForLoadState("networkidle");

    // ── Tablist MUST be present even with no stages ─────────────────────
    const tabList = page.locator('[role="tablist"]');
    await expect(tabList).toBeVisible({ timeout: 10000 });
    const tabs = page.getByRole("tab");
    await expect(tabs).toHaveCount(1);
    await expect(tabs.nth(0)).toHaveText("orchestrator");

    // ── Server logs (orchestrator.log) must be visible ─────────────────
    await expect(page.getByText("Orchestrator: pipeline created")).toBeVisible();
    await expect(page.getByText("Orchestrator: workspace initialised")).toBeVisible();
  });
});

/**
 * Mock pipeline in init stage.
 *
 * Simulates a freshly-started pipeline where the init stage is "running".
 * During this phase the orchestrator logs pipeline lifecycle events and the
 * server (opencode server) logs startup health checks — both should be
 * visible by clicking their respective tabs.
 *
 * Log entry messages carry UID strings so assertions are precise and the
 * test can verify that tab switching shows/hides the correct content.
 */
const MOCK_PIPELINE_INIT = {
  id: "init-stage-pipeline",
  invocation_name: "init-stage-test",
  status: "running",
  current_stage: "init",
  iteration_count: 1,
  user_input_pending: false,
  user_input_request: null,
  pr_url: "",
  description: "Pipeline during init stage",
  created_at: "2026-06-19T00:00:00Z",
  updated_at: "2026-06-19T00:01:00Z",
  stages: [
    { id: 1, name: "init", status: "running", output: null, retry_count: 0, started_at: "2026-06-19T00:00:05Z", finished_at: null },
    { id: 2, name: "RED", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 3, name: "GREEN", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 4, name: "REFRACTOR", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 5, name: "compilance", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    { id: 6, name: "PR writer", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
  ],
};

/**
 * UID-based mock log entries for the init-stage test.
 *
 * Each entry's msg contains a unique identifier so the test can assert the
 * correct tab content is visible and previous content is hidden.
 */
const MOCK_ENTRIES_INIT: Record<string, { entries: Array<Record<string, unknown>> }> = {
  "orchestrator.log": {
    entries: [
      { ts: "2026-06-19T00:00:00.000Z", level: "INFO", msg: "uid-orch-001: pipeline queued", pipeline: "init-stage-pipeline", stage: "-", src: "orchestrator" },
      { ts: "2026-06-19T00:00:01.000Z", level: "INFO", msg: "uid-orch-002: pipeline execution started", pipeline: "init-stage-pipeline", stage: "-", src: "orchestrator" },
      { ts: "2026-06-19T00:00:02.000Z", level: "INFO", msg: "uid-orch-003: workspace creation initiated", pipeline: "init-stage-pipeline", stage: "-", src: "orchestrator" },
    ],
  },
  "server.log": {
    entries: [
      { ts: "2026-06-19T00:00:02.500Z", level: "INFO", msg: "uid-srv-001: opencode server container started", pipeline: "init-stage-pipeline", stage: "-", src: "server" },
      { ts: "2026-06-19T00:00:03.000Z", level: "INFO", msg: "uid-srv-002: health check endpoint responding", pipeline: "init-stage-pipeline", stage: "-", src: "server" },
      { ts: "2026-06-19T00:00:04.000Z", level: "INFO", msg: "uid-srv-003: server ready to accept stages", pipeline: "init-stage-pipeline", stage: "-", src: "server" },
    ],
  },
};

test.describe("Pipeline detail — init stage logs", () => {
  test("shows orchestrator and server log entries with UID values during init stage", async ({ page }) => {
    // ── Mock pipeline detail (init stage running) ──────────────────────
    await page.route("**/api/pipelines/init-stage-pipeline/", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_PIPELINE_INIT),
      });
    });

    // ── Mock per-file log entries ───────────────────────────────────────
    await page.route("**/api/pipelines/init-stage-pipeline/logs/entries/**", async (route) => {
      const url = route.request().url();
      const match = url.match(/logs\/entries\/(.+?)\/?$/);
      const filename = match ? decodeURIComponent(match[1]) : "orchestrator.log";
      const body = MOCK_ENTRIES_INIT[filename] ?? { entries: [] };
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(body),
      });
    });

    // ── Navigate and wait for initial render ────────────────────────────
    await page.goto("/_spa/?id=init-stage-pipeline");
    await page.waitForLoadState("networkidle");

    // ── Tab list must be visible ────────────────────────────────────────
    const tabList = page.locator('[role="tablist"]');
    await expect(tabList).toBeVisible({ timeout: 10000 });
    const tabs = page.getByRole("tab");
    // 6 stages → 8 log tabs (orchestrator + server + 6 stages)
    await expect(tabs).toHaveCount(8);

    // ── Tab labels ──────────────────────────────────────────────────────
    await expect(tabs.nth(0)).toHaveText("orchestrator");
    await expect(tabs.nth(1)).toHaveText("server");
    await expect(tabs.nth(2)).toHaveText("init");
    await expect(tabs.nth(3)).toHaveText("RED");
    await expect(tabs.nth(4)).toHaveText("GREEN");
    await expect(tabs.nth(5)).toHaveText("REFRACTOR");
    await expect(tabs.nth(6)).toHaveText("compilance");
    await expect(tabs.nth(7)).toHaveText("PR writer");

    // ── Default tab: orchestrator — verify UID entries ─────────────────
    await expect(page.getByText("uid-orch-001: pipeline queued")).toBeVisible();
    await expect(page.getByText("uid-orch-002: pipeline execution started")).toBeVisible();
    await expect(page.getByText("uid-orch-003: workspace creation initiated")).toBeVisible();

    // Server UIDs must NOT be visible while orchestrator tab is selected
    await expect(page.getByText("uid-srv-001: opencode server container started")).not.toBeVisible();

    // ── Switch to server tab — verify UID entries ───────────────────────
    await tabs.nth(1).click();
    await page.waitForTimeout(500);

    await expect(page.getByText("uid-srv-001: opencode server container started")).toBeVisible();
    await expect(page.getByText("uid-srv-002: health check endpoint responding")).toBeVisible();
    await expect(page.getByText("uid-srv-003: server ready to accept stages")).toBeVisible();

    // Orchestrator UIDs must be hidden after switching to server tab
    await expect(page.getByText("uid-orch-001: pipeline queued")).not.toBeVisible();

    // ── Switch back to orchestrator tab — verify UIDs still present ────
    await tabs.nth(0).click();
    await page.waitForTimeout(500);

    await expect(page.getByText("uid-orch-001: pipeline queued")).toBeVisible();
    await expect(page.getByText("uid-orch-002: pipeline execution started")).toBeVisible();
    await expect(page.getByText("uid-orch-003: workspace creation initiated")).toBeVisible();

    // Server UIDs must be hidden again after switching back
    await expect(page.getByText("uid-srv-001: opencode server container started")).not.toBeVisible();
  });
});
