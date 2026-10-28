import { useEffect, useState } from "react";
import { fixApi, type Blocker, type Run, type SpecForm } from "../api";
import { ErrorNote } from "./ErrorNote";

/** "Before agents can start": what a person must fix, each with the button that fixes it. */
export function NeedsYou({ req, blockers, onFixed, onRun, openFile }: {
  req: string; blockers: Blocker[]; onFixed: () => void; onRun: (r: Run) => void; openFile: (path: string) => void;
}) {
  const [open, setOpen] = useState<Blocker | null>(null);
  const [err, setErr] = useState("");
  if (!blockers.length) return null;
  const must = blockers.filter((b) => b.blocking !== false);
  return (
    <div className={`needs-you ${must.length ? "" : "info-only"}`}>
      <div className="needs-head">
        <b>{must.length ? "Before agents can start" : "From the last run"}</b>
        <span className="muted small">
          {must.length
            ? `${must.length} thing${must.length > 1 ? "s" : ""} need${must.length > 1 ? "" : "s"} you. Agents are held back until they're done, so no run is wasted.`
            : "The agents stopped and said what they needed. Do what it asks (if anything is left for you), then run them again."}
        </span>
      </div>
      <ol className="needs-list">
        {blockers.map((b, i) => (
          <li key={b.code} className={b.blocking === false ? "info" : ""}>
            <span className="needs-num">{b.blocking === false ? "💬" : i + 1}</span>
            <div className="needs-body">
              <b>{b.title}</b>
              <div className="muted small">{b.detail}</div>
              {b.code === "agent_help" && !!b.files?.length && (
                <div className="help-files">
                  <span className="muted small">Read first:</span>
                  {b.files.map((f) => (
                    <button key={f} className="btn file-btn" onClick={() => openFile(f)} title={`Open ${f}`}>
                      📄 {f.split("/").pop()}
                    </button>
                  ))}
                </div>
              )}
              {b.code === "agent_help" && b.decision && (
                <Decision req={req} onDone={(r) => { onRun(r); onFixed(); }} onSaved={onFixed} />
              )}
              {b.code === "agent_help" && <NextSteps req={req} blocker={b} />}
            </div>
            {b.action === "edit_spec" && <button className="btn primary" onClick={() => setOpen(b)}>Fill in the spec</button>}
            {b.action === "commit" && <button className="btn primary" onClick={() => setOpen(b)}>Review &amp; commit</button>}
            {b.action === "fix_commits" && (
              <button className="btn primary" onClick={() => fixApi.fixCommits(req).then(onFixed, (e: Error) => setErr(e.message))}>
                Fix commits
              </button>
            )}
            {b.action === "reset_tasks" && (
              <button className="btn primary" onClick={() => fixApi.resetTasks(req).then(onFixed, (e: Error) => setErr(e.message))}>
                Reset
              </button>
            )}
            {b.action === "free_worktrees" && (
              <button className="btn primary" onClick={() => fixApi.freeWorktrees(req).then(onFixed, (e: Error) => setErr(e.message))}>
                Free it
              </button>
            )}
            {b.action === "switch" && !blockers.some((x) => x.action === "commit") && (
              <button className="btn primary" onClick={() => fixApi.switchTo(b.target!).then(onFixed, (e: Error) => setErr(e.message))}>
                Switch to {b.target}
              </button>
            )}
            {b.action === "switch" && blockers.some((x) => x.action === "commit") && (
              <span className="muted small">Done by step {blockers.findIndex((x) => x.action === "commit") + 1}</span>
            )}
          </li>
        ))}
      </ol>
      {must.length > 0 && <div className="needs-foot">
        {err && <span className="error">{err}</span>}
        <span className="spacer" />
        <button className="link small" title="Start the agents now; they will probably stop at the same problems"
          onClick={() => fixApi.runAnyway(req).then(onRun, (e: Error) => setErr(e.message))}>Run anyway</button>
      </div>}
      {!must.length && (
        <div className="needs-foot info-actions">
          <button className="btn primary" onClick={() => fixApi.runAnyway(req).then(onRun, (e: Error) => setErr(e.message))}>
            ▶ Run agents again
          </button>
          <button className="btn" title="The request is dealt with or no longer applies; hide this note"
            onClick={() => fixApi.helpHandled(req).then(onFixed, (e: Error) => setErr(e.message))}>Mark as handled</button>
          {err && <span className="error">{err}</span>}
        </div>
      )}
      {open?.action === "edit_spec" && <SpecEditor req={req} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); onFixed(); }} />}
      {open?.action === "commit" && (
        <CommitDialog blocker={open} req={req} onClose={() => setOpen(null)} onDone={() => { setOpen(null); onFixed(); }} />
      )}
    </div>
  );
}

