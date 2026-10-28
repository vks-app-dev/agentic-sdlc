import { useEffect, useRef, useState } from "react";
import { api, since, type Run } from "../api";
import { ErrorNote } from "./ErrorNote";

/** Log of one dashboard-started agent run; polls while it is running. */
export function RunLog({ run, onClose, onResumed }: { run: Run; onClose: () => void; onResumed?: (r: Run) => void }) {
  const [text, setText] = useState("");
  const [current, setCurrent] = useState<Run>(run);
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(false);
  useEffect(() => setCurrent(run), [run.id]);
  const box = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.runLog(run.id).then((r) => {
        if (!alive) return;
        setText(r.text);
        setCurrent(r.run);
        if (r.run.status === "running") window.setTimeout(load, 2000);
      }, () => undefined);
    load();
    return () => {
      alive = false;
    };
  }, [run.id]);

  useEffect(() => {
    box.current?.scrollTo({ top: box.current.scrollHeight });
  }, [text]);

  return (
    <div className="runlog">
      <div className="runlog-head">
        <b>Agent run</b> <span className="mono">{current.id}</span> ·{" "}
        <span className={`run-state ${current.status}`}>{current.status === "paused" ? "⏸ paused" : current.status}</span>
        {current.exit_code != null && !["finished", "paused", "stopped"].includes(current.status) ? ` (exit ${current.exit_code})` : ""}
        <span className="spacer" />
        {current.status === "running" && (
          <>
            <button className="btn" disabled={!!busy} title="Stop the agents now and keep their work; Resume continues where they were"
              onClick={() => setConfirm(true)}>
              {busy === "pause" ? "Pausing…" : "⏸ Pause"}
            </button>
            <button className="btn danger" disabled={!!busy} title="Stop the agents; the next run starts fresh from the item's current state"
              onClick={() => { setBusy("stop"); setErr(""); api.stopRun(current.id).then(setCurrent, (e: Error) => setErr(e.message)).finally(() => setBusy("")); }}>
              ■ Stop
            </button>
          </>
        )}
        {current.status === "paused" && (
          <button className="btn primary" disabled={!!busy}
            onClick={() => { setBusy("resume"); setErr(""); api.resume(current.req).then((r) => { onResumed?.(r); setCurrent(r); }, (e: Error) => setErr(e.message)).finally(() => setBusy("")); }}>
            {busy === "resume" ? "Resuming…" : "▶ Resume"}
          </button>
        )}
        <button className="btn" onClick={onClose}>Hide log</button>
      </div>
      {current.status === "failed" && current.error && (
        <div className="run-error">⚠ {current.error}<ErrorNote message={/sign/i.test(current.error) ? `Claude Code: ${current.error}` : ""} /></div>
      )}
      {confirm && current.status === "running" && (
        <div className="pause-confirm">
          <div>
            <b>Pause the agents?</b> They stop right away. Everything they have finished is kept. An agent in the middle of
            a step is cut off and does that step again when you press ▶ Resume, which continues the same conversation.
          </div>
          <div className="pause-actions">
            <button className="btn primary" onClick={() => {
              setConfirm(false); setBusy("pause"); setErr("");
              api.stopRun(current.id, true).then(setCurrent, (e: Error) => setErr(e.message)).finally(() => setBusy(""));
            }}>⏸ Pause now</button>
            <button className="btn" onClick={() => setConfirm(false)}>Keep running</button>
          </div>
        </div>
      )}
      {busy === "pause" && <div className="run-paused">Pausing: stopping the agents and keeping their work…</div>}
      {err && <div className="run-error">⚠ {err}</div>}
      {current.status === "paused" && <div className="run-paused">⏸ Paused. The agents have stopped and their work so far is kept. ▶ Resume continues the same conversation where it left off.</div>}
      <pre ref={box}>{text || "Starting Claude Code…"}</pre>
    </div>
  );
}

export function RunButton({ req, run, onStarted, label = "▶ Run agents" }: {
  req: string; run: Run | null; onStarted: (r: Run) => void; label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  if (run && run.status === "running") {
    return <span className="chip"><span className="dot-live pulse" /> Agents running · {since(run.started)}</span>;
  }
  return (
    <>
      <button
        className="btn primary"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          setErr("");
          api.run(req).then(onStarted, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
        }}
      >
        {busy ? "Starting…" : label}
      </button>
      <ErrorNote message={err} onRetry={() => api.run(req).then(onStarted, (e: Error) => setErr(e.message))} />
    </>
  );
}
