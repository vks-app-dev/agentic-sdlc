import { tokens, clock, shortAgent, type SdlcEvent } from "../api";
import { AgentAvatar } from "./Avatar";

const PHRASE: Record<string, string> = {
  item_created: "created",
  stage_started: "stage started",
  stage_done: "stage done",
  stage_skipped: "stage skipped",
  gate_waiting: "waiting at gate",
  gate_approved: "gate approved",
  gate_rejected: "sent back",
  plan_set: "task plan loaded",
  artifact: "file recorded",
  task_start: "task started",
  task_review: "task in review",
  task_done: "task merged",
  task_fail: "task failed",
  task_unblock: "task unblocked",
  agent_dispatch: "dispatched",
  agent_start: "started",
  agent_stop: "finished",
  session_idle: "idle",
  project_initialized: "project set up",
  run_started: "started agents from dashboard",
  run_finished: "agent run ended",
  human_needed: "needs you",
  branch_ready: "work-item branch ready",
};

function describe(e: SdlcEvent): { who: string; what: string } {
  const who = e.source === "engine" ? e.by ?? "engine" : shortAgent(e.agent) || e.source;
  if (e.event === "tool_use") return { who, what: e.tool ?? "tool" };
  if (e.event === "usage") {
    const u = e as SdlcEvent & Record<string, number | string>;
    const n = ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]
      .reduce((a, k) => a + Number(u[k] ?? 0), 0);
    return { who, what: `used ${tokens(n)} tokens on ${String(u.model ?? "").replace(/^claude-/, "")}` };
  }
  return { who, what: PHRASE[e.event] ?? e.event.replace(/_/g, " ") };
}

export function Activity({ events, reqs, filter, setFilter }: { events: SdlcEvent[]; reqs: string[]; filter: string; setFilter: (f: string) => void }) {
  const shown = events.filter((e) => e.event !== "session_idle" && (!filter || e.req === filter)).slice(-150).reverse();
  return (
    <section className="panel">
      <h2>
        Activity
        <label className="filter">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All items</option>
            {reqs.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
      </h2>
      <div className="feed">
        {shown.length === 0 && <div className="empty">No activity yet.</div>}
        {shown.map((e, i) => {
          const { who, what } = describe(e);
          return (
            <div className={`ev ${e.source}`} key={`${e.ts}-${i}`}>
              <div className="time">{clock(e.ts)}</div>
              <div>
                <div className="what">
                  {e.agent && e.source !== "engine" && <AgentAvatar name={e.agent} size={18} />}
                  <b>{who}</b> {what}
                  {e.req && (
                    <span className="mono" style={{ color: "var(--muted)" }}>
                      {" "}
                      · {e.req}
                      {e.task ? `/${e.task}` : ""}
                    </span>
                  )}
                </div>
                {e.detail && <div className="detail" title={e.detail}>{e.detail}</div>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