function SpecEditor({ req, onClose, onSaved }: { req: string; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<SpecForm | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [thinking, setThinking] = useState(false);
  const [drafted, setDrafted] = useState<string[]>([]);
  const [draftNote, setDraftNote] = useState("");
  const suggest = () => {
    setThinking(true);
    setErr("");
    fixApi.suggestSpec(req).then((r) => {
      setValues((v) => ({ ...v, ...r.values }));
      setDrafted(Object.keys(r.values));
      setDraftNote(`Drafted by AI (${r.model}${r.cost_usd != null ? `, $${r.cost_usd.toFixed(2)}` : ""}). Review and edit, then save.`);
    }, (e: Error) => setErr(e.message)).finally(() => setThinking(false));
  };
  useEffect(() => {
    fixApi.spec(req).then((f) => {
      setForm(f);
      setValues(Object.fromEntries(f.sections.map((s) => [s.name, s.value])));
    }, (e: Error) => setErr(e.message));
  }, [req]);
  // the same rule as the engine: a checklist needs an outcome of its own besides "Existing tests pass"
  const filled = (kind: string, v: string) => kind !== "checklist" ? !!v.trim()
    : v.split("\n").map((l) => l.replace(/^[-*]\s*(\[.?\]\s*)?/, "").trim()).some((l) => l && l.toLowerCase() !== "existing tests pass");
  const missing = form?.sections.filter((s) => s.required && !filled(s.kind, values[s.name] ?? "")) ?? [];
  return (
    <div className="viewer-backdrop demo-backdrop" onClick={onClose}>
      <div className="add-std" onClick={(e) => e.stopPropagation()}>
        <div className="demo-head">
          <b>Fill in the spec</b> <span className="mono muted small">{form?.file}</span>
          <span className="spacer" />
          <button className="proposal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="add-body">
          {!form ? <div className="empty">{err || "Loading…"}</div> : (
            <>
              <div className="suggest-row">
                <button className="btn primary" disabled={thinking} onClick={suggest}>
                  {thinking ? "✨ Reading the repo and drafting…" : drafted.length ? "✨ Draft again" : "✨ Suggest with AI"}
                </button>
                <span className="muted small">
                  {draftNote || "AI reads your request and the repo (read-only) and drafts every section for you to review. About $0.02–0.10."}
                </span>
              </div>
              {form.sections.map((s) => (
                <label key={s.name} className="field">
                  <span><b>{s.name}</b>{s.required ? " (required)" : ""} · {s.hint}
                    {drafted.includes(s.name) && <span className="ai-tag">✨ AI draft · review</span>}</span>
                  <textarea rows={s.kind === "checklist" ? 4 : 3} value={values[s.name] ?? ""} className={drafted.includes(s.name) ? "ai-drafted" : ""}
                    placeholder={s.kind === "checklist" ? "One outcome per line, e.g.\nExisting tests pass\nuv.lock pins the new version" : s.hint}
                    disabled={thinking} onChange={(e) => setValues({ ...values, [s.name]: e.target.value })} />
                  {s.kind === "checklist" && (
                    <span className={`small ${s.required && !filled(s.kind, values[s.name] ?? "") ? "error" : "muted"}`}>
                      {s.required && !filled(s.kind, values[s.name] ?? "")
                        ? "Add at least one outcome for this task besides “Existing tests pass”."
                        : "Each line becomes a checkbox the reviewers tick."}
                    </span>
                  )}
                </label>
              ))}
              <ErrorNote message={err} onRetry={suggest} />
              <div className="proposal-actions">
                <button className="btn primary big" disabled={busy || missing.length > 0} onClick={() => {
                  setBusy(true);
                  setErr("");
                  const changed = Object.fromEntries(Object.entries(values).filter(([, v]) => v.trim()));
                  fixApi.saveSpec(req, changed).then((f) => {
                    if (f.gaps.length) setErr(`Saved, but still needed: ${f.gaps.join(", ")}`);
                    else onSaved();
                  }, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
                }}>{busy ? "Saving…" : missing.length ? `Fill in ${missing.map((m) => m.name).join(", ")}` : "Save the spec"}</button>
                <button className="btn" onClick={onClose}>Cancel</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function CommitDialog({ blocker, req, onClose, onDone }: { blocker: Blocker; req: string; onClose: () => void; onDone: () => void }) {
  const files = blocker.files ?? [];
  const [chosen, setChosen] = useState<string[]>(files);
  const [target, setTarget] = useState(blocker.target ?? "main");
  const [message, setMessage] = useState("Project setup: Agentic SDLC config, standards and scripts");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const current = blocker.branch ?? "";
  const toBase = target !== current;
  return (
    <div className="viewer-backdrop demo-backdrop" onClick={onClose}>
      <div className="add-std" onClick={(e) => e.stopPropagation()}>
        <div className="demo-head">
          <b>Commit the setup files</b>
          <span className="spacer" />
          <button className="proposal-close" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="add-body">
          <div className="section-label">Commit to</div>
          <div className="types">
            <button className={`type ${target === (blocker.target ?? "main") ? "on" : ""}`} onClick={() => setTarget(blocker.target ?? "main")}>
              {blocker.target ?? "main"} <span className="muted small">recommended</span>
            </button>
            {current && current !== (blocker.target ?? "main") && (
              <button className={`type ${target === current ? "on" : ""}`} onClick={() => setTarget(current)}>
                {current} <span className="muted small">current branch</span>
              </button>
            )}
          </div>
          <div className="muted small commit-explain">
            {toBase
              ? `The files are committed to ${target}, then this folder switches to ${target} so ${req} starts from it. ${current} stays exactly as it is.`
                + (/^sdlc\/REQ-\d+/.test(current) ? ` Until ${current.replace("sdlc/", "")}'s pull request is merged, its code and documents live only on that branch, so this folder won't show them after the switch.` : "")
              : `The files are committed on ${current}. If it has an open pull request, they become part of it.`}
          </div>
          <div className="section-label">Files ({chosen.length} of {files.length})</div>
          <div className="commit-files">
            {files.map((f) => (
              <label key={f} className="commit-file">
                <input type="checkbox" checked={chosen.includes(f)}
                  onChange={(e) => setChosen(e.target.checked ? [...chosen, f] : chosen.filter((x) => x !== f))} />
                <span className="mono">{f}</span>
              </label>
            ))}
          </div>
          <label className="field"><span>Commit message</span><input value={message} onChange={(e) => setMessage(e.target.value)} /></label>
          {err && <div className="error">{err}</div>}
          <div className="proposal-actions">
            <button className="btn primary big" disabled={busy || !chosen.length || !message.trim()} onClick={() => {
              setBusy(true);
              setErr("");
              fixApi.commit(chosen, message, target).then(onDone, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
            }}>{busy ? "Committing…" : `Commit ${chosen.length} file${chosen.length === 1 ? "" : "s"} to ${target}`}</button>
            <button className="btn" onClick={onClose}>Cancel</button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** What to do next about what the agents asked: known cases from rules, anything else explained by AI on request. */
function NextSteps({ req, blocker }: { req: string; blocker: Blocker }) {
  const [steps, setSteps] = useState<string[]>(blocker.steps ?? []);
  const [byAi, setByAi] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const explain = () => {
    setBusy(true);
    setErr("");
    fixApi.explainHelp(req).then((r) => {
      setSteps(r.steps);
      setByAi(`explained by AI${r.cost_usd != null ? ` · $${r.cost_usd.toFixed(2)}` : ""}`);
    }, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <div className="next-steps">
      <div className="next-steps-head">
        <b>What to do next</b>
        {byAi && <span className="ai-tag">✨ {byAi}</span>}
      </div>
      <ol>{steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
      <div className="next-steps-foot">
        <button className={`btn ${blocker.known ? "" : "primary"}`} disabled={busy} onClick={explain}>{busy ? "✨ Reading the run…" : "✨ Explain with AI"}</button>
        <span className="muted small">{blocker.known ? "Steps for this kind of problem." : "Not a known problem: AI can read the run and suggest steps."}</span>
      </div>
      <ErrorNote message={err} onRetry={explain} />
    </div>
  );
}

/** Answer the agents' question: accept (the gap or the risk they describe) or say what to do instead. */
function Decision({ req, onDone, onSaved }: { req: string; onDone: (r: Run) => void; onSaved: () => void }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const answer = (accepted: boolean, run: boolean) => {
    setBusy(true);
    setErr("");
    fixApi.decide(req, accepted, note)
      .then(() => (run ? fixApi.runAnyway(req).then(onDone) : onSaved()))
      .catch((e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };
  return (
    <div className="decision">
      <b>Your decision</b>
      <textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)}
        placeholder="Why (optional for Accept), e.g. A FastAPI upgrade has no performance targets; the existing tests cover it." />
      <div className="decision-actions">
        <button className="btn primary" disabled={busy} onClick={() => answer(true, true)}>✓ Accept &amp; run agents again</button>
        <button className="btn danger" disabled={busy || !note.trim()} onClick={() => answer(false, true)}
          title="Write what the agents should do instead">✗ Not accepted: do this instead</button>
        <button className="btn" disabled={busy} onClick={() => answer(true, false)}>Accept only</button>
      </div>
      <span className="muted small">Your decision is written to the spec's “Decisions” section, which every agent reads.</span>
      {err && <span className="error">{err}</span>}
    </div>
  );
}
