import { Badge } from "../ui/badge";

export function StatusBadge({ status }: { status: string }) {
  return <Badge status={status} />;
}

export function StageProgress({ stages }: { stages: { name: string; status: string }[] }) {
  const STAGE_ORDER = [
    "planner", "plan_reviewer", "test_builder", "testing_align_red",
    "coder", "code_reviewer", "testing_green", "pr_writer", "pr_reviewer",
  ];

  return (
    <div className="flex gap-1 flex-wrap">
      {STAGE_ORDER.map((name) => {
        const stage = stages?.find((s) => s.name === name);
        const status = stage?.status || "pending";
        const colors: Record<string, string> = {
          pending: "bg-gray-700",
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
