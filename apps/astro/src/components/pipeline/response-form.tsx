import { useState, useEffect } from "react";
import { respondToPipeline, getPipeline, type AgentRequest, type Pipeline } from "../../lib/api";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { getPipelineIdFromURL, pipelineUrl, pipelineFilesUrl } from "../../lib/routes";

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
        <div className="bg-card border border-border p-4">
          <div className="h-6 bg-muted rounded w-1/3 mb-3" />
          <div className="h-4 bg-muted rounded w-3/4" />
        </div>
        <div className="h-24 bg-muted rounded-lg" />
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
        <a href={pipelineUrl(id)} className="text-blue-400 hover:underline mt-2 inline-block">Back to pipeline</a>
      </div>
    );
  }

  if (submitted) {
    return (
      <div className="bg-green-900/30 border border-green-700 rounded-lg p-6 text-center">
        <p className="text-green-400 mb-3">Response submitted successfully.</p>
        <a href={pipelineUrl(id)} className="text-blue-400 hover:underline">Back to pipeline</a>
      </div>
    );
  }

  if (!request) {
    return null;
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div className="bg-card border border-border p-4">
        <h2 className="text-lg font-semibold mb-2">{request.summary}</h2>
        {request.question && (
          <div className="text-sm text-foreground whitespace-pre-wrap">{request.question}</div>
        )}
      </div>

      {request.options && request.options.length > 0 && (
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-muted-foreground">Options</h3>
          {request.options.map((opt) => (
            <label key={opt.id} className="flex items-start gap-3 p-3 bg-card border border-border hover:border-border cursor-pointer">
              <input
                type="radio"
                name="selected_option"
                value={opt.id}
                checked={selectedOption === opt.id}
                onChange={(e) => setSelectedOption(e.target.value)}
                className="mt-0.5 accent-blue-500"
              />
              <div>
                <div className="text-sm font-medium text-foreground">{opt.label}</div>
                {opt.description && <div className="text-xs text-muted-foreground mt-1">{opt.description}</div>}
              </div>
            </label>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-1">
        <label className="text-sm font-medium text-foreground">Your Response</label>
        <Textarea
          placeholder="Provide additional guidance or context..."
          value={freeform}
          onChange={(e) => setFreeform(e.target.value)}
          rows={4}
        />
      </div>

      {request.context_refs && request.context_refs.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">Related Context</h3>
          {request.context_refs.map((ref, i) => (
            <div key={i} className="text-xs text-muted-foreground">
              {ref.type === "file" && ref.path ? (
                <a href={pipelineFilesUrl(id, { path: ref.path })} className="text-blue-400 hover:underline">
                  {ref.path}
                  {ref.line_start ? `:${ref.line_start}` : ""}
                  {ref.line_end ? `-${ref.line_end}` : ""}
                </a>
              ) : (
                <span className="text-muted-foreground">[{ref.type}]</span>
              )}
              {ref.note && <span className="ml-2 text-muted-foreground">— {ref.note}</span>}
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


