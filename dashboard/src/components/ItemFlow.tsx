import { useState } from "react";
import { agentTitle, since, type FlowStage, type GoLive, type Run } from "../api";
import { AgentAvatar, Avatar } from "./Avatar";

const STAGE_STATE: Record<string, { label: string; cls: string }> = {
  approved: { label: "Approved", cls: "done" }, awaiting_approval: { label: "Your review", cls: "waiting" },
  in_progress: { label: "Working", cls: "working" }, blocked: { label: "Blocked", cls: "blocked" },
  pending: { label: "Not started", cls: "pending" }, skipped: { label: "Skipped", cls: "pending" },
};
const AGENT_TIP = { done: "done", working: "working now", waiting: "not started yet" };

/** The item's own flow: the stages it really goes through, the agents planned for each (with what they actually
 *  did: done, working, waiting), the human reviews between stages, and any agent that ran without being planned. */
export function ItemFlow({ flow, user, current, running, lastRun, held = 0, goLive = null }: {
  flow: FlowStage[]; user: string; current: string | null; running: boolean; lastRun: Run | null; held?: number;
  goLive?: GoLive | null;
}) {
  const live = goLive?.state === "live";
  const ready = goLive?.state === "ready";
  const finish = live ? "live" : ready ? "ready" : "pending";
  return (
    <ol className="flow track-flow item-flow"
      style={{ gridTemplateColumns: `repeat(${flow.length}, minmax(0, 1fr)) 150px` }}>
      {flow.map((f, i) => {
        // a stage that has started but has no agent run going is paused, not working
        const paused = f.status === "in_progress" && !running && !f.agents.some((a) => a.status === "working");
        const st = paused && held && f.stage === current ? { label: `Needs you · ${held} thing${held > 1 ? "s" : ""}`, cls: "waiting" }
          : paused ? { label: "Started · agents not running", cls: "waiting" } : STAGE_STATE[f.status] ?? STAGE_STATE.pending;
        const done = f.agents.filter((a) => a.status === "done").length;
        const review = f.gate.length > 0 && !f.human;
        return (
          <li key={f.stage} className={`${review ? "gated" : ""} ${st.cls} ${f.stage === current ? "now" : ""}`}>
            <div className="flow-node">
              <Avatar seed={f.human ? user : f.lead} stage={f.stage} size={44} person={f.human}
                working={f.status === "in_progress" && !paused} title={f.human ? "You" : f.lead} />
              <span className={`flow-num ${st.cls}`}>{f.status === "approved" ? "✓" : i + 1}</span>
              {review && (
                <span className={`checkpoint ${f.status === "approved" ? "passed" : f.status === "awaiting_approval" ? "due" : ""}`}
                  tabIndex={0} role="button" aria-label={`Human review by ${f.gate.join(", ")}`}>
                  <Avatar seed={user} stage="production" size={18} person />
                  <span className="checkpoint-label">
                    {f.status === "approved" ? `Approved${f.decided_by ? ` by ${f.decided_by}` : ""}`
                      : f.status === "awaiting_approval" ? "Waiting for your review" : "You review"}
                    <small>{f.gate.join(", ")} approves before the next stage</small>
                  </span>
                </span>
              )}
            </div>
            <div className="flow-card">
              <b>{f.title}</b>
              <span className={`flow-state ${st.cls}`}>{st.label}{f.agents.length ? ` · ${done}/${f.agents.length} agents done` : ""}</span>
              {f.human ? <div className="muted small flow-agents">You deploy it: agents never deploy to production</div> : (
                <div className="flow-crew item-crew">
                  {f.agents.map((a) => (
                    <span key={a.name} className={`crew-dot ${a.status}`}
                      title={`${agentTitle(a.name)} (step ${a.step}): ${AGENT_TIP[a.status]}${a.runs > 1 ? `, ran ${a.runs} times` : ""}`}>
                      <AgentAvatar name={a.name} size={24} working={a.status === "working"} />
                      {a.status === "done" && <span className="crew-tick">✓</span>}
                    </span>
                  ))}
                </div>
              )}
              {f.stage === current && lastRun && (lastRun.summary || lastRun.error) && (
                <LastRun run={lastRun} held={held} />
              )}
              {f.extra.length > 0 && (
                <div className="flow-extra" title="These agents ran for this stage but are not in its plan">
                  Not planned: {f.extra.map((a) => agentTitle(a.name)).join(", ")}
                </div>
              )}
            </div>
          </li>
        );
      })}
      {/* the destination: going live is done by a person, never by an agent */}
      <li className={`go-live-step ${finish}`}>
        <div className="flow-node">
          <span className={`go-live-badge ${finish}`} title={live ? "Live in production" : ready ? "Ready to go live: a person merges, deploys and records it" : "Goes live after the Release approval"}>
            {live ? "✓" : "🚀"}
          </span>
        </div>
        <div className="flow-card">
          <b>{live ? "Live" : "Go live"}</b>
          <span className={`flow-state ${live ? "done" : ready ? "golive" : "pending"}`}>
            {live ? `In ${goLive!.env}${goLive!.deployed?.version ? ` · ${goLive!.deployed.version}` : ""}`
              : ready ? "Ready to go live" : "After Release approval"}
          </span>
          <div className="muted small flow-agents">
            {live ? `Deployed by ${goLive!.deployed?.by} ${since(goLive!.deployed?.ts)} ago`
              : ready ? `You merge${goLive!.pr ? ` PR #${goLive!.pr.number}` : " the PR"}, deploy${goLive!.version ? ` ${goLive!.version}` : ""} and record it`
              : "Done by you: agents never deploy to production"}
          </div>
        </div>
      </li>
    </ol>
  );
}

/** How the last run ended: two lines, with "Show more" for the whole message. */
function LastRun({ run, held }: { run: Run; held: number }) {
  const [open, setOpen] = useState(false);
  const full = run.error ?? run.summary ?? "";
  // when the engine already lists what's needed, say that first; the run's own words stay under Show more
  const text = held && !open ? `Stopped: ${held} thing${held > 1 ? "s" : ""} need you (see "Before agents can start" above).\n${full}` : full;
  const long = text.length > 110 || text.includes("\n") || held > 0;
  return (
    <div className={`flow-lastrun ${run.status} ${open ? "open" : ""}`}>
      <div className="flow-lastrun-text">
        <b>Last run</b> {run.ended ? `${since(run.ended)} ago` : ""}: {text}
      </div>
      {long && (
        <button className="link flow-more" onClick={() => setOpen(!open)}>{open ? "Show less" : "Show more"}</button>
      )}
    </div>
  );
}
