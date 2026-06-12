import { useState, useMemo } from "react";
import { createPipeline } from "../../lib/api";
import { Button } from "../ui/button";
import { Textarea } from "../ui/textarea";
import { pipelineUrl } from "../../lib/routes";

function toGitBranch(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-+/g, "-")
    .replace(/_+/g, "_");
}

export function NewPipelineForm() {
  const [prettyName, setPrettyName] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const branchName = useMemo(() => toGitBranch(prettyName), [prettyName]);

  async function handleSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError("");
    if (!branchName) {
      setError("Invocation name must contain at least one letter or digit.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await createPipeline(description, branchName);
      if (result.error) {
        setError(result.error);
        setSubmitting(false);
        return;
      }
      window.location.href = pipelineUrl(result.id);
    } catch {
      setError("Failed to create pipeline. Please try again.");
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="max-w-lg space-y-4">
      <div>
        <label className="block text-sm font-medium text-gray-300 mb-1">Invocation Name</label>
        <input
          type="text"
          value={prettyName}
          onChange={(e) => setPrettyName(e.target.value)}
          placeholder="My Feature Branch"
          required
          maxLength={100}
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
        {branchName && (
          <p className="text-xs text-blue-400 mt-1">
            <code className="bg-gray-800 px-1 rounded">{branchName}</code> will be used as the branch name
          </p>
        )}
        {!branchName && prettyName.trim() && (
          <p className="text-xs text-red-400 mt-1">Name must contain at least one letter or digit.</p>
        )}
      </div>

      <Textarea
        label="Description"
        placeholder="Describe what you want the pipeline to build..."
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        rows={4}
      />

      {error && (
        <div className="bg-red-900/30 border border-red-700 rounded-lg p-3">
          <p className="text-red-400 text-sm">{error}</p>
        </div>
      )}

      <Button type="submit" variant="primary" disabled={submitting}>
        {submitting ? "Creating..." : "Start Pipeline"}
      </Button>
    </form>
  );
}
