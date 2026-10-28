import { useEffect, useRef, useState } from "react";
import {
  agentTitle, api, homeApi, since, type Home as HomeData, type LastJob, type Link, type Proposal, type Run,
  type Started, type TeamLead, type WorkTypeName,
} from "../api";
import { AgentAvatar, Avatar } from "./Avatar";
import { Demo } from "./Demo";
import { RunButton, RunLog } from "./RunPanel";

const TYPE_ICONS: Record<WorkTypeName, string> = { feature: "✨", change: "🔁", bug: "🐞", task: "🛠️" };
const TYPES: WorkTypeName[] = ["feature", "change", "bug", "task"];
const POWER_EXAMPLE: Record<WorkTypeName, string> = {
  feature: "Let users export their expenses as CSV",
  change: "Expenses can be edited after they are recorded",
  bug: "Listing expenses fails when the list is empty",
  task: "Upgrade FastAPI to the latest version",
};
const EXAMPLES = [
  "Let users export their expenses as CSV",
  "Expenses can be edited after they are recorded",
  "Listing expenses fails when the list is empty",
  "Upgrade FastAPI to the latest version",
];
const LEAD_TAG: Record<TeamLead["status"], string> = {
  ready: "Ready to serve you", working: "Working now", off: "Turned off", human: "Done by you",
};

