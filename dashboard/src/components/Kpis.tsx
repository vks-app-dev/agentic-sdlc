import { tokens, usd, type Item, type LiveAgent, type Metrics } from "../api";

export function Kpis({ items, metrics, live }: { items: Item[]; metrics: Metrics | null; live: LiveAgent[] }) {
  const waiting = items.filter((i) => i.current_stage && i.stages[i.current_stage]?.status === "awaiting_approval").length;
  const blocked = items.filter((i) => i.status === "blocked").length;
  const tasks = metrics?.tasks;
  const rework = metrics?.gates.rework_rate;
  const tiles: { v: string | number; l: string; cls?: string; title?: string }[] = [
    { v: items.filter((i) => i.status !== "done").length, l: "Work items in flight" },
    { v: live.length, l: "Agents running now" },
    { v: waiting, l: "Gates waiting for you", cls: waiting ? "warn" : "" },
    { v: blocked, l: "Blocked items", cls: blocked ? "bad" : "" },
    { v: tasks ? `${tasks.done}/${tasks.total}` : "–", l: "Tasks done" },
    { v: rework == null ? "–" : `${Math.round(rework * 100)}%`, l: "Gates sent back (rework)" },
    { v: metrics?.ai.total.tokens ? `~${usd(metrics.ai.total.cost_usd)}` : "–",
      l: metrics?.ai.total.tokens ? `AI cost, est. · ${tokens(metrics.ai.total.tokens)} tokens` : "AI cost, est.",
      title: metrics?.ai.note },
  ];
  return (
    <div className="kpis">
      {tiles.map((t) => (
        <div key={t.l} className={`kpi ${t.cls ?? ""}`} title={t.title}>
          <div className="v">{t.v}</div>
          <div className="l">{t.l}</div>
        </div>
      ))}
    </div>
  );
}
