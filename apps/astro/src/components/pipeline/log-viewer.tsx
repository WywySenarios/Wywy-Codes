import { useCallback, useEffect, useRef, useState } from "react";
import { tailLog, type LogEntry } from "../../lib/api";

export function LogViewer({ pipelineId, stage }: { pipelineId: string; stage: string }) {
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [follow, setFollow] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = useCallback(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const newEntries = await tailLog(pipelineId, stage);
        if (!cancelled) setEntries(newEntries);
      } catch { /* ignore */ }
    }

    poll();
    const interval = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [pipelineId, stage]);

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
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="bg-card border border-border p-4 h-80 overflow-y-auto font-mono text-xs"
      >
        {entries.length === 0 ? (
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
      {!follow && entries.length > 0 && (
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
