import type { Task } from "../api";
import { statusLabel } from "./Status";

const W = 188, H = 52, GX = 56, GY = 14, PAD = 6;

const COLOR: Record<string, [string, string]> = {
  pending: ["var(--surface)", "var(--border)"],
  running: ["var(--running-bg)", "var(--running)"],
  in_review: ["var(--running-bg)", "var(--running)"],
  done: ["var(--done-bg)", "var(--done)"],
  failed: ["var(--waiting-bg)", "var(--waiting)"],
  blocked: ["var(--blocked-bg)", "var(--blocked)"],
};

/** Tasks laid out left→right by dependency depth; tasks in one column can run in parallel. */
export function TaskDag({ tasks, reviews }: { tasks: Task[]; reviews: Record<string, string> }) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const depth = new Map<string, number>();
  const d = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const deps = byId.get(id)?.deps ?? [];
    const v = deps.length ? Math.max(...deps.map((x) => d(x, seen))) + 1 : 0;
    depth.set(id, v);
    return v;
  };
  tasks.forEach((t) => d(t.id));
  const cols: Task[][] = [];
  tasks.forEach((t) => (cols[depth.get(t.id)!] ??= []).push(t));
  const pos = new Map<string, { x: number; y: number }>();
  cols.forEach((col, ci) => col.forEach((t, ri) => pos.set(t.id, { x: PAD + ci * (W + GX), y: PAD + 18 + ri * (H + GY) })));
  const width = PAD * 2 + cols.length * W + (cols.length - 1) * GX;
  const height = PAD * 2 + 18 + Math.max(...cols.map((c) => c.length)) * (H + GY) - GY;

  return (
    <svg width={width} height={height} role="img" aria-label="Task dependency graph">
      <defs>
        <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
          <path d="M0,0 L10,5 L0,10 z" fill="var(--faint)" />
        </marker>
      </defs>
      {cols.map((_, ci) => (
        <text key={ci} x={PAD + ci * (W + GX)} y={PAD + 10} fontSize="11" fill="var(--muted)">
          {ci === 0 ? "Start now" : `Wave ${ci + 1}`}
          {cols[ci].length > 1 ? ` · ${cols[ci].length} in parallel` : ""}
        </text>
      ))}
      {tasks.flatMap((t) =>
        t.deps.filter((dep) => pos.has(dep)).map((dep) => {
          const a = pos.get(dep)!, b = pos.get(t.id)!;
          // Run straight along the source row through any skipped columns, then curve in the last gap,
          // so edges that skip a wave don't pass under other task boxes.
          const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 2, y2 = b.y + H / 2;
          const xb = b.x - GX, mx = (xb + x2) / 2;
          return (
            <path key={`${dep}-${t.id}`} d={`M${x1},${y1} L${xb},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`}
              fill="none" stroke="var(--faint)" strokeWidth="1.5" markerEnd="url(#arr)" />
          );
        }),
      )}
      {tasks.map((t) => {
        const p = pos.get(t.id)!;
        const [fill, stroke] = COLOR[t.status] ?? COLOR.pending;
        const verdict = reviews[t.id];
        const sub = `${statusLabel(t.status)}${t.attempts > 1 ? ` · try ${t.attempts}` : ""}${verdict ? ` · ${verdict === "APPROVE" ? "review ✓" : "changes asked"}` : ""}`;
        return (
          <g key={t.id} transform={`translate(${p.x},${p.y})`}>
            <title>{`${t.id}: ${t.title}\n${t.branch}\nACs: ${t.acceptance.join(", ") || "–"}\nFiles: ${t.files.join(", ") || "–"}`}</title>
            <rect width={W} height={H} rx="8" fill={fill} stroke={stroke} strokeWidth="1.5"
              strokeDasharray={t.status === "pending" ? "4 3" : undefined} />
            <text x="10" y="20" fontSize="12.5" fontWeight="600" fill="var(--text)">
              {t.id} {t.title.length > 20 ? `${t.title.slice(0, 19)}…` : t.title}
            </text>
            <text x="10" y="38" fontSize="11" fill={stroke === "var(--border)" ? "var(--muted)" : stroke}>{sub}</text>
          </g>
        );
      })}
    </svg>
  );
}
