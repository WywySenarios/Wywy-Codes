const API_HOST = import.meta.env.PUBLIC_AGENTIC_API_HOST || "localhost";
const API_PORT = import.meta.env.PUBLIC_AGENTIC_API_PORT || "8000";
const API_URL = `http://${API_HOST}:${API_PORT}`;

export interface Pipeline {
  id: string;
  invocation_name: string;
  status: string;
  current_stage: string | null;
  iteration_count: number;
  user_input_pending: boolean;
  user_input_request: AgentRequest | null;
  pr_url: string;
  description: string;
  created_at: string;
  updated_at: string;
}

export interface PipelineStage {
  id: number;
  name: string;
  status: string;
  output: unknown;
  retry_count: number;
  started_at: string | null;
  finished_at: string | null;
}

export interface AgentRequest {
  type: string;
  summary: string;
  question: string;
  options?: { id: string; label: string; description: string }[];
  context_refs?: { type: string; path?: string; stage?: string; note: string; line_start?: number; line_end?: number }[];
}

export interface LogEntry {
  ts: string;
  level: string;
  pipeline: string;
  stage: string;
  src: string;
  msg: string;
  ctx?: Record<string, unknown>;
}

export interface FileListing {
  artifacts: { path: string; size: number }[];
  summaries: { path: string; size: number }[];
  user_input: { path: string; size: number }[];
  logs: { path: string; size: number }[];
  other: { path: string; size: number }[];
}

export async function listPipelines(status?: string): Promise<Pipeline[]> {
  const url = `${API_URL}/api/pipelines/${status ? `?status=${status}` : ""}`;
  const res = await fetch(url);
  const data = await res.json();
  return data.pipelines || [];
}

export async function listBlockedPipelines(): Promise<Pipeline[]> {
  const res = await fetch(`${API_URL}/api/pipelines/blocked/`);
  const data = await res.json();
  return data.pipelines || [];
}

export async function getPipeline(id: string): Promise<Pipeline & { stages?: PipelineStage[] }> {
  const res = await fetch(`${API_URL}/api/pipelines/${id}/`);
  return res.json();
}

export async function createPipeline(description: string, invocationName: string) {
  const res = await fetch(`${API_URL}/api/pipelines/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description, invocation_name: invocationName }),
  });
  return res.json();
}

export async function respondToPipeline(id: string, selectedOption: string, freeform: string) {
  const res = await fetch(`${API_URL}/api/pipelines/${id}/respond/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_option: selectedOption, freeform_response: freeform }),
  });
  return res.json();
}

export async function abortPipeline(id: string) {
  const res = await fetch(`${API_URL}/api/pipelines/${id}/abort/`, { method: "POST" });
  return res.json();
}

export async function tailLog(pipelineId: string, stage: string): Promise<LogEntry[]> {
  const res = await fetch(`${API_URL}/api/pipelines/${pipelineId}/logs/${stage}/`);
  const data = await res.json();
  return data.entries || [];
}

export async function listFiles(pipelineId: string, verbose = false): Promise<FileListing> {
  const url = `${API_URL}/api/pipelines/${pipelineId}/files/${verbose ? "?verbose=1" : ""}`;
  const res = await fetch(url);
  return res.json();
}

export async function getFileContent(pipelineId: string, path: string): Promise<string> {
  const res = await fetch(`${API_URL}/api/pipelines/${pipelineId}/files/?path=${encodeURIComponent(path)}`);
  return res.text();
}
