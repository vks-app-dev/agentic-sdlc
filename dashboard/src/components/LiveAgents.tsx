import { shortAgent, since, type LiveAgent } from "../api";
import { AgentAvatar } from "./Avatar";

export function LiveAgents({ live, now, select }: { live: LiveAgent[]; now: number; select: (id: string) => void }) {
  return (
    <section className="panel">
      <h2>
        Live agents <span className="count">{live.length}</span>
      </h2>
      {live.length === 0 ? (
        <div className="empty">No agents running.</div>
      ) : (
        live.map((a) => (
          <div className="agent" key={a.agent_id}>
            <div className="name">
              <AgentAvatar name={a.agent} size={28} working />
              {shortAgent(a.agent)}
              {a.req && (
                <button className="link ref" onClick={() => select(a.req!)}>
                  {a.req}
                  {a.task ? `/${a.task}` : ""}
                </button>
              )}
            </div>
            <div className="age" title={`started ${a.started}`}>{since(a.started, now)}</div>
            <div className="last" title={a.last ?? ""}>
              {a.last ? `${a.last} · ${since(a.last_ts, now)} ago` : "starting…"} · {a.actions} actions
            </div>
          </div>
        ))
      )}
    </section>
  );
}
