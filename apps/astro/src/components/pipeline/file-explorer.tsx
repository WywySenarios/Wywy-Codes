import { useState, useEffect } from "react";
import { getFileContent, listFiles, type FileListing } from "../../lib/api";
import { getPipelineIdFromURL } from "../../lib/routes";

export function FileExplorer({ pipelineId, verbose }: { pipelineId?: string; verbose: boolean }) {
  const [files, setFiles] = useState<FileListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [contentLoading, setContentLoading] = useState(false);

  const id = pipelineId && pipelineId !== "_spa" ? pipelineId : getPipelineIdFromURL();

  useEffect(() => {
    if (id) {
      listFiles(id, verbose).then(setFiles).catch(() => setError("Failed to load file listing.")).finally(() => setLoading(false));
    }
  }, [id, verbose]);

  async function openFile(path: string) {
    setSelectedFile(path);
    setContentLoading(true);
    try {
      const text = await getFileContent(id || "", path);
      setContent(text);
    } catch {
      setContent("Failed to load file.");
    }
    setContentLoading(false);
  }

  const sections: { key: keyof FileListing; title: string }[] = [
    { key: "artifacts", title: "Artifacts" },
    { key: "summaries", title: "Summaries" },
    { key: "user_input", title: "User Input" },
    { key: "logs", title: "Logs" },
    { key: "other", title: "Other" },
  ];

  if (loading) {
    return (
      <div className="flex gap-6 animate-pulse">
        <div className="w-72 space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-20 bg-gray-800 rounded-lg" />
          ))}
        </div>
        <div className="flex-1">
          <div className="h-48 bg-gray-800 rounded-lg" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="bg-red-900/30 border border-red-700 rounded-lg p-6 text-center">
        <p className="text-red-400">{error}</p>
      </div>
    );
  }

  if (!files) {
    return (
      <div className="text-gray-500 text-center py-12">Loading files...</div>
    );
  }

  return (
    <div className="flex gap-6">
      <div className="w-72 shrink-0 space-y-4">
        {sections.map(({ key, title }) => {
          const items = files[key];
          if (!items || items.length === 0) return null;
          return (
            <div key={key} className="bg-gray-900 rounded-lg border border-gray-800 p-3">
              <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">{title}</h3>
              <ul className="space-y-1">
                {items.map((f) => (
                  <li key={f.path}>
                    <button
                      onClick={() => openFile(f.path)}
                      className={`text-left text-sm font-mono w-full truncate hover:text-blue-400 ${selectedFile === f.path ? "text-blue-400" : "text-gray-300"}`}
                    >
                      {f.path}
                      <span className="text-gray-600 ml-1 text-xs">({f.size}B)</span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
        {!sections.some((s) => files[s.key] && files[s.key].length > 0) && (
          <p className="text-gray-600 text-sm">No files found.</p>
        )}
      </div>

      <div className="flex-1">
        {selectedFile ? (
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-mono text-gray-300">{selectedFile}</h2>
              <button
                onClick={() => { setSelectedFile(null); setContent(null); }}
                className="text-gray-500 hover:text-gray-300 text-xs"
              >
                Close
              </button>
            </div>
            {contentLoading ? (
              <p className="text-gray-600 text-sm">Loading...</p>
            ) : (
              <pre className="text-xs text-gray-300 whitespace-pre-wrap overflow-x-auto max-h-[70vh] overflow-y-auto">{content}</pre>
            )}
          </div>
        ) : (
          <div className="bg-gray-900 rounded-lg border border-gray-800 p-8 text-center text-gray-600">
            Select a file to view its contents.
          </div>
        )}
      </div>
    </div>
  );
}


