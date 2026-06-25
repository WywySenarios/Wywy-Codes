/**
 * Tests for API functions in lib/api.ts.
 *
 * Verifies that getSpaLogs and getDjangoLogs parse responses correctly
 * and handle optional parameters.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { getSpaLogs, getDjangoLogs, type SpaLogResponse, type LogEntry } from "../../../src/lib/api";

const mockLogEntry: LogEntry = {
  ts: "2025-01-01T00:00:00Z",
  level: "INFO",
  pipeline: "test",
  stage: "init",
  src: "test",
  msg: "test message",
};

const mockSpaResponse: SpaLogResponse = {
  system: [mockLogEntry],
  django: [mockLogEntry],
};

const mockSpaResponseWithPipeline: SpaLogResponse = {
  ...mockSpaResponse,
  pipeline: {
    files: ["orchestrator.log", "server.log"],
    entries: [mockLogEntry],
  },
};

const mockDjangoResponse = { entries: [mockLogEntry, mockLogEntry] };

/** Mock globalThis.fetch to return a JSON response with the given body. */
function mockFetch(responseBody: unknown): void {
  vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
    new Response(JSON.stringify(responseBody), { status: 200 }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("getSpaLogs", () => {
  it("calls GET /api/logs/spa/ and returns consolidated log data", async () => {
    mockFetch(mockSpaResponse);
    const data: SpaLogResponse = await getSpaLogs();
    expect(data).toHaveProperty("system");
    expect(data).toHaveProperty("django");
    expect(data.django).toHaveLength(1);
    expect(data.system).toHaveLength(1);
  });

  it("accepts an optional pipeline_id parameter", async () => {
    mockFetch(mockSpaResponseWithPipeline);
    const pipelineId = "00000000-0000-0000-0000-000000000000";
    const data: SpaLogResponse = await getSpaLogs(pipelineId);
    expect(data).toHaveProperty("system");
    expect(data).toHaveProperty("django");
    expect(data).toHaveProperty("pipeline");
    expect(data.pipeline).toHaveProperty("files");
    expect(data.pipeline).toHaveProperty("entries");
  });

  it("accepts a lines parameter", async () => {
    mockFetch(mockSpaResponse);
    const data: SpaLogResponse = await getSpaLogs(undefined, 50);
    expect(data).toHaveProperty("system");
    expect(data).toHaveProperty("django");
  });
});

describe("getDjangoLogs", () => {
  it("calls GET /api/logs/django/ and returns log entries", async () => {
    mockFetch(mockDjangoResponse);
    const entries: LogEntry[] = await getDjangoLogs();
    expect(Array.isArray(entries)).toBe(true);
    expect(entries).toHaveLength(2);
  });

  it("accepts a lines parameter", async () => {
    mockFetch(mockDjangoResponse);
    const entries: LogEntry[] = await getDjangoLogs(50);
    expect(Array.isArray(entries)).toBe(true);
    expect(entries).toHaveLength(2);
  });
});
