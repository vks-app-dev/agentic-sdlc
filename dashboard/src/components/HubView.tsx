import { useEffect, useMemo, useState } from "react";
import { agentTitle, dur, liveApi, since, type FlowStage, type GoLive, type LiveStage } from "../api";
import { AgentAvatar, Avatar } from "./Avatar";

const W = 900;
const H = 560;
const CX = W / 2;
const CY = 430;
const RX = 360;
const RY = 300;

/** The orchestrator in the centre, every stage lead around it; the stage in focus opens to show its sub-agents. */
export function HubView({ req, flow, current, running, user, goLive, version }: {
  req: string; flow: FlowStage[]; current: string | null; running: boolean; user: string; goLive: GoLive | null | undefined;
  version: number;
}) {
  // null: follow the current stage; "": everything closed; a stage name: that stage open
  const [picked, setPicked] = useState<string | null>(null);
  const focus = picked === "" ? null
    : picked ?? (flow.find((f) => f.stage === current)?.stage ?? flow[flow.length - 1]?.stage ?? null);
  const toggle = (stage: string) => setPicked(stage === focus ? "" : stage);
  const [live, setLive] = useState<LiveStage | null>(null);

  useEffect(() => {
    if (!focus) { setLive(null); return; }
    let alive = true;
    const load = () => liveApi.stage(req, focus).then((d) => { if (alive) setLive(d); }, () => undefined);
    load();
    const t = window.setInterval(load, 3000);
    return () => { alive = false; window.clearInterval(t); };
  }, [req, focus, version]);

  // the stages clockwise over the top, from the left to the go-live finish on the right
  const nodes = useMemo(() => {
    const all = [...flow.map((f) => ({ key: f.stage, f })), { key: "golive", f: null as FlowStage | null }];
    return all.map((n, i) => {
      const a = Math.PI + (Math.PI * i) / Math.max(1, all.length - 1);
      return { ...n, x: CX + RX * Math.cos(a), y: CY + RY * Math.sin(a) };
    });
  }, [flow]);

  const o = live?.orchestrator;
  const orchWorking = running || o?.status === "working";
  const crowded = nodes.length > 6;
  const idx = nodes.findIndex((n) => n.key === focus);

  return (
    <div className="hub">
      {(() => {
        const f = flow.find((x) => x.stage === focus);
        return f ? (
          <FocusPanel f={f} live={live} working={f.status === "in_progress" && orchWorking} onClose={() => toggle(f.stage)} />
        ) : null;
      })()}
      <div className="hub-stage" style={{ aspectRatio: `${W} / ${H}` }}>
        <svg className="hub-spokes" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          {nodes.map((n) => {
            const st = n.f?.status;
            const cls = n.key === "golive" ? (goLive?.state === "live" ? "done" : goLive ? "ready" : "")
              : st === "approved" ? "done" : st === "in_progress" && orchWorking && n.key === current ? "flowing"
              : st === "awaiting_approval" ? "due" : st === "in_progress" ? "active" : "";
            const mx = (CX + n.x) / 2;
            const my = (CY + n.y) / 2;
            return (
              <g key={n.key}>
                <line x1={CX} y1={CY} x2={n.x} y2={n.y} className={`spoke ${cls}`} />
                {n.f && n.f.gate.length > 0 && !n.f.human && (
                  <g className={`spoke-gate-g ${st === "approved" ? "done" : st === "awaiting_approval" ? "due" : ""}`}>
                    <title>{st === "approved" ? "Reviewed and approved" : st === "awaiting_approval" ? "Waiting for your review" : "You review"}: {n.f.gate.join(", ")}</title>
                    <circle cx={mx} cy={my} r={12} className={`spoke-gate ${st === "approved" ? "done" : st === "awaiting_approval" ? "due" : ""}`} />
                    {st === "approved" && <text x={mx} y={my} className="spoke-tick">✓</text>}
                    {st === "awaiting_approval" && <text x={mx} y={my} className="spoke-tick due">!</text>}
                  </g>
                )}
              </g>
            );
          })}
        </svg>

        <div className={`hub-center ${orchWorking ? "working" : ""}`} style={{ left: `${(CX / W) * 100}%`, top: `${(CY / H) * 100}%` }}>
          <Avatar seed="orchestrator-sdlc" stage="cicd" size={52} working={orchWorking} />
          <div className="hub-center-name">Orchestrator</div>
          <div className={`live-status ${orchWorking ? "working" : "done"}`}>{orchWorking ? "Directing" : "Idle"}</div>
          {o?.last && <div className="hub-center-act mono" title={o.last}>{o.last}</div>}
          {o && <div className="hub-center-meta">{o.actions} actions{o.last_ts ? ` · ${since(o.last_ts)} ago` : ""}</div>}
        </div>

        {nodes.map((n, i) => {
          const near = Math.min(Math.abs(i - idx), nodes.length - Math.abs(i - idx)) <= 1;
          if (n.key === "golive") {
            const g = goLive?.state;
            return (
              <div key="golive" className={`hub-node golive ${g ?? "pending"}`}
                style={{ left: `${(n.x / W) * 100}%`, top: `${(n.y / H) * 100}%` }}
                title={g === "live" ? "Live in production" : g ? "Ready to go live: you merge, deploy and record it" : "After the Release approval"}>
                <span className="hub-golive">{g === "live" ? "✓" : "🚀"}</span>
                <div className="hub-node-name">{g === "live" ? "Live" : "Go live"}</div>
                <div className="muted small">{g === "live" ? goLive?.deployed?.version ?? "" : g ? "Ready · you deploy" : "Done by you"}</div>
              </div>
            );
          }
          const f = n.f!;
          const done = f.agents.filter((a) => a.status === "done").length;
          const needed = f.agents.filter((a) => a.status !== "skipped").length;
          const pct = needed ? done / needed : f.status === "approved" ? 1 : 0;
          const open = n.key === focus;
          if (open) {
            return (
              <div key={n.key}>
                <button className={`hub-node ${f.status} open`} onClick={() => toggle(n.key)}
                  style={{ left: `${(n.x / W) * 100}%`, top: `${(n.y / H) * 100}%` }} title="Open above · click to close">
                  <span className="hub-ring" style={{ ["--p" as string]: `${pct * 360}deg` }}>
                    <Avatar seed={f.lead} stage={f.stage} size={52} />
                  </span>
                  <div className="hub-node-name">{f.title}</div>
                </button>
              </div>
            );
          }
          return (
            <button key={n.key} className={`hub-node ${f.status}`} onClick={() => toggle(n.key)}
              style={{ left: `${(n.x / W) * 100}%`, top: `${(n.y / H) * 100}%` }} title={`${f.lead} · ${f.title}`}>
              <span className="hub-ring" style={{ ["--p" as string]: `${pct * 360}deg` }}>
                <Avatar seed={f.human ? user : f.lead} stage={f.stage} size={52} person={f.human} />
              </span>
              {(!crowded || near) && <div className="hub-node-name">{f.title}</div>}
              {(!crowded || near) && (
                <div className="muted small">
                  {f.status === "approved" ? "✓ approved" : f.status === "awaiting_approval" ? "your review" : needed ? `${done}/${needed}` : "—"}
                </div>
              )}
            </button>
          );
        })}
      </div>
      <div className="hub-hint muted small">Click a stage to open it, click it again to close it. The ring around each lead fills as its sub-agents finish; the small circle on a spoke is your review.</div>
    </div>
  );
}

