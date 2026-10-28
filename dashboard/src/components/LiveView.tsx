import { useEffect, useState } from "react";
import { agentTitle, clock, dur, liveApi, since, type LiveStage } from "../api";
import { AgentAvatar, Avatar } from "./Avatar";

const TITLES: Record<string, string> = {
  requirements: "Requirements", design: "Design", planning: "Planning", development: "Development", qa: "QA", release: "Release",
};
const STATUS = { done: "Done", working: "Working", waiting: "Waiting", skipped: "Not needed" };

/** A stage as a control room: the orchestrator directing the stage lead and its sub-agents, live. */
export function LiveView({ req, current, version, user }: { req: string; current: string | null; version: number; user: string }) {
  const [stage, setStage] = useState<string | undefined>(undefined);
  const [data, setData] = useState<LiveStage | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => liveApi.stage(req, stage).then((d) => { if (alive) setData(d); }, () => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, [req, stage, version]);

  if (!data || !data.stage) return <div className="empty">No agent activity yet.</div>;
  const o = data.orchestrator!;
  const agents = data.agents ?? [];
  const steps = [...new Set(agents.filter((a) => a.planned).map((a) => a.step))].sort((a, b) => a - b);
  const extra = agents.filter((a) => !a.planned);
  const working = agents.some((a) => a.status === "working") || o.status === "working";
  const gateDone = data.status === "approved";
  const gateDue = data.status === "awaiting_approval";

  return (
    <div className="live-view">
      <div className="live-top">
        <div className="live-stages">
          {data.stages.map((s) => (
            <button key={s} className={`live-stage ${s === data.stage ? "on" : ""} ${s === current ? "current" : ""}`}
              onClick={() => setStage(s)}>
              {TITLES[s] ?? s}{s === current ? " · now" : ""}
            </button>
          ))}
        </div>
        <span className={`live-pill ${working ? "on" : ""}`}>
          {working ? <><span className="dot-live pulse" /> Live</> : data.status === "approved" ? "Finished" : "Idle"}
        </span>
      </div>

      <div className="live-grid">
        <div className="live-tree">
          <div className={`live-node orch ${o.status}`}>
            <Avatar seed="orchestrator-sdlc" stage="cicd" size={52} working={o.status === "working"} />
            <div className="live-node-body">
              <div className="live-name">Orchestrator <span className="muted small">/sdlc-run {req}</span></div>
              <div className="live-act mono" title={o.last ?? ""}>{o.last ?? "no actions yet"}</div>
              <div className="muted small">{o.actions} actions{o.last_ts ? ` · last ${since(o.last_ts)} ago` : ""}</div>
            </div>
            <span className={`live-status ${o.status === "working" ? "working" : "done"}`}>{o.status === "working" ? "Directing" : "Idle"}</span>
          </div>
          <div className={`live-link ${working ? "flowing" : ""}`} />
          <div className={`live-node lead ${data.status}`}>
            <Avatar seed={data.lead ?? data.stage} stage={data.stage} size={48} working={data.status === "in_progress" && working} />
            <div className="live-node-body">
              <div className="live-name">{data.lead}</div>
              <div className="muted small">{data.title} · {agents.filter((a) => a.status === "done").length}/{agents.filter((a) => a.planned && a.status !== "skipped").length} sub-agents done</div>
            </div>
          </div>

          {steps.map((s) => {
            const lane = agents.filter((a) => a.planned && a.step === s);
            const laneWorking = lane.some((a) => a.status === "working");
            return (
              <div key={s} className="live-lane">
                <div className={`live-link ${laneWorking ? "flowing" : ""}`} />
                <div className="live-lane-label">Step {s}{lane.length > 1 ? ` · ${lane.length} in parallel` : ""}</div>
                <div className="live-agents">
                  {lane.map((a) => <AgentCard key={a.name} a={a} />)}
                </div>
              </div>
            );
          })}
          {extra.length > 0 && (
            <div className="live-lane">
              <div className="live-link" />
              <div className="live-lane-label">Not in the plan</div>
              <div className="live-agents">{extra.map((a) => <AgentCard key={a.name} a={a} />)}</div>
            </div>
          )}
          {!!data.gate?.length && (
            <>
              <div className="live-link" />
              <div className={`live-node gate ${gateDone ? "done" : gateDue ? "due" : ""}`}>
                <Avatar seed={user} stage="production" size={40} person />
                <div className="live-node-body">
                  <div className="live-name">You review <span className="muted small">{data.gate.join(", ")}</span></div>
                  <div className="muted small">{gateDone ? "Approved: the next stage can start" : gateDue ? "Waiting for your review now" : "After the agents finish"}</div>
                </div>
                <span className={`live-status ${gateDone ? "done" : gateDue ? "due" : "waiting"}`}>{gateDone ? "Approved" : gateDue ? "Your turn" : "Later"}</span>
              </div>
            </>
          )}
        </div>

        <div className="live-feed">
          <div className="section-label">Handoffs</div>
          {(data.handoffs ?? []).length === 0 && <div className="muted small">Nothing yet.</div>}
          {(data.handoffs ?? []).map((h, i) => (
            <div key={i} className={`handoff ${h.kind}`}>
              <span className="handoff-time mono">{clock(h.ts)}</span>
              <span className="handoff-who">
                {h.kind === "dispatch" ? <><span className="mini-orch">◆</span> → <AgentAvatar name={h.agent} size={18} /></>
                  : h.kind === "done" ? <><AgentAvatar name={h.agent} size={18} /> ✓</>
                  : h.kind === "orchestrator" ? <span className="mini-orch">◆</span>
                  : h.kind === "human" ? "👤" : "⚙"}
              </span>
              <span className="handoff-text">
                <b>{h.kind === "dispatch" ? `Orchestrator → ${agentTitle(h.agent)}`
                  : h.kind === "done" ? `${agentTitle(h.agent)} finished${h.took != null ? ` in ${dur(h.took)}` : ""}`
                  : h.kind === "orchestrator" ? "Orchestrator" : h.kind === "human" ? "Person / gate" : "Engine"}</b>{" "}
                <span className="muted">{h.text}</span>
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function AgentCard({ a }: { a: NonNullable<LiveStage["agents"]>[number] }) {
  const r = a.runs[a.runs.length - 1];
  return (
    <div className={`live-agent ${a.status}`}>
      <div className="live-agent-top">
        <AgentAvatar name={a.name} size={40} working={a.status === "working"} />
        <div className="live-node-body">
          <div className="live-name">{agentTitle(a.name)}</div>
          <span className={`live-status ${a.status}`}>{a.status === "done" ? "✓ " : ""}{STATUS[a.status]}</span>
        </div>
      </div>
      {r ? (
        <>
          <div className="live-act mono" title={r.last ?? ""}>{a.status === "working" ? "▸ " : ""}{r.last ?? "starting…"}</div>
          <div className="muted small">
            {r.task ? `${r.task} · ` : ""}{r.stop ? dur(r.took) : `${since(r.start)} so far`} · {r.actions} actions
            {a.runs.length > 1 ? ` · ${a.runs.length} runs` : ""}
          </div>
          {r.result && a.status === "done" && <div className="live-result" title={r.result}>{r.result}</div>}
        </>
      ) : (
        <div className="muted small">{a.status === "skipped" ? a.reason ?? "Not needed for this item"
          : a.status === "done" ? "Done (no activity recorded)" : "Waiting for its turn"}</div>
      )}
    </div>
  );
}
