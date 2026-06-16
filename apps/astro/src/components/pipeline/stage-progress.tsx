import { Badge } from "../ui/badge";

const STATUS_STYLES: Record<string, string> = {
  queued: "bg-muted text-foreground",
  running: "bg-blue-900/50 text-blue-300 border-blue-700",
  completed: "bg-green-900/50 text-green-300 border-green-700",
  failed: "bg-destructive/10 text-destructive border-destructive",
  cancelled: "bg-yellow-900/50 text-yellow-300 border-yellow-700",
  blocked: "bg-yellow-900/50 text-yellow-300 border-yellow-700",
  pending: "bg-muted text-muted-foreground",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <Badge variant="outline" className={STATUS_STYLES[status] || STATUS_STYLES.pending}>
      {status}
    </Badge>
  );
}

export function StageProgress({ stages }: { stages: { name: string; status: string }[] }) {
  const STAGE_ORDER = [
    "RED", "GREEN", "REFRACTOR", "compilance", "PR writer",
  ];

  return (
    <div className="flex gap-1 flex-wrap">
      {STAGE_ORDER.map((name) => {
        const stage = stages?.find((s) => s.name === name);
        const status = stage?.status || "pending";
        const colors: Record<string, string> = {
          pending: "bg-muted",
          running: "bg-blue-500",
          blocked: "bg-yellow-500",
          completed: "bg-green-500",
          failed: "bg-red-500",
        };
        return (
          <span
            key={name}
            className={`w-2.5 h-2.5 rounded-full ${colors[status] || colors.pending}`}
            title={`${name}: ${status}`}
          />
        );
      })}
    </div>
  );
}
