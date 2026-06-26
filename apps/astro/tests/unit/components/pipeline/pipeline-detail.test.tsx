/**
 * Tests for PipelineDetail page — log file tabs.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { PipelineDetail } from "../../../../src/components/pipeline/pipeline-detail";
import type { Pipeline, PipelineStage } from "../../../../src/lib/api";
import { getPipeline } from "../../../../src/lib/api";

const stages: PipelineStage[] = [
  { id: 1, name: "init", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
  { id: 2, name: "RED", status: "running", output: null, retry_count: 0, started_at: null, finished_at: null },
  { id: 3, name: "GREEN", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
];

const mockPipeline: Pipeline & { stages?: PipelineStage[] } = {
  id: "test-pipeline-id",
  invocation_name: "test-pipeline",
  status: "running",
  current_stage: "RED",
  iteration_count: 1,
  user_input_pending: false,
  user_input_request: null,
  pr_url: "",
  description: "A test pipeline",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:01:00Z",
  stages,
};

// Mock all API imports used by PipelineDetail (including transitive deps like LogViewer).
vi.mock("../../../../src/lib/api", () => ({
  getPipeline: vi.fn(() => Promise.resolve(mockPipeline)),
  abortPipeline: vi.fn(),
  tailLog: vi.fn(() => Promise.resolve([])),
  getSpaLogs: vi.fn(() => Promise.resolve({ system: [], django: [], pipeline: { files: [], entries: [] } })),
}));

describe("PipelineDetail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders pipeline metadata", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    expect(await screen.findByText("test-pipeline")).toBeTruthy();
    // "RED" appears in both the stage list and the tab bar (from RED.log)
    expect(screen.getAllByText("RED").length).toBeGreaterThanOrEqual(1);
  });

  it("renders a tab list for selecting log files", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    const tablist = await screen.findByRole("tablist");
    expect(tablist).toBeTruthy();
  });

  it("includes orchestrator and stage log tabs", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    const tabs = await screen.findAllByRole("tab");
    const tabNames = tabs.map((t) => t.textContent);
    expect(tabNames).toContain("orchestrator");
  });

  it("includes server log tab for server container output", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    const tabs = await screen.findAllByRole("tab");
    const tabNames = tabs.map((t) => t.textContent);
    expect(tabNames).toContain("server");
  });

  it("includes a System tab for consolidated orchestrator logs", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    expect(await screen.findByRole("tab", { name: "System" })).toBeTruthy();
  });

  it("includes a Django tab for consolidated application logs", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    expect(await screen.findByRole("tab", { name: "Django" })).toBeTruthy();
  });
});

describe("PipelineDetail — stages section", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the stages section heading", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    expect(await screen.findByText("Stages")).toBeTruthy();
  });

  it("renders a View Files link in the stages section header", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    const link = await screen.findByText("View Files");
    expect(link).toBeTruthy();
    expect(link.getAttribute("href")).toMatch(/\/files/);
  });

  it("renders all stage names from the API response in the stages list", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // Wait for component to finish loading (getPipeline resolves)
    await screen.findByText("Stages");

    for (const s of stages) {
      // Each stage name appears at least once (in the stages list; RED also appears as a log tab)
      expect(screen.getAllByText(s.name).length).toBeGreaterThanOrEqual(1);
    }
  });

  it("shows the correct status badge for each stage", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // init is completed, RED is running, GREEN is pending
    // "completed" and "pending" are unique; "running" appears in both the
    // pipeline-level status badge (mockPipeline.status) and the RED stage badge
    expect(await screen.findByText("completed")).toBeTruthy();
    expect(screen.getAllByText("running").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("pending")).toBeTruthy();
  });

  it("renders StageProgress progress dots with correct status titles", async () => {
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // StageProgress renders 6 dots: init, RED, GREEN, REFRACTOR, compliance, PR writer
    // The 3 stages from the API each have a known status
    // init is completed, RED is running, GREEN is pending
    expect(await screen.findByTitle("init: completed")).toBeTruthy();
    expect(screen.getByTitle("RED: running")).toBeTruthy();
    expect(screen.getByTitle("GREEN: pending")).toBeTruthy();
    // Stages not present in the API default to "pending"
    expect(screen.getByTitle("REFRACTOR: pending")).toBeTruthy();
    expect(screen.getByTitle("compliance: pending")).toBeTruthy();
    expect(screen.getByTitle("PR writer: pending")).toBeTruthy();
  });

  it("shows retry count when a stage has been retried", async () => {
    const stagesWithRetries: PipelineStage[] = [
      { id: 1, name: "init", status: "running", output: null, retry_count: 3, started_at: null, finished_at: null },
      { id: 2, name: "RED", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 3, name: "GREEN", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    ];
    const pipelineWithRetries = { ...mockPipeline, stages: stagesWithRetries, status: "queued" };
    vi.mocked(getPipeline).mockResolvedValue(pipelineWithRetries);

    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    expect(await screen.findByText("(3 retries)")).toBeTruthy();
    // Verify pipeline-level badge shows "queued" (not "running") to confirm distinct states
    expect(screen.getByText("queued")).toBeTruthy();
  });

  it("does not show retry label when retry_count is zero", async () => {
    // Reset any mock implementation that may have leaked from previous tests
    vi.mocked(getPipeline).mockImplementation(() => Promise.resolve(mockPipeline));
    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    await screen.findByText("Stages");
    // None of the 3 stages in the default mock have retries
    expect(screen.queryByText(/retries?/)).toBeNull();
  });

  it("renders init stage with a running status badge when pipeline is stuck in init", async () => {
    // Simulate the user's scenario: pipeline stuck at init stage
    const stagesInitRunning: PipelineStage[] = [
      { id: 1, name: "init", status: "running", output: null, retry_count: 1, started_at: "2026-06-19T00:00:05Z", finished_at: null },
      { id: 2, name: "RED", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 3, name: "GREEN", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    ];
    // Use a distinct pipeline status so stage "running" is unambiguous
    const pipelineInitRunning = { ...mockPipeline, stages: stagesInitRunning, current_stage: "init", status: "queued" };
    vi.mocked(getPipeline).mockResolvedValue(pipelineInitRunning);

    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // The init stage must show "running" in its status badge (there should be exactly one)
    expect(await screen.findByText("running")).toBeTruthy();

    // The init stage name must be present (appears in both stage list and log tab)
    expect(screen.getAllByText("init").length).toBeGreaterThanOrEqual(1);

    // Init's retry count must be displayed (since retry_count > 0)
    expect(screen.getByText("(1 retries)")).toBeTruthy();

    // The StageProgress dot must show init as running (not just in the text list)
    expect(screen.getByTitle("init: running")).toBeTruthy();
  });

  it("renders init stage as failed when init cannot complete", async () => {
    // Edge case: what if init itself fails?
    const stagesInitFailed: PipelineStage[] = [
      { id: 1, name: "init", status: "failed", output: null, retry_count: 2, started_at: "2026-06-19T00:00:05Z", finished_at: "2026-06-19T00:00:20Z" },
      { id: 2, name: "RED", status: "pending", output: null, retry_count: 0, started_at: null, finished_at: null },
    ];
    // Use a distinct pipeline-level status so stage "failed" is unambiguous
    const pipelineInitFailed = { ...mockPipeline, stages: stagesInitFailed, status: "cancelled", current_stage: "init" };
    vi.mocked(getPipeline).mockResolvedValue(pipelineInitFailed);

    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // The init stage must show "failed" in its status badge (exactly one "failed" element)
    expect(await screen.findByText("failed")).toBeTruthy();

    // Init's retry count should be shown
    expect(screen.getByText("(2 retries)")).toBeTruthy();

    // StageProgress must show init as failed and RED as pending (not started)
    expect(screen.getByTitle("init: failed")).toBeTruthy();
    expect(screen.getByTitle("RED: pending")).toBeTruthy();
  });

  it("renders all 6 stages when the API returns a full pipeline", async () => {
    const allStages: PipelineStage[] = [
      { id: 1, name: "init", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 2, name: "RED", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 3, name: "GREEN", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 4, name: "REFRACTOR", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 5, name: "compliance", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
      { id: 6, name: "PR writer", status: "completed", output: null, retry_count: 0, started_at: null, finished_at: null },
    ];
    const pipelineCompleted = { ...mockPipeline, stages: allStages, status: "completed" };
    vi.mocked(getPipeline).mockResolvedValue(pipelineCompleted);

    render(<PipelineDetail pipelineId="test-pipeline-id" />);

    // Wait for render, then verify all 6 stage names appear at least once
    // (some names appear in both stage list and log tabs — e.g. "init" is
    // rendered as both a stage name span and a log tab button)
    await screen.findByText("Stages");
    for (const s of allStages) {
      expect(screen.getAllByText(s.name).length).toBeGreaterThanOrEqual(1);
    }

    // All 6 progress dots must show "completed" (init is now in StageProgress too)
    expect(screen.getByTitle("init: completed")).toBeTruthy();
    expect(screen.getByTitle("RED: completed")).toBeTruthy();
    expect(screen.getByTitle("GREEN: completed")).toBeTruthy();
    expect(screen.getByTitle("REFRACTOR: completed")).toBeTruthy();
    expect(screen.getByTitle("compliance: completed")).toBeTruthy();
    expect(screen.getByTitle("PR writer: completed")).toBeTruthy();
  });
});
