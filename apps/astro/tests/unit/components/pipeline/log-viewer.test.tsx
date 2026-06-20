/**
 * Tests for LogViewer component — log tabs and raw mode.
 *
 * RED: These tests assert new features that don't exist yet.
 *  1. A "Toggle Raw" button to switch between parsed and raw log display.
 *  2. Raw mode displays the fetched content as plain text.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { LogViewer } from "../../../../src/components/pipeline/log-viewer";
import type { LogEntry } from "../../../../src/lib/api";

const sampleEntries: LogEntry[] = [
  {
    ts: "2026-01-01T00:00:00.000Z",
    level: "INFO",
    msg: "Pipeline started",
    pipeline: "p1",
    stage: "orchestrator",
    src: "orchestrator",
  },
  {
    ts: "2026-01-01T00:00:01.000Z",
    level: "ERROR",
    msg: "Something went wrong",
    pipeline: "p1",
    stage: "orchestrator",
    src: "orchestrator",
  },
];

vi.mock("../../../../src/lib/api", () => ({
  tailLog: vi.fn(
    (_pipelineId: string, _stage: string): Promise<LogEntry[]> =>
      Promise.resolve(sampleEntries),
  ),
}));

describe("LogViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders parsed log entries for a given stage", async () => {
    render(<LogViewer pipelineId="test-pipeline" stage="orchestrator" />);

    expect(await screen.findByText("Pipeline started")).toBeTruthy();
    expect(screen.getByText("Something went wrong")).toBeTruthy();
  });

  it("provides a toggle to switch to raw log display", async () => {
    render(<LogViewer pipelineId="test-pipeline" stage="orchestrator" />);

    const rawButton = await screen.findByRole("button", { name: /raw/i });
    expect(rawButton).toBeTruthy();
  });

  it("displays raw log content when raw toggle is active", async () => {
    render(<LogViewer pipelineId="test-pipeline" stage="orchestrator" />);

    const rawButton = await screen.findByRole("button", { name: /raw/i });
    fireEvent.click(rawButton);

    // When raw mode is on, the raw API response text should be visible
    expect(screen.getByText(/raw content/i)).toBeTruthy();
  });
});
