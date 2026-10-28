import { useEffect, useState } from "react";
import { api, tokens, usd, type Usage } from "../api";
import { AgentAvatar } from "./Avatar";

/** AI usage for the selected work item (or every item), in the right-hand column above Activity. */
export function UsagePanel({ id, version }: { id: string | null; version: number }) {
  const [use, setUse] = useState<Usage | null>(null);
  const [dim, setDim] = useState<"by_stage" | "by_agent">("by_stage");

  useEffect(() => {
    api.usage(id ?? undefined).then(setUse, () => setUse(null));
  }, [id, version]);

  const rows = use?.[dim] ?? [];
  const max = Math.max(1, ...rows.map((r) => r.tokens));
  return (
    <section className="panel usage-panel">
      <h2>
        AI usage <span className="count">{id ?? "all items"}</span>
      </h2>
      {!use || use.total.tokens === 0 ? (
        <div className="empty">No AI usage recorded{id ? ` for ${id}` : ""} yet.</div>
      ) : (
        <div className="usage-body">
          <div className="usage-totals" title={use.note}>
            <div><b>{tokens(use.total.tokens)}</b><span>tokens</span></div>
            <div><b>~{usd(use.total.cost_usd)}</b><span>estimated</span></div>
            {use.runs_actual_usd ? <div><b>{usd(use.runs_actual_usd)}</b><span>by runs</span></div> : null}
          </div>
          <div className="tabs usage-tabs">
            <button className={dim === "by_stage" ? "on" : ""} onClick={() => setDim("by_stage")}>By stage</button>
            <button className={dim === "by_agent" ? "on" : ""} onClick={() => setDim("by_agent")}>By agent</button>
          </div>
          <div className="usage-rows">
            {rows.slice(0, 8).map((r) => (
              <div key={r.name} className="usage-row"
                title={r.unpriced_tokens ? `${tokens(r.unpriced_tokens)} tokens on a model with no price` : ""}>
                <span className="usage-name">
                  {dim === "by_agent" && r.name !== "orchestrator" && <AgentAvatar name={r.name} size={18} />}
                  {r.name}
                </span>
                <span className="usage-num">{tokens(r.tokens)}</span>
                <span className="usage-num">~{usd(r.cost_usd)}</span>
                <span className="usage-bar"><span style={{ width: `${(r.tokens / max) * 100}%` }} /></span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
