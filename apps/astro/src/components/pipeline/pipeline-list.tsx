import { useState, useEffect } from "react";
import { StatusBadge, StageProgress } from "./stage-progress";
import { listPipelines, type Pipeline } from "../../lib/api";
import { pipelineUrl, pipelineRespondUrl } from "../../lib/routes";

export function PipelineList() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listPipelines().then(setPipelines).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-4 animate-pulse">
            <div className="h-5 bg-gray-800 rounded w-1/3 mb-3" />
            <div className="h-4 bg-gray-800 rounded w-2/3 mb-2" />
            <div className="h-3 bg-gray-800 rounded w-1/4" />
          </div>
        ))}
      </div>
    );
  }

  if (pipelines.length === 0) {
    return (
      <div className="text-center py-16 text-gray-500">
        <p className="mb-2">No pipelines yet.</p>
        <a href="/new/" className="text-blue-400 hover:underline">Create one</a>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {pipelines.map((p) => (
        <div key={p.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <a href={pipelineUrl(p.id)} className="text-base font-semibold text-gray-100 hover:text-blue-400">
                {p.invocation_name}
              </a>
              <StatusBadge status={p.status} />
            </div>
            <span className="text-xs text-gray-500">
              {new Date(p.created_at).toISOString().split('T')[0]}
            </span>
          </div>
          {p.description && (
            <p className="text-sm text-gray-500 mb-2 truncate">{p.description}</p>
          )}
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>Stage: {p.current_stage || "-"}</span>
            <span>Iterations: {p.iteration_count}</span>
            {p.user_input_pending && (
              <a href={pipelineRespondUrl(p.id)} className="text-yellow-400 hover:underline font-medium">
                Needs input
              </a>
            )}
          </div>
          <div className="mt-2">
            <StageProgress stages={(p as any).stages || []} />
          </div>
        </div>
      ))}
    </div>
  );
}
