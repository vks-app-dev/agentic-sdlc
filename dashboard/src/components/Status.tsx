const LABEL: Record<string, string> = {
  pending: "Pending",
  in_progress: "Running",
  running: "Running",
  in_review: "In review",
  awaiting_approval: "Waiting for you",
  approved: "Approved",
  done: "Done",
  skipped: "Off",
  blocked: "Blocked",
  failed: "Retrying",
};

const ICON: Record<string, string> = {
  pending: "",
  in_progress: "●",
  running: "●",
  in_review: "◐",
  awaiting_approval: "!",
  approved: "✓",
  done: "✓",
  skipped: "–",
  blocked: "✕",
  failed: "↻",
};

export function StatusBadge({ status, text, compact }: { status: string; text?: string; compact?: boolean }) {
  const live = status === "in_progress" || status === "running";
  return (
    <span className={`st ${status}${live ? " pulse" : ""}`} title={LABEL[status] ?? status}>
      {ICON[status]}
      {compact ? (text ? ` ${text}` : "") : ` ${text ?? LABEL[status] ?? status}`}
    </span>
  );
}

export const statusLabel = (s: string) => LABEL[s] ?? s;

const TYPE_LABEL: Record<string, string> = { feature: "Feature", change: "Change", bug: "Bug", task: "Task" };

export function TypeBadge({ type }: { type: string }) {
  return <span className={`wt ${type}`}>{TYPE_LABEL[type] ?? type}</span>;
}
