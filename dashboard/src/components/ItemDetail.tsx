import { useEffect, useState } from "react";
import { api, clock, since, typeOf, type ItemDetail as Detail, type Project, type Run, type WorkTypeName } from "../api";
import { RunButton, RunLog } from "./RunPanel";
import { ItemFlow } from "./ItemFlow";
import { NeedsYou } from "./NeedsYou";
import { LiveView } from "./LiveView";
import { HubView } from "./HubView";
import { StatusBadge, TypeBadge } from "./Status";
import { TaskDag } from "./TaskDag";

const NEXT_TEXT: Record<string, string> = {
  run_stage: "Agents can work on this stage. They run until the next gate.",
  complete_stage: "All tasks are merged. Agents will wrap up the stage.",
  await_gate: "Waiting for your decision in the inbox above.",
};
const RUNNABLE = new Set(["run_stage", "complete_stage"]);
const OPEN = new Set(["open", "fixing", "fixed", "reopened"]);

/** Defects found in QA. Deferring or accepting one is a human decision, so only people do it, here. */
function Defects({ item, done }: { item: Detail; done: () => void }) {
  const [err, setErr] = useState("");
  const defects = item.defects ?? [];
  if (!defects.length) return null;
  async function decide(id: string, action: "defer" | "accept" | "reopen") {
    const why = window.prompt(`${action[0].toUpperCase()}${action.slice(1)} ${id}: why?`);
    if (!why?.trim()) return;
    try {
      await api.decideDefect(item.id, id, action, why);
      setErr("");
      done();
    } catch (e) {
      setErr((e as Error).message);
    }
  }
  const open = defects.filter((d) => OPEN.has(d.status));
  return (
    <div style={{ padding: "0 14px 14px" }}>
      <div className="section-label">
        Defects · {open.length} open{open.some((d) => d.blocker) ? `, ${open.filter((d) => d.blocker).length} blocking release` : ""}
      </div>
      <table className="defects">
        <tbody>
          {defects.map((d) => (
            <tr key={d.id} className={OPEN.has(d.status) ? "" : "closed"}>
              <td className="mono">{d.id}</td>
              <td><span className={`sev ${d.severity}`}>{d.severity}</span>{d.blocker && <span className="sev blocker">blocker</span>}</td>
              <td title={d.log.at(-1)?.note}>{d.title}</td>
              <td>{d.status}{d.duplicate_of ? ` of ${d.duplicate_of}` : ""}{d.task ? ` · ${d.task}` : ""}</td>
              <td className="acts">
                {OPEN.has(d.status) && (
                  <>
                    <button className="btn" onClick={() => decide(d.id, "defer")}>Defer</button>
                    <button className="btn" onClick={() => decide(d.id, "accept")}>Accept</button>
                  </>
                )}
                {["verified", "deferred", "accepted"].includes(d.status) && (
                  <button className="btn" onClick={() => decide(d.id, "reopen")}>Reopen</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {err && <div className="error">{err}</div>}
    </div>
  );
}

/** Change the kind of work (a human decision). Only offered until Development starts. */
function Retype({ item, project, done }: { item: Detail; project: Project | null; done: () => void }) {
  const others = (project?.work_types ?? []).filter((w) => w.name !== typeOf(item));
  const [type, setType] = useState<WorkTypeName>(others[0]?.name ?? "change");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const target = project?.work_types.find((w) => w.name === type);

  async function apply() {
    if (!reason.trim()) return setErr("Say why the type changes.");
    setBusy(true);
    setErr("");
    try {
      await api.retype(item.id, type, reason);
      setReason("");
      done();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="retype">
      <summary>Change type…</summary>
      <div className="row">
        <select value={type} onChange={(e) => setType(e.target.value as WorkTypeName)}>
          {others.map((w) => <option key={w.name} value={w.name}>{w.title}</option>)}
        </select>
        <input placeholder="Why? e.g. triage found it works as specified" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button className="btn" disabled={busy} onClick={apply}>Change type</button>
      </div>
      <div style={{ color: "var(--faint)", marginTop: 6 }}>
        {target?.purpose}. The item restarts at its first stage; the current spec text is kept at the end of the new spec.
      </div>
      {err && <div className="error" style={{ marginTop: 6 }}>{err}</div>}
    </details>
  );
}

export function ItemDetail({ id, project, version, openFile, select, refresh, close }: {
  id: string; project: Project | null; version: number; openFile: (p: string) => void;
  select: (id: string) => void; refresh: () => void; close: () => void;
}) {
  const [item, setItem] = useState<Detail | null>(null);
  const [err, setErr] = useState("");
  const [logRun, setLogRun] = useState<Run | null>(null);
  const [logHidden, setLogHidden] = useState(false);
  const [view, setView] = useState<"summary" | "live" | "detail">("summary");

  useEffect(() => {
    api.item(id).then((d) => { setItem(d); setErr(""); }, (e: Error) => setErr(e.message));
  }, [id, version]);

  useEffect(() => {
    setLogRun(null);
    setLogHidden(false);
  }, [id]);

  // While agents run, open on the live view; the person can switch back to the summary.
  useEffect(() => {
    if (item?.run) setView("live");
  }, [item?.run?.id]);

  // Show the log of a running agent run automatically (unless the user hid it).
  useEffect(() => {
    if (item?.run && !logHidden && logRun?.id !== item.run.id) setLogRun(item.run);
  }, [item?.run, logHidden, logRun?.id]);

  if (err) return <section className="panel"><div className="empty">{err}</div></section>;
  if (!item) return <section className="panel"><div className="empty">Loading {id}…</div></section>;

  const stages = project?.stages ?? [];
  const cur = item.current_stage ? item.stages[item.current_stage] : null;
  const files = stages.flatMap((s) => item.stages[s.name].artifacts.map((a) => ({ stage: item.stage_titles[s.name] ?? s.title, path: a })));
  const uniqueFiles = files.filter((f, i) => files.findIndex((x) => x.path === f.path) === i);
  // the files of the stage waiting for approval: what "Review N files" in Waiting for you points at
  const gateFiles = new Set(cur?.status === "awaiting_approval" ? cur.artifacts : []);
  const reviewFiles = item.tasks.filter((t) => item.reviews[t.id]).map((t) => `.sdlc/reviews/${item.id}-${t.id}.md`);
  // Without Design the engine made a single task itself, so there is no plan file.
  const planned = item.stages.planning?.status !== "skipped" || item.stages.design.status !== "skipped";
  const planFile = item.tasks.length && planned ? `.sdlc/plans/${item.id}.json` : null;
  const nextMsg = NEXT_TEXT[item.next.action] ?? item.next.message;
  const canRun = RUNNABLE.has(item.next.action) || !!item.run;

  let reached = true;
  return (
    <section className="panel" id="item-detail">
      <div className="detail-head">
        <span className="id">{item.id}</span>
        <TypeBadge type={typeOf(item)} />
        {item.go_live ? (
          <span className={`golive-pill ${item.go_live.state}`}>
            {item.go_live.state === "live" ? `✓ Live${item.go_live.deployed?.version ? ` · ${item.go_live.deployed.version}` : ""}` : "🚀 Ready to go live"}
          </span>
        ) : cur?.status === "in_progress" && !item.run && item.status !== "blocked"
          ? <StatusBadge status="pending" text="Started · agents not running" />
          : <StatusBadge status={item.status === "done" ? "done" : item.status === "blocked" ? "blocked" : cur?.status ?? "pending"} />}
        <span className="spacer" />
        <span style={{ color: "var(--muted)", fontSize: 12 }}>updated {since(item.updated)} ago</span>
        <button className="proposal-close" onClick={close} title="Hide details (the item and any running agents are not affected)"
          aria-label="Hide details">✕</button>
        <h3 className="detail-title">{item.title}</h3>
      </div>

      {(item.links?.length || item.size) ? (
        <div className="item-meta">
          {item.links?.map((l) => (
            <span key={`${l.rel}-${l.req}`}>
              {{ fixes: "Fixes", updates: "Updates", blocked_by: "Waits for" }[l.rel]}{" "}
              <button className="link mono" onClick={() => select(l.req)}>{l.req}</button>
            </span>
          ))}
          {item.size && <span>Size {item.size}</span>}
        </div>
      ) : null}

      <NeedsYou req={item.id} blockers={item.blockers ?? []} onFixed={() => { refresh(); api.item(id).then(setItem); }}
        onRun={(r) => { setLogHidden(false); setLogRun(r); refresh(); }} openFile={openFile} />

      {!!item.flow?.length && (
        <div className="view-switch">
          <div className="tabs">
            <button className={view === "summary" ? "on" : ""} onClick={() => setView("summary")}>Summary</button>
            <button className={view === "live" ? "on" : ""} onClick={() => setView("live")}>
              {item.run ? <><span className="dot-live pulse" /> Live</> : "Live"}
            </button>
            <button className={view === "detail" ? "on" : ""} onClick={() => setView("detail")}>Detail</button>
          </div>
          <span className="muted small">{view === "live" ? "The orchestrator directing every stage, live" : view === "detail" ? "Every sub-agent's actions and results, step by step" : "Every stage at a glance"}</span>
        </div>
      )}
      {view === "live" && item.flow?.length ? (
        <HubView req={item.id} flow={item.flow} current={item.current_stage} running={!!item.run} user={item.user ?? ""}
          goLive={item.go_live} version={version} />
      ) : view === "detail" && item.flow?.length ? (
        <LiveView req={item.id} current={item.current_stage} version={version} user={item.user ?? ""} />
      ) : item.flow?.length ? <ItemFlow flow={item.flow} user={item.user ?? ""} current={item.current_stage} running={!!item.run}
        lastRun={item.run ? null : item.last_run ?? null} held={item.blockers?.filter((b) => b.blocking !== false).length ?? 0} goLive={item.go_live} /> : (
      <div className="stepper">
        {stages.map((s) => {
          const st = item.stages[s.name];
          const cls = `step ${st.status}${reached ? " reached" : ""}`;
          if (s.name === item.current_stage) reached = false;
          const dot = { approved: "✓", awaiting_approval: "!", blocked: "✕", in_progress: "●", skipped: "–", pending: "" }[st.status];
          return (
            <div key={s.name} className={cls} title={st.status === "skipped" && st.summary ? st.summary : s.purpose}>
              <div className="dot">{dot}</div>
              <div className="lbl">{item.stage_titles[s.name] ?? s.title}</div>
              <div className="sub">
                {st.decision ? `by ${st.decision.by}` : st.status === "awaiting_approval" ? st.gate.join(", ") : s.automated ? "" : "manual"}
              </div>
            </div>
          );
        })}
      </div>
      )}

      {nextMsg && (
        <div className="next next-row">
          <span><b>Next:</b> {item.blockers?.some((b) => b.blocking !== false) && !item.run
            ? "The things above need you first, then the agents can start."
            : nextMsg}</span>
          {canRun && !!item.blockers?.some((b) => b.blocking !== false) && !item.run && (
            <button className="btn primary" disabled title="Fix the items in “Before agents can start” first">▶ Run agents</button>
          )}
          {canRun && !(item.blockers?.some((b) => b.blocking !== false) && !item.run) && (
            <span className="actions" style={{ marginTop: 0 }}>
              <RunButton req={item.id} run={item.run} onStarted={(r) => { setLogHidden(false); setLogRun(r); }} />
              {item.run && !logRun && (
                <button className="btn" onClick={() => { setLogHidden(false); setLogRun(item.run); }}>View log</button>
              )}
            </span>
          )}
        </div>
      )}
      {item.can_retype && !item.run && <Retype item={item} project={project} done={refresh} />}
      {logRun && <RunLog run={logRun} onClose={() => { setLogHidden(true); setLogRun(null); }} />}
      {!logRun && (item.run || item.latest_run) && (() => {
        const r = (item.run ?? item.latest_run)!;
        return (
          <button className="log-toggle" onClick={() => { setLogHidden(false); setLogRun(r); }}>
            📜 Show agent run log
            <span className="muted small">
              {r.status === "running" ? " · running now" : ` · last run ${r.ended ? `${since(r.ended)} ago` : ""} · ${r.status}`}
              {r.cost_usd != null ? ` · $${r.cost_usd.toFixed(2)}` : ""}
            </span>
          </button>
        );
      })()}

      {(item.pr || item.deployments?.length || item.checks) ? (
        <div className="facts facts-below">
          {item.pr && (
            <div className="fact">
              <div className="fact-head">
                <span className="fact-label">Pull request</span>
                <span className={`fact-chip ${item.pr.draft ? "wait" : "ok"}`}>{item.pr.draft ? "Draft" : "Ready for review"}</span>
              </div>
              <a className="fact-main" href={item.pr.url} target="_blank" rel="noreferrer">#{item.pr.number} on GitHub ↗</a>
              <div className="fact-chips">
                {Object.entries(item.pr.statuses ?? {}).map(([k, v]) => (
                  <span key={k} className={`mini ${v === "success" ? "ok" : v === "failure" ? "bad" : "wait"}`}>
                    {v === "success" ? "✓" : v === "failure" ? "✕" : "…"} {k}
                  </span>
                ))}
              </div>
              <div className="fact-sub">{item.pr.head} · pushed {since(item.pr.updated)} ago</div>
            </div>
          )}
          {!!item.deployments?.length && (
            <div className="fact">
              <div className="fact-head"><span className="fact-label">Deployed</span></div>
              {Object.values(Object.fromEntries(item.deployments.map((d) => [d.env, d]))).map((d) => {
                const sm = [...(item.smoke ?? [])].reverse().find((x) => x.env === d.env);
                const good = d.ok && sm?.ok !== false;
                return (
                  <div key={d.env} className="deploy-row" title={`at ${d.commit}${sm ? `, smoke check ${sm.ok ? "passed" : "failed"}` : ", no smoke check yet"}`}>
                    <span className={`fact-chip ${good ? "ok" : "bad"}`}>{d.env}{d.version ? ` ${d.version}` : ""}</span>
                    <span className="fact-sub">
                      by {["agentic-sdlc", "sdlc-pilot"].includes(d.by) ? "an agent" : d.by} · {since(d.ts)} ago
                      {sm ? (sm.ok ? " · smoke ✓" : " · smoke ✕") : ""}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          {item.checks && (() => {
            const failed = item.checks.results.filter((r) => r.status !== "passed");
            return (
              <div className="fact">
                <div className="fact-head">
                  <span className="fact-label">Checks</span>
                  <span className={`fact-chip ${item.checks.passed ? "ok" : "bad"}`}>
                    {item.checks.passed ? `All ${item.checks.results.length} passed` : `${failed.length} of ${item.checks.results.length} failed`}
                  </span>
                </div>
                <div className="fact-chips">
                  {item.checks.results.map((r) => (
                    <span key={r.name} className={`mini ${r.status === "passed" ? "ok" : "bad"}`}>
                      {r.status === "passed" ? "✓" : "✕"} {r.name.replace(" (built-in)", "")}
                    </span>
                  ))}
                </div>
                <div className="fact-sub">
                  {item.checks.head ? `at ${item.checks.head} · ` : ""}{since(item.checks.ts)} ago ·{" "}
                  <button className="link" onClick={() => openFile(`docs/dev/${item.id}/checks.md`)}>report</button>
                </div>
              </div>
            );
          })()}
        </div>
      ) : null}

      {cur?.feedback.length ? (
        <div style={{ margin: "0 14px 14px" }}>
          {cur.feedback.map((f, i) => (
            <div className="fb" key={i}><b>{f.by}</b> sent this back: {f.comment}</div>
          ))}
        </div>
      ) : null}

      <Defects item={item} done={refresh} />

      {item.tasks.length > 0 && (
        <>
          <div style={{ padding: "0 14px 6px", fontSize: 12, fontWeight: 650, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.04em" }}>
            Task plan · {item.tasks.filter((t) => t.status === "done").length}/{item.tasks.length} merged
          </div>
          <div className="dag"><TaskDag tasks={item.tasks} reviews={item.reviews} /></div>
        </>
      )}

      <div className="sections">
        <div id="item-files">
          <h4>Files{gateFiles.size ? <span className="to-review-count"> · {gateFiles.size} to review for this gate</span> : null}</h4>
          {uniqueFiles.length + reviewFiles.length === 0 ? (
            <div style={{ color: "var(--faint)" }}>None yet.</div>
          ) : (
            <ul className="files">
              {[...uniqueFiles].sort((a, b) => Number(gateFiles.has(b.path)) - Number(gateFiles.has(a.path))).map((f) => (
                <li key={f.path} className={gateFiles.has(f.path) ? "to-review" : ""}>
                  <button className="link mono" onClick={() => openFile(f.path)}>{f.path}</button>{" "}
                  <span style={{ color: "var(--faint)", fontSize: 12 }}>{f.stage}</span>
                  {gateFiles.has(f.path) && <span className="review-tag">to review</span>}
                </li>
              ))}
              {planFile && (
                <li><button className="link mono" onClick={() => openFile(planFile)}>{planFile}</button></li>
              )}
              {reviewFiles.map((p) => (
                <li key={p}><button className="link mono" onClick={() => openFile(p)}>{p}</button></li>
              ))}
            </ul>
          )}
          {stages.filter((s) => item.stages[s.name].summary).map((s) => (
            <p key={s.name} style={{ fontSize: 13, margin: "10px 0 0" }}>
              <b>{item.stage_titles[s.name] ?? s.title}:</b> {item.stages[s.name].summary}
            </p>
          ))}
        </div>
        <div>
          <h4>History</h4>
          <ul className="hist">
            {[...item.history].reverse().map((h, i) => (
              <li key={i}>
                <span className="time">{clock(h.ts)}</span>
                <span>
                  {h.what.replace(/_/g, " ")}
                  {h.stage ? ` · ${h.stage}` : ""}
                  {h.by ? ` · ${h.by}` : ""}
                  {h.what === "retyped" ? ` · ${String(h.from)} → ${String(h.to)}` : ""}
                  {h.comment ? ` — “${h.comment}”` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
