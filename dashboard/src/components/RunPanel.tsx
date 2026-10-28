import { useEffect, useRef, useState } from "react";
import { api, since, type Run } from "../api";
import { ErrorNote } from "./ErrorNote";

/** Log of one dashboard-started agent run; polls while it is running. */
export function RunLog({ run, onClose }: { run: Run; onClose: () => void }) {
  const [text, setText] = useState("");
  const [current, setCurrent] = useState<Run>(run);
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
        <b>Agent run</b> <span className="mono">{current.id}</span> · {current.status}
        {current.exit_code != null && current.status !== "finished" ? ` (exit ${current.exit_code})` : ""}
        <span className="spacer" />
        {current.status === "running" && (
          <button className="btn danger" onClick={() => api.stopRun(current.id).then(setCurrent)}>Stop</button>
        )}
        <button className="btn" onClick={onClose}>Hide log</button>
      </div>
      {current.status === "failed" && current.error && (
        <div className="run-error">⚠ {current.error}<ErrorNote message={/sign/i.test(current.error) ? `Claude Code: ${current.error}` : ""} /></div>
      )}
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
