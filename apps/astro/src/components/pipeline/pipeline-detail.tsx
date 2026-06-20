import { useState, useEffect } from "react";
import { StatusBadge, StageProgress } from "./stage-progress";
import { Button } from "../ui/button";
import { LogTabs } from "./log-tabs";
import { getPipeline, abortPipeline, type Pipeline, type PipelineStage } from "../../lib/api";
import { getPipelineIdFromURL, pipelineRespondUrl, pipelineFilesUrl } from "../../lib/routes";

function logFilesFromStages(stages: PipelineStage[]): string[] {
  return ["orchestrator.log", "server.log", ...stages.map((s) => `${s.name}.log`)];
}

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

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="flex items-center justify-between">
          <div className="h-8 bg-muted rounded w-1/4" />
          <div className="h-8 bg-muted rounded w-24" />
        </div>
        <div className="grid grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-card border border-border rounded-lg p-3">
              <div className="h-4 bg-muted rounded w-2/3" />
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
      <div className="text-muted-foreground text-center py-12">Pipeline not found.</div>
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
              <Button variant="default">Respond</Button>
            </a>
          )}
          {(pipeline.status === "running" || pipeline.status === "queued") && (
            <Button variant="destructive" onClick={handleAbort}>Abort</Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 text-sm">
        <div className="bg-card border border-border rounded-lg p-3">
          <span className="text-muted-foreground">ID:</span>{" "}
          <span className="font-mono text-foreground">{pipeline.id.slice(0, 12)}</span>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <span className="text-muted-foreground">Iterations:</span>{" "}
          <span className="text-foreground">{pipeline.iteration_count}</span>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <span className="text-muted-foreground">Created:</span>{" "}
          <span className="text-foreground">{new Date(pipeline.created_at).toISOString().replace('T', ' ').slice(0, 19)}</span>
        </div>
        <div className="bg-card border border-border rounded-lg p-3">
          <span className="text-muted-foreground">Updated:</span>{" "}
          <span className="text-foreground">{new Date(pipeline.updated_at).toISOString().replace('T', ' ').slice(0, 19)}</span>
        </div>
      </div>

      {pipeline.description && (
        <div className="bg-card border border-border rounded-lg p-4">
          <h2 className="text-sm font-medium text-muted-foreground mb-2">Description</h2>
          <p className="text-sm text-foreground whitespace-pre-wrap">{pipeline.description}</p>
        </div>
      )}

      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Stages</h2>
        <a href={pipelineFilesUrl(pipeline.id)} className="text-sm text-blue-400 hover:underline">
          View Files
        </a>
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <StageProgress stages={stages} />
        <div className="mt-4 space-y-1">
          {stages.map((s) => (
            <div key={s.name} className="flex items-center justify-between text-sm">
              <span className="text-foreground">{s.name}</span>
              <div className="flex items-center gap-2">
                {s.retry_count > 0 && (
                  <span className="text-xs text-muted-foreground">({s.retry_count} retries)</span>
                )}
                <StatusBadge status={s.status} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="text-lg font-semibold mb-3">Logs</h2>
        <LogTabs
          pipelineId={pipeline.id}
          logFiles={stages.length > 0 ? logFilesFromStages(stages) : []}
        />
      </div>

      {pipeline.pr_url && (
        <div className="bg-card border border-border rounded-lg p-4">
          <span className="text-muted-foreground text-sm">PR: </span>
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


