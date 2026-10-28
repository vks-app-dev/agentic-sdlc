import { useState } from "react";
import { typeOf, type Item, type Metrics, type Project } from "../api";
import { StatusBadge, TypeBadge } from "./Status";

const SHORT: Record<string, string> = {
  requirements: "Req", design: "Design", planning: "Plan", development: "Dev", cicd: "CI/CD",
  qa: "QA", release: "Release", production: "Prod", monitoring: "Monitor",
};

export function Pipeline({ items, project, metrics, selected, select }: {
  items: Item[]; project: Project | null; metrics: Metrics | null; selected: string | null; select: (id: string) => void;
}) {
  // only the stages agents actually work in here (switched on, with agents); production is shown as "Go live"
  const stages = (project?.stages ?? []).filter((s) => s.enabled !== false && s.automated && s.agents.length > 0);
  const goLive = (it: Item) => {
    if (it.stages.release?.status !== "approved") return null;
    const prod = (it.deployments ?? []).filter((d) => d.ok && /^(prod|production|live)$/i.test(d.env));
    return prod.length ? { live: true, version: prod[prod.length - 1].version } : { live: false };
  };
  const [type, setType] = useState<string>("all");
  const shown = type === "all" ? items : items.filter((i) => typeOf(i) === type);
  const types = (project?.work_types ?? []).filter((w) => items.some((i) => typeOf(i) === w.name));
  const cycle = (name: string) => {
    const m = metrics?.by_type.find((t) => t.type === name);
    return m ? `${m.open} open, ${m.done} done${m.avg_cycle_h != null ? `, ${m.avg_cycle_h} h average from created to done` : ""}` : "";
  };
  return (
    <section className="panel">
      <h2>
        Pipeline <span className="count">{shown.length}</span>
        {types.length > 1 && (
          <span className="type-filter">
            <button className={type === "all" ? "on" : ""} onClick={() => setType("all")}>All</button>
            {types.map((w) => (
              <button key={w.name} className={type === w.name ? "on" : ""} title={cycle(w.name)} onClick={() => setType(w.name)}>
                {w.title} {items.filter((i) => typeOf(i) === w.name).length}
              </button>
            ))}
          </span>
        )}
      </h2>
      {items.length === 0 ? (
        <div className="empty">
          No work items yet. Run <code>/sdlc-new "title"</code> in Claude Code.
        </div>
      ) : (
        <div className="scroll-x">
          <table className="pipe">
            <thead>
              <tr>
                <th style={{ paddingLeft: 14 }}>Item</th>
                <th>Type</th>
                <th>Title</th>
                {stages.map((s) => (
                  <th key={s.name} className={s.enabled === false ? "off" : ""} title={`${s.title}: ${s.purpose}`}>
                    {SHORT[s.name] ?? s.title}
                  </th>
                ))}
                <th title="Merged, deployed and recorded by a person after the Release approval">Go live</th>
              </tr>
            </thead>
            <tbody>
              {[...shown].reverse().map((it) => (
                <tr key={it.id} className={it.id === selected ? "sel" : ""} onClick={() => select(it.id)}>
                  <td className="id" style={{ paddingLeft: 14 }}>{it.id}</td>
                  <td><TypeBadge type={typeOf(it)} /></td>
                  <td className="t" title={it.title}>{it.title}</td>
                  {stages.map((s) => {
                    const st = it.stages[s.name];
                    let text: string | undefined;
                    if (s.name === "development" && it.tasks.length && st.status === "in_progress") {
                      text = `${it.tasks.filter((t) => t.status === "done").length}/${it.tasks.length}`;
                    }
                    return (
                      <td key={s.name} className={s.enabled === false ? "off" : ""}>
                        <StatusBadge status={st.status} text={text} compact />
                      </td>
                    );
                  })}
                  <td>
                    {(() => {
                      const g = goLive(it);
                      return !g ? <span className="golive-cell none" title="After the Release approval">–</span>
                        : g.live ? <span className="golive-cell live" title="Live in production">✓ Live{g.version ? ` ${g.version}` : ""}</span>
                        : <span className="golive-cell ready" title="Ready to go live: a person merges, deploys and records it">🚀 Ready</span>;
                    })()}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
