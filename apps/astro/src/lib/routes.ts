/** Shared URL helpers for pipeline routing. */

/** Extract the pipeline ID from the current URL.
 *
 * Primary: query string `?id=<uuid>` (works in dev with _spa routes).
 * Fallback: first path segment for backward compat (`/<uuid>/...`).
 */
export function getPipelineIdFromURL(): string {
  // Primary: query string ?id=<uuid> (works in dev with _spa routes)
  const params = new URLSearchParams(window.location.search);
  const queryId = params.get("id");
  if (queryId) return queryId;

  // Fallback: first path segment for backward compat (/<uuid>/...)
  const parts = window.location.pathname.split("/").filter(Boolean);
  return parts[0] || "";
}

/** Build a pipeline detail URL: /_spa/?id=<uuid> */
export function pipelineUrl(id: string): string {
  return `/_spa/?id=${encodeURIComponent(id)}`;
}

/** Build a pipeline respond URL: /_spa/respond/?id=<uuid> */
export function pipelineRespondUrl(id: string): string {
  return `/_spa/respond/?id=${encodeURIComponent(id)}`;
}

/** Build a pipeline files URL: /_spa/files/?id=<uuid>[&path=<path>][&verbose=1] */
export function pipelineFilesUrl(
  id: string,
  opts?: { path?: string; verbose?: boolean },
): string {
  const params = new URLSearchParams({ id });
  if (opts?.path) params.set("path", opts.path);
  if (opts?.verbose) params.set("verbose", "1");
  return `/_spa/files/?${params.toString()}`;
}
