import { useState, useEffect } from "react";
import { StatusBadge, StageProgress } from "./stage-progress";
import { LogViewer } from "./log-viewer";
import { Button } from "../ui/button";
import { getPipeline, abortPipeline, type Pipeline, type PipelineStage } from "../../lib/api";
import { getPipelineIdFromURL, pipelineRespondUrl, pipelineFilesUrl } from "../../lib/routes";

export function PipelineDetail({ pipelineId }: { pipelineId?: string }) {
  const [pipeline, setPipeline] = useState<(Pipeline & { stages?: PipelineStage[] }) | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const id = pipelineId && pipelineId !== "_spa" ? pipelineId : getPipelineIdFromURL();

  useEffect(() => {
    if (id) {
      getPipeline(id).then(setPipeline).catch(() => setError("Failed to load pipeline data.")).finally(() => setLoading(false));
    }
  }, [id]);

  async function handleAbort() {
    if (!pipeline || !confirm("Abort this pipeline?")) return;
    setError("");
    try {
      await abortPipeline(pipeline.id);
      window.location.reload();
    } catch {
      setError("Failed to abort pipeline.");
    }
  }

  const stages = pipeline?.stages || [];
  const currentStage = pipeline?.current_stage || "orchestrator";

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="flex items-center justify-between">
          <div className="h-8 bg-gray-800 rounded w-1/4" />
          <div className="h-8 bg-gray-800 rounded w-24" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-3">
              <div className="h-4 bg-gray-800 rounded w-2/3" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error && !pipeline) {
    return (
      <div className="bg-red-900/30 border border-red-700 rounded-lg p-6 text-center">
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  if (!pipeline) {
    return (
      <div className="text-gray-500 text-center py-12">Pipeline not found.</div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold">{pipeline.invocation_name}</h1>
          <StatusBadge status={pipeline.status} />
        </div>
        <div className="flex gap-2">
          {pipeline.user_input_pending && (
            <a href={pipelineRespondUrl(pipeline.id)}>
              <Button variant="primary">Respond</Button>
            </a>
          )}
          {(pipeline.status === "running" || pipeline.status === "queued") && (
            <Button variant="danger" onClick={handleAbort}>Abort</Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <span className="text-gray-500">ID:</span>{" "}
          <span className="font-mono text-gray-300">{pipeline.id.slice(0, 12)}</span>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <span className="text-gray-500">Iterations:</span>{" "}
          <span className="text-gray-300">{pipeline.iteration_count}</span>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <span className="text-gray-500">Created:</span>{" "}
          <span className="text-gray-300">{new Date(pipeline.created_at).toISOString().replace('T', ' ').slice(0, 19)}</span>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-3">
          <span className="text-gray-500">Updated:</span>{" "}
          <span className="text-gray-300">{new Date(pipeline.updated_at).toISOString().replace('T', ' ').slice(0, 19)}</span>
        </div>
      </div>

      {pipeline.description && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <h2 className="text-sm font-medium text-gray-400 mb-2">Description</h2>
          <p className="text-sm text-gray-300 whitespace-pre-wrap">{pipeline.description}</p>
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Stages</h2>
        <a href={pipelineFilesUrl(pipeline.id)} className="text-sm text-blue-400 hover:underline">
          View Files
        </a>
      </div>

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <StageProgress stages={stages} />
        <div className="mt-4 space-y-1">
          {stages.map((s) => (
            <div key={s.name} className="flex items-center justify-between text-sm">
              <span className="text-gray-300">{s.name}</span>
              <div className="flex items-center gap-2">
                {s.retry_count > 0 && (
                  <span className="text-xs text-gray-600">({s.retry_count} retries)</span>
                )}
                <StatusBadge status={s.status} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">
          Log — <span className="text-gray-400">{currentStage}</span>
        </h2>
        <LogViewer pipelineId={pipeline.id} stage={currentStage} />
      </div>

      {pipeline.pr_url && (
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <span className="text-gray-400 text-sm">PR: </span>
          <a href={pipeline.pr_url} className="text-blue-400 hover:underline text-sm" target="_blank" rel="noopener">
            {pipeline.pr_url}
          </a>
        </div>
      )}

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-3">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}
    </div>
  );
}


