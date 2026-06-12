import { useState, useEffect } from "react";
import { StatusBadge } from "./stage-progress";
import { listBlockedPipelines, type Pipeline } from "../../lib/api";
import { pipelineUrl, pipelineRespondUrl } from "../../lib/routes";

export function Inbox() {
  const [pipelines, setPipelines] = useState<Pipeline[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    listBlockedPipelines().then(setPipelines).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2].map((i) => (
          <div key={i} className="bg-gray-900 border border-gray-800 rounded-lg p-4 animate-pulse">
            <div className="h-5 bg-gray-800 rounded w-1/3 mb-3" />
            <div className="h-4 bg-gray-800 rounded w-2/3" />
          </div>
        ))}
      </div>
    );
  }

  if (pipelines.length === 0) {
    return (
      <div className="text-center py-16 text-gray-500">
        <p>No pipelines awaiting input. All clear!</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {pipelines.map((p) => (
        <div key={p.id} className="bg-yellow-900/20 border border-yellow-800 rounded-lg p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-3">
              <a href={pipelineUrl(p.id)} className="text-base font-semibold text-gray-100 hover:text-blue-400">
                {p.invocation_name}
              </a>
              <StatusBadge status={p.status} />
            </div>
            <span className="text-xs text-yellow-600">Blocked at: {p.current_stage}</span>
          </div>

          {p.user_input_request && (
            <>
              <p className="text-sm font-medium text-yellow-300 mb-1">{p.user_input_request.summary}</p>
              {p.user_input_request.question && (
                <p className="text-sm text-gray-400 line-clamp-3 mb-3">{p.user_input_request.question}</p>
              )}
            </>
          )}

          <div className="flex items-center justify-between">
            <span className="text-xs text-gray-600">
              Waiting since: {new Date(p.updated_at).toISOString().replace('T', ' ').slice(0, 19)}
            </span>
            <a href={pipelineRespondUrl(p.id)} className="inline-flex items-center px-3 py-1.5 rounded-lg text-sm font-medium bg-yellow-600 hover:bg-yellow-700 text-white transition-colors">
              Respond
            </a>
          </div>
        </div>
      ))}
    </div>
  );
}
