import { useEffect, useState } from "react";
import { since, tokens, usd } from "../api";

interface Waiting { req: string; title: string; stage: string; gate: string[]; hours: number | null; project?: string }
interface ProjectRow {
  path: string; name: string; org?: string; error?: string; requires?: string;
  items: { total: number; active: number; blocked: number; done: number };
  in_stage: Record<string, number>; waiting: Waiting[]; defects_open: number; blockers_open: number;
  lead_time_h: number | null; rework_rate: number | null;
  ai: { tokens: number; cost_usd: number; per_done_item_usd: number | null }; last_activity: string | null;
}
interface Data {
  projects: ProjectRow[];
  totals: { projects: number; items_in_flight: number; gates_waiting: number; blocked: number; blockers_open: number;
            ai_cost_usd: number; ai_tokens: number };
  waiting: Waiting[];
}

const hours = (h: number | null) => (h == null ? "-" : h >= 48 ? `${Math.round(h / 24)} d` : `${Math.round(h)} h`);

/** Every project at once (read-only): where work is, what waits for people, and what it costs. */
export function Portfolio() {
  const [data, setData] = useState<Data | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    const load = () => fetch("/api/portfolio").then((r) => r.json()).then(setData, (e: Error) => setErr(e.message));
    load();
    const t = window.setInterval(load, 30000);
    return () => window.clearInterval(t);
  }, []);
  if (err) return <div className="app"><div className="error">{err}</div></div>;
  if (!data) return <div className="app"><div className="empty">Loading the portfolio…</div></div>;
  const t = data.totals;
  const org = data.projects.find((p) => p.org)?.org;
  const tiles = [
    { v: t.projects, l: "Projects" },
    { v: t.items_in_flight, l: "Work items in flight" },
    { v: t.gates_waiting, l: "Gates waiting for people", cls: t.gates_waiting ? "warn" : "" },
    { v: t.blocked, l: "Blocked items", cls: t.blocked ? "bad" : "" },
    { v: t.blockers_open, l: "Release blockers open", cls: t.blockers_open ? "bad" : "" },
    { v: t.ai_tokens ? `~${usd(t.ai_cost_usd)}` : "–", l: t.ai_tokens ? `AI cost, est. · ${tokens(t.ai_tokens)} tokens` : "AI cost, est." },
  ];
  return (
    <div className="app">
      <header className="top">
        <div className="brand">Agentic SDLC <span>/ portfolio{org ? ` · ${org}` : ""}</span></div>
        <span className="spacer" />
        <span className="chip">read-only · refreshes every 30 s</span>
      </header>
      <div className="kpis">
        {tiles.map((x) => (
          <div key={x.l} className={`kpi ${x.cls ?? ""}`}><div className="v">{x.v}</div><div className="l">{x.l}</div></div>
        ))}
      </div>
      <section className="panel">
        <h2>Projects <span className="count">{data.projects.length}</span></h2>
        <div className="scroll-x">
          <table className="pipe portfolio">
            <thead>
              <tr>
                <th style={{ paddingLeft: 14 }}>Project</th><th>Active</th><th>Blocked</th><th>Done</th><th>Where</th>
                <th>Waiting</th><th>Defects</th><th>Lead time</th><th>Rework</th><th>AI cost</th><th>Last activity</th>
              </tr>
            </thead>
            <tbody>
              {data.projects.map((p) => p.error ? (
                <tr key={p.path}><td style={{ paddingLeft: 14 }}>{p.name}</td><td colSpan={10} className="error">{p.error}</td></tr>
              ) : (
                <tr key={p.path} title={p.path}>
                  <td style={{ paddingLeft: 14 }}><b>{p.name}</b>{p.requires ? <span className="muted"> · needs {p.requires}</span> : null}</td>
                  <td>{p.items.active}</td>
                  <td className={p.items.blocked ? "bad" : ""}>{p.items.blocked}</td>
                  <td>{p.items.done}</td>
                  <td className="muted">{Object.entries(p.in_stage).map(([s, n]) => `${s} ${n}`).join(", ") || "-"}</td>
                  <td className={p.waiting.length ? "warn" : ""}>{p.waiting.length}</td>
                  <td>{p.defects_open}{p.blockers_open ? <span className="bad"> ({p.blockers_open} blocking)</span> : null}</td>
                  <td>{hours(p.lead_time_h)}</td>
                  <td>{p.rework_rate == null ? "-" : `${Math.round(p.rework_rate * 100)}%`}</td>
                  <td title={p.ai.per_done_item_usd != null ? `~${usd(p.ai.per_done_item_usd)} per finished item` : ""}>
                    {p.ai.tokens ? `~${usd(p.ai.cost_usd)}` : "-"}
                  </td>
                  <td className="muted">{p.last_activity ? `${since(p.last_activity)} ago` : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="panel" style={{ marginTop: 16 }}>
        <h2>Waiting for people <span className="count">{data.waiting.length}</span></h2>
        {data.waiting.length === 0 ? <div className="empty">No gate is waiting.</div> : (
          <table className="pipe portfolio">
            <thead><tr><th style={{ paddingLeft: 14 }}>Project</th><th>Item</th><th>Gate</th><th>Approvers</th><th>Waiting</th></tr></thead>
            <tbody>
              {data.waiting.map((w) => (
                <tr key={`${w.project}-${w.req}`}>
                  <td style={{ paddingLeft: 14 }}>{w.project}</td>
                  <td><span className="mono">{w.req}</span> {w.title}</td>
                  <td>{w.stage}</td>
                  <td>{w.gate.join(", ")}</td>
                  <td className={(w.hours ?? 0) > 24 ? "bad" : ""}>{hours(w.hours)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
