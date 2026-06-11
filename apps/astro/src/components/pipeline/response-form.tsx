import { useState, useEffect } from "react";
import { respondToPipeline, getPipeline, type AgentRequest, type Pipeline } from "../../lib/api";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";

export function ResponseForm({ pipelineId }: { pipelineId?: string }) {
  const [request, setRequest] = useState<AgentRequest | null>(null);
  const [pipeline, setPipeline] = useState<Pipeline | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedOption, setSelectedOption] = useState("");
  const [freeform, setFreeform] = useState("");
  const [submitted, setSubmitted] = useState(false);

  const id = pipelineId && pipelineId !== "_spa" ? pipelineId : getPipelineIdFromURL();

  useEffect(() => {
    if (id) {
      getPipeline(id)
        .then((p) => {
          setPipeline(p);
          if (p.user_input_request) {
            setRequest(p.user_input_request);
          }
        })
        .catch(() => setError("Failed to load pipeline data."))
        .finally(() => setLoading(false));
    }
  }, [id]);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    try {
      await respondToPipeline(id || "", selectedOption, freeform);
      setSubmitted(true);
    } catch {
      setError("Failed to submit response. Please try again.");
    }
  }

  if (loading) {
    return (
      <div className="space-y-6 animate-pulse">
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
          <div className="h-6 bg-gray-800 rounded w-1/3 mb-3" />
          <div className="h-4 bg-gray-800 rounded w-3/4" />
        </div>
        <div className="h-24 bg-gray-800 rounded-lg" />
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

  if (pipeline && !pipeline.user_input_request) {
    return (
      <div className="bg-yellow-900/30 border border-yellow-700 rounded-lg p-6 text-center">
        <p className="text-yellow-400">This pipeline is not awaiting user input.</p>
        <a href={`/${id}/`} className="text-blue-400 hover:underline mt-2 inline-block">Back to pipeline</a>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="bg-green-900/30 border border-green-700 rounded-lg p-6 text-center">
        <p className="text-green-400 mb-3">Response submitted successfully.</p>
        <a href={`/${id}/`} className="text-blue-400 hover:underline">Back to pipeline</a>
      </div>
    );
  }

  if (!request) {
    return null;
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h2 className="text-lg font-semibold mb-2">{request.summary}</h2>
        {request.question && (
          <div className="text-sm text-gray-300 whitespace-pre-wrap">{request.question}</div>
        )}
      </div>

      {request.options && request.options.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-gray-400">Options</h3>
          {request.options.map((opt) => (
            <label key={opt.id} className="flex items-start gap-3 p-3 bg-gray-900 rounded-lg border border-gray-800 hover:border-gray-700 cursor-pointer">
              <input
                type="radio"
                name="selected_option"
                value={opt.id}
                checked={selectedOption === opt.id}
                onChange={(e) => setSelectedOption(e.target.value)}
                className="mt-0.5 accent-blue-500"
              />
              <div>
                <div className="text-sm font-medium text-gray-200">{opt.label}</div>
                {opt.description && <div className="text-xs text-gray-500 mt-1">{opt.description}</div>}
              </div>
            </label>
          ))}
        </div>
      )}

      <Textarea
        label="Your Response"
        placeholder="Provide additional guidance or context..."
        value={freeform}
        onChange={(e) => setFreeform(e.target.value)}
        rows={4}
      />

      {request.context_refs && request.context_refs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-gray-400">Related Context</h3>
          {request.context_refs.map((ref, i) => (
            <div key={i} className="text-xs text-gray-500">
              {ref.type === "file" && ref.path ? (
                <a href={`/${id}/files/?path=${ref.path}`} className="text-blue-400 hover:underline">
                  {ref.path}
                  {ref.line_start ? `:${ref.line_start}` : ""}
                  {ref.line_end ? `-${ref.line_end}` : ""}
                </a>
              ) : (
                <span className="text-gray-500">[{ref.type}]</span>
              )}
              {ref.note && <span className="ml-2 text-gray-600">— {ref.note}</span>}
            </div>
          ))}
        </div>
      )}

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-3">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      <Button type="submit" variant="primary">Submit Response</Button>
    </form>
  );
}

function getPipelineIdFromURL(): string {
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[0] || "";
}
