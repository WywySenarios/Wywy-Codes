const STATUS_COLORS: Record<string, string> = {
  queued: "bg-gray-700 text-gray-300",
  running: "bg-blue-900/50 text-blue-300 border border-blue-700",
  completed: "bg-green-900/50 text-green-300 border border-green-700",
  failed: "bg-red-900/50 text-red-300 border border-red-700",
  cancelled: "bg-yellow-900/50 text-yellow-300 border border-yellow-700",
  blocked: "bg-yellow-900/50 text-yellow-300 border border-yellow-700",
  pending: "bg-gray-800 text-gray-400",
};

export function Badge({ status, className = "" }: { status: string; className?: string }) {
  const color = STATUS_COLORS[status] || STATUS_COLORS.pending;
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-full text-xs font-medium ${color} ${className}`}>
      {status}
    </span>
  );
}