function greeting(): string {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function Home({ openItem, refresh }: { openItem: (id: string) => void; refresh: () => void }) {
  const [data, setData] = useState<HomeData | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [started, setStarted] = useState<Started | null>(null);
  const [openLead, setOpenLead] = useState<string | null>(null);
  const [resumed, setResumed] = useState<Run | null>(null);
  const [demo, setDemo] = useState(false);
  const [asked, setAsked] = useState(0);
  const [powerTab, setPowerTab] = useState<WorkTypeName>("feature");
  const powerRefs = useRef<Partial<Record<WorkTypeName, HTMLButtonElement | null>>>({});
  const powerList = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const load = () => homeApi.home().then(setData, (e: Error) => setErr(e.message));
  useEffect(() => {
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);

  const ask = () => {
    if (!text.trim()) return;
    setBusy(true);
    setErr("");
    setStarted(null);
    // a fresh card for every answer, so nothing from the previous request (type, title, links) carries over
    homeApi.classify(text).then((p) => { setAsked((n) => n + 1); setProposal(p); }, (e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  const first = data?.user.split(/\s+/)[0] ?? "";
  // stages without agents (other than Production, which is yours) are not team members yet
  const shown = data?.team.filter((t) => t.agents.length || t.status === "human") ?? [];
  const notYet = data?.team.filter((t) => !shown.includes(t)) ?? [];
  // one lead is always open on the right: the first one until another is picked
  const current = shown.some((t) => t.stage === openLead) ? openLead : shown[0]?.stage ?? null;
  const currentLead = shown.find((t) => t.stage === current);

  return (
    <div className="home">
      <div className="top-row">
      <section className="hero">
        <div className="hero-kicker">{greeting()}{first ? `, ${first}` : ""} 👋</div>
        <h1>Welcome to AI-native development</h1>
        <p className="hero-sub">How may I help you today? Describe what you need; I'll work out what kind of work it is and
          which agents should take it.</p>
        <div className="ask">
          <textarea
            ref={box}
            value={text}
            rows={3}
            placeholder="e.g. Let users export their expenses as CSV"
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) ask();
            }}
          />
          <div className="ask-row">
            <div className="examples">
              {EXAMPLES.map((x) => (
                <button key={x} className="example" onClick={() => { setText(x); box.current?.focus(); }}>{x}</button>
              ))}
            </div>
            <button className="btn primary big" disabled={busy || !text.trim()} onClick={ask}>
              {busy ? "Thinking…" : "Ask ⌘↵"}
            </button>
          </div>
          {err && <div className="error">{err}</div>}
        </div>
      </section>
      {data && (
        <section className="powers panel">
          <div className="team-head">
            <h2>What your AI team can do</h2>
            <span className="muted small">Pick one to try it</span>
          </div>
          <div className="power-tabs" role="tablist">
            {data.work_types.map((w) => (
              <button key={w.name} role="tab" aria-selected={powerTab === w.name}
                className={`power-tab ${w.name} ${powerTab === w.name ? "on" : ""}`}
                onClick={() => {
                  setPowerTab(w.name);
                  const card = powerRefs.current[w.name];
                  if (card && powerList.current) powerList.current.scrollTo({ top: card.offsetTop - 2, behavior: "smooth" });
                }}>
                {TYPE_ICONS[w.name]} {w.title}
              </button>
            ))}
          </div>
          <div className="power-scroll"><div className="power-grid" ref={powerList} onScroll={(e) => {
            // the tab follows whichever card is at the top of the list
            const top = e.currentTarget.scrollTop + 8;
            const atTop = [...data.work_types].reverse().find((w) => (powerRefs.current[w.name]?.offsetTop ?? 0) <= top);
            const end = e.currentTarget.scrollTop + e.currentTarget.clientHeight >= e.currentTarget.scrollHeight - 2;
            setPowerTab(end ? data.work_types[data.work_types.length - 1].name : atTop?.name ?? data.work_types[0].name);
          }}>
            {data.work_types.map((w) => {
              const agents = new Set(w.plan.flatMap((p) => p.agents));
              const gates = w.plan.filter((p) => p.gate.length).length;
              return (
                <button key={w.name} className={`power ${w.name}`} ref={(el) => { powerRefs.current[w.name] = el; }}
                  onClick={() => { setText(POWER_EXAMPLE[w.name]); box.current?.focus(); window.scrollTo({ top: 0, behavior: "smooth" }); }}>
                  <span className="power-icon">{TYPE_ICONS[w.name]}</span>
                  <span className="power-title">{w.title}</span>
                  <span className="power-purpose">{w.purpose}</span>
                  <span className="power-flow">
                    {w.plan.map((p) => {
                      const lead = data.team.find((t) => t.stage === p.stage);
                      return (
                        <span key={p.stage} className="power-step"
                          title={`${lead?.lead ?? p.title} · ${p.title}${p.note ? ` (${p.note})` : ""}`}>
                          <Avatar seed={lead?.status === "human" ? data.user : lead?.lead ?? p.stage} stage={p.stage}
                            size={28} person={lead?.status === "human"} />
                        </span>
                      );
                    })}
                  </span>
                  <span className="power-stats">
                    <b>{agents.size}</b> agents · <b>{w.plan.length}</b> stages · <b>{gates}</b> approvals by you
                  </span>
                  <span className="power-eg">e.g. “{POWER_EXAMPLE[w.name]}”</span>
                  <span className="power-try">Try it →</span>
                </button>
              );
            })}
          </div></div>
        </section>
      )}

      </div>

      {proposal && !started && (
        <ProposalCard key={asked} text={text} proposal={proposal} team={data?.team ?? []} user={data?.user ?? ""} onCancel={() => setProposal(null)}
          onStarted={(s) => {
            setProposal(null);
            setText("");
            load();
            if (s.run || s.blockers?.length) openItem(s.req); // watch the agents, or see what they need first
            else setStarted(s);
          }} />
      )}

      {started && (
        <section className="panel started">
          <div className="started-head">
            <span className="big-icon">{TYPE_ICONS[started.type]}</span>
            <div>
              <div className="started-title"><span className="mono">{started.req}</span> · {started.title}</div>
              <div className="muted">
                {started.run ? "The agents have started. You'll be asked to approve at each gate."
                  : started.run_error ?? "Created. Start the agents when you're ready."}
              </div>
            </div>
            <span className="spacer" />
            <button className="btn" onClick={() => openItem(started.req)}>Open on the board →</button>
          </div>
          {started.run && <RunLog run={started.run} onClose={() => setStarted({ ...started, run: null })} />}
        </section>
      )}

      {data?.last_job && !started && (
        <ResumeCard job={data.last_job} resumed={resumed} openItem={openItem}
          onResumed={(r) => { setResumed(r); load(); refresh(); }} />
      )}

      {data && !data.last_job && !started && !proposal && (
        <section className="panel resume caught-up">
          <span className="big-icon">✅</span>
          <div>
            <div className="started-title">You're all caught up</div>
            <div className="muted">No unfinished work in {data.project}. Describe a new task above and the team will pick it up.</div>
          </div>
        </section>
      )}

      {data && (
        <section className="team">
          <div className="team-head">
            <h2>Your AI team</h2>
            <span className="muted">
              {shown.filter((t) => t.agents.length).length} lead agents ·{" "}
              {data.team.reduce((n, t) => n + t.agents.length, 0)} sub-agents
            </span>
            <span className="spacer" />
            <button className="btn primary big" onClick={() => setDemo(true)}>▶ See how they work on a task</button>
          </div>
          <div className="team-split">
            <div className="team-left">
              <div className="leads">
                {shown.map((t, n) => (
                  <button key={t.stage} className={`lead ${t.status} ${current === t.stage ? "open" : ""}`}
                    onClick={() => setOpenLead(t.stage)} aria-pressed={current === t.stage}>
                    <span className="lead-top">
                      <Avatar seed={t.status === "human" ? data.user : t.lead} stage={t.stage} size={52}
                        person={t.status === "human"} working={t.status === "working"} />
                    </span>
                    <span className="lead-text">
                      <span className="lead-name"><span className="lead-n">{n + 1}</span>{t.lead}</span>
                      <span className="lead-stage">
                        {t.title} · {t.agents.length ? `${t.agents.length} sub-agent${t.agents.length > 1 ? "s" : ""}` : "no agents"}
                      </span>
                      <span className={`tag ${t.status}`}>
                        {t.status === "working" && <span className="dot-live pulse" />}
                        {LEAD_TAG[t.status]}
                      </span>
                    </span>
                    <span className="lead-arrow">›</span>
                  </button>
                ))}
              </div>
              {notYet.length > 0 && (
                <div className="not-yet muted small">Not set up yet: {notYet.map((t) => t.title).join(", ")}</div>
              )}
            </div>
            <div className="team-right">
              {currentLead && <LeadDetail key={currentLead.stage} lead={currentLead} user={data.user} />}
            </div>
          </div>
        </section>
      )}
      {demo && data && <Demo team={data.team} user={data.user} onClose={() => setDemo(false)} />}
    </div>
  );
}

function LeadDetail({ lead, user }: { lead: TeamLead; user: string }) {
  const standard = lead.agents.filter((a) => !a.for.length);
  const special = lead.agents.filter((a) => a.for.length);
  const steps = [...new Set(standard.map((a) => a.step))];
  return (
    <section className="panel lead-detail">
      <div className="lead-detail-head">
        <Avatar seed={lead.status === "human" ? user : lead.lead} stage={lead.stage} size={52}
          person={lead.status === "human"} working={lead.status === "working"} />
        <div>
          <div className="started-title">{lead.lead} · {lead.title}</div>
          <div className="muted">
            {lead.purpose}
            {lead.gate.length ? ` · approved by ${lead.gate.join(", ")}` : ""}
            {!lead.enabled ? " · turned off in sdlc.config.yaml" : ""}
          </div>
        </div>
      </div>
      {lead.stage === "production" ? (
        <div className="empty">Production is deployed by a person, never by an agent.</div>
      ) : !lead.agents.length ? (
        <div className="empty">No agents for this stage yet.</div>
      ) : (
        <ol className="seq">
          {steps.map((s) => {
            const here = standard.filter((a) => a.step === s);
            return (
              <li key={s}>
                <div className="seq-step">
                  Step {s}{here.length > 1 ? <span className="muted"> · {here.length} run at the same time</span> : ""}
                </div>
                {here.map((a) => <SubAgent key={a.name} a={a} stage={lead.stage} />)}
              </li>
            );
          })}
          {special.length > 0 && (
            <li>
              <div className="seq-step">Joins for some kinds of work</div>
              {special.map((a) => <SubAgent key={a.name} a={a} stage={lead.stage} />)}
            </li>
          )}
        </ol>
      )}
    </section>
  );
}

function SubAgent({ a, stage }: { a: TeamLead["agents"][number]; stage: string }) {
  return (
    <div className="sub" title={a.about}>
      <Avatar seed={a.name} stage={stage} size={44} working={a.status === "working"} />
      <div className="sub-body">
        <div className="sub-name">
          {agentTitle(a.name)} <span className="mono muted">{a.name}</span>
        </div>
        <div className="sub-does">{a.does}</div>
        <div className="sub-out muted">→ {a.output}</div>
      </div>
      <div className="sub-tags">
        <span className={`tag ${a.status}`}>
          {a.status === "working" && <span className="dot-live pulse" />}
          {a.status === "working" ? "Working now" : "Ready to serve you"}
        </span>
        <span className="chip">{a.model}</span>
        {a.for.map((f) => <span key={f} className="chip">for {f}s</span>)}
        {a.when && <span className="chip">{a.when}</span>}
      </div>
    </div>
  );
}

function ResumeCard({ job, resumed, openItem, onResumed }: {
  job: LastJob; resumed: Run | null; openItem: (id: string) => void; onResumed: (r: Run) => void;
}) {
  const [log, setLog] = useState<Run | null>(null);
  const run = resumed && resumed.req === job.req ? resumed : null;
  return (
    <section className="panel resume">
      <div className="resume-row">
        <span className="big-icon">{TYPE_ICONS[job.type]}</span>
        <div className="resume-body">
          <div className="section-label">Pick up where you left off</div>
          <div className="started-title">
            <span className="mono">{job.req}</span> · {job.title}
          </div>
          <div className="muted">
            {job.stage_title} · updated {since(job.updated)} ago
            {job.others ? ` · ${job.others} other open item${job.others > 1 ? "s" : ""}` : ""}
          </div>
          {job.message && <div className="resume-msg">{job.message}</div>}
        </div>
        <div className="resume-actions">
          {job.paused ? (
            <button className="btn primary" onClick={() => api.resume(job.req).then((r) => { onResumed(r); openItem(job.req); },
              (e: Error) => alert(e.message))}>▶ Resume (paused)</button>
          ) : job.running ? (
            <button className="btn primary" onClick={() => openItem(job.req)}>
              <span className="dot-live pulse" /> Agents working · watch on the board
            </button>
          ) : job.can_resume ? (
            <RunButton req={job.req} run={run} label="▶ Resume" onStarted={(r) => { onResumed(r); openItem(job.req); }} />
          ) : job.turn === "you" ? (
            <span className="tag waiting">Your turn</span>
          ) : null}
          <button className="btn" onClick={() => openItem(job.req)}>
            {job.turn === "you" ? "Review on the board →" : "Open →"}
          </button>
          <span className="muted small">or describe a new task above</span>
        </div>
      </div>
      {log && <RunLog run={log} onClose={() => setLog(null)} />}
    </section>
  );
}

function ProposalCard({ text, proposal, team, user, onCancel, onStarted }: {
  text: string; proposal: Proposal; team: TeamLead[]; user: string; onCancel: () => void; onStarted: (s: Started) => void;
}) {
  const [type, setType] = useState<WorkTypeName>(proposal.type);
  const [title, setTitle] = useState(proposal.title);
  const [links, setLinks] = useState<Link[]>(proposal.links);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const plan = proposal.plans[type];

  const pickType = (t: WorkTypeName) => {
    setType(t);
    const rel = t === "change" ? "updates" : t === "bug" ? "fixes" : null;
    const to = proposal.links[0]?.req ?? proposal.related[0]?.req;
    setLinks(rel && to ? [{ rel, req: to }] : []);
  };

  const go = (run: boolean) => {
    setBusy(true);
    setErr("");
    homeApi.start({ text, title, type, links, run }).then(onStarted, (e: Error) => setErr(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="panel proposal">
      <div className="proposal-head">
        <div className="section-label">Here's how I understood it</div>
        <button className="proposal-close" onClick={onCancel} title="Cancel: nothing is created" aria-label="Cancel">✕</button>
      </div>
      <div className="types">
        {TYPES.map((t) => (
          <button key={t} className={`type ${t === type ? "on" : ""}`} onClick={() => pickType(t)} title={proposal.types[t]}>
            <span>{TYPE_ICONS[t]}</span> {t[0].toUpperCase() + t.slice(1)}
            {t === proposal.type && <span className={`conf ${proposal.confidence}`}>{proposal.confidence === "low" ? "guess" : "suggested"}</span>}
          </button>
        ))}
      </div>
      <div className="muted">{proposal.types[type]}</div>
      {type === proposal.type && <div className="why">Why: {proposal.reasons.join("; ")}.</div>}

      <label className="field">
        <span>Title</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </label>

      {links.length > 0 && (
        <div className="links">
          {links.map((l) => (
            <span key={l.req} className="chip">
              {l.rel === "updates" ? "Updates" : l.rel === "fixes" ? "Fixes" : "Blocked by"} {l.req}
              {" · "}{proposal.related.find((r) => r.req === l.req)?.title}
              <button className="x" onClick={() => setLinks(links.filter((x) => x !== l))} title="Remove the link">×</button>
            </span>
          ))}
        </div>
      )}

      <div className="section-label">What happens next</div>
      <ol className="flow track-flow">
        {plan.map((p, i) => (
          <li key={p.stage} className={p.gate.length && p.stage !== "production" ? "gated" : ""}>
            <div className="flow-node">
              {(() => {
                const lead = team.find((t) => t.stage === p.stage);
                const human = lead?.status === "human";
                return <Avatar seed={human ? user : lead?.lead ?? p.stage} stage={p.stage} size={44} person={human}
                  title={human ? "You" : lead?.lead} />;
              })()}
              <span className="flow-num">{i + 1}</span>
              {p.gate.length > 0 && p.stage !== "production" && (
                <span className="checkpoint" tabIndex={0} role="button"
                  aria-label={`Human review: ${p.gate.join(", ")} approves before the next stage`}>
                  <Avatar seed={user} stage="production" size={18} person />
                  <span className="checkpoint-label">You review<small>{p.gate.join(", ")} approves before the next stage</small></span>
                </span>
              )}
            </div>
            <div className="flow-card">
              <b>{p.title}</b>{p.note ? <span className="muted"> ({p.note})</span> : ""}
              {p.agents.length > 0 && (
                <div className="flow-crew">{p.agents.map((a) => <AgentAvatar key={a} name={a} size={22} />)}</div>
              )}
              <div className="muted small flow-agents" title={p.agents.map(agentTitle).join(", ")}>
                {p.stage === "production" ? "You deploy it: agents never deploy to production" : p.agents.length ? p.agents.map(agentTitle).join(", ") : "By hand"}
              </div>
            </div>
          </li>
        ))}
      </ol>

      <div className="proposal-actions">
        <button className="btn primary big" disabled={busy || !title.trim()} onClick={() => go(true)}>
          {busy ? "Starting…" : "▶ Create and start the agents"}
        </button>
        <button className="btn" disabled={busy || !title.trim()} onClick={() => go(false)}>Create only</button>
        <button className="btn" onClick={onCancel}>✕ Cancel</button>
        {err && <span className="error">{err}</span>}
      </div>
    </section>
  );
}