function FocusPanel({ f, live, working, onClose }: {
  f: FlowStage; live: LiveStage | null; working: boolean; onClose: () => void;
}) {
  const agents = live?.stage === f.stage ? live.agents ?? [] : [];
  const byName = Object.fromEntries(agents.map((a) => [a.name, a]));
  const steps = [...new Set(f.agents.map((a) => a.step))].sort((a, b) => a - b);
  const active = agents.filter((a) => a.status === "working");
  return (
    <div className={`hub-focus ${f.status} ${working ? "working" : ""}`}>
      <button className="hub-focus-head" onClick={onClose} title="Close: back to a circle">
        <Avatar seed={f.lead} stage={f.stage} size={40} working={working} />
        <div className="hub-focus-title">
          <div className="hub-node-name">{f.lead}</div>
          <div className="muted small">{f.title} · {f.agents.filter((a) => a.status === "done").length}/{f.agents.filter((a) => a.status !== "skipped").length} done
            {f.agents.some((a) => a.status === "skipped") ? ` · ${f.agents.filter((a) => a.status === "skipped").length} not needed for this item` : ""}</div>
        </div>
        <span className="hub-collapse">−</span>
      </button>
      <div className="hub-steps">
        {steps.map((s) => (
          <div key={s} className="hub-step">
            <span className="hub-step-n">Step {s}</span>
            <div className="hub-step-agents">
              {f.agents.filter((a) => a.step === s).map((a) => {
                const r = byName[a.name]?.runs.slice(-1)[0];
                return (
                  <span key={a.name} className={`hub-sub ${a.status}`}
                    title={a.status === "skipped" ? `${agentTitle(a.name)}: ${a.reason ?? "not needed"} (decided by the architect)` : `${agentTitle(a.name)}: ${a.status}${r?.last ? `\n${r.last}` : ""}${r?.took != null ? `\n${dur(r.took)}` : ""}${r?.result ? `\n${r.result}` : ""}`}>
                    <span className="hub-sub-face">
                      <AgentAvatar name={a.name} size={38} working={a.status === "working"} />
                      {a.status === "done" && <span className="crew-tick">✓</span>}
                    </span>
                    <span className="hub-sub-name">{agentTitle(a.name)}</span>
                    <span className={`hub-sub-state ${a.status}`}>{a.status === "skipped" ? "not needed" : a.status}</span>
                  </span>
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {active.length > 0 ? active.map((a) => (
        <div key={a.name} className="hub-active mono" title={a.runs.slice(-1)[0]?.last ?? ""}>
          ▸ <b>{agentTitle(a.name)}</b> {a.runs.slice(-1)[0]?.last ?? "starting…"}
        </div>
      )) : (
        <div className="muted small">
          {f.status === "approved" ? "Finished and approved" : f.status === "awaiting_approval" ? "Waiting for your review"
            : f.status === "in_progress" ? "No sub-agent working right now" : "Not started"}
        </div>
      )}
    </div>
  );
}
