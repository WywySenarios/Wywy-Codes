import { useCallback, useEffect, useRef, useState } from "react";
import { tailLog, type LogEntry } from "../../lib/api";

export function LogViewer({
  pipelineId,
  stage,
  entries: prefetchedEntries,
}: {
  pipelineId?: string;
  stage?: string;
  entries?: LogEntry[];
}) {
  const isStatic = prefetchedEntries !== undefined;
  const [entries, setEntries] = useState<LogEntry[]>(prefetchedEntries ?? []);
  const [raw, setRaw] = useState(false);
  const [follow, setFollow] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, []);

  // Sync when prefetched entries change (e.g. SPA data loads after mount).
  useEffect(() => {
    if (prefetchedEntries !== undefined) {
      setEntries(prefetchedEntries);
    }
  }, [prefetchedEntries]);

  useEffect(() => {
    if (isStatic) return;

    let cancelled = false;

    async function poll() {
      try {
        const newEntries = await tailLog(pipelineId!, stage!);
        if (!cancelled) setEntries(newEntries);
      } catch { /* ignore */ }
    }

    poll();
    const interval = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [pipelineId, stage, isStatic]);

  useEffect(() => {
    if (follow) scrollToBottom();
  }, [entries, follow, scrollToBottom]);

  function handleScroll() {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 2;
    setFollow(atBottom);
  }

  const levelColors: Record<string, string> = {
    INFO: "text-foreground",
    WARN: "text-yellow-400",
    ERROR: "text-red-400",
  };

  return (
    <div className="relative">
      {/* Raw toggle button */}
      <div className="absolute top-2 right-2 z-10 flex gap-1">
        <button
          onClick={() => setRaw(!raw)}
          className={`text-xs px-2 py-1 rounded transition-colors ${
            raw
              ? "bg-blue-600 text-white"
              : "bg-muted text-muted-foreground hover:bg-muted/80"
          }`}
        >
          {raw ? "Parsed" : "Raw"}
        </button>
      </div>

      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="bg-card border border-border p-4 h-80 overflow-y-auto font-mono text-xs"
      >
        {raw ? (
          <pre className="whitespace-pre-wrap text-foreground">
            {entries.length > 0 ? (
              <>
                Raw content:
                {"\n"}
                {JSON.stringify(entries, null, 2)}
              </>
            ) : (
              "No log entries yet."
            )}
          </pre>
        ) : entries.length === 0 ? (
          <p className="text-muted-foreground">No log entries yet.</p>
        ) : (
          entries.map((entry, i) => (
            <div key={i} className="flex gap-2 leading-relaxed">
              <span className="text-muted-foreground shrink-0">{entry.ts?.slice(11, 23) || entry.ts}</span>
              <span className={`shrink-0 w-10 ${levelColors[entry.level] || "text-muted-foreground"}`}>{entry.level}</span>
              <span className="text-foreground">{entry.msg}</span>
            </div>
          ))
        )}
      </div>
      {!follow && !raw && entries.length > 0 && (
        <button
          onClick={() => { scrollToBottom(); setFollow(true); }}
          className="absolute bottom-4 right-6 bg-blue-600 hover:bg-blue-700 text-foreground text-xs px-3 py-1.5 rounded-full shadow-lg transition-colors"
        >
          Follow
        </button>
      )}
    </div>
  );
}
