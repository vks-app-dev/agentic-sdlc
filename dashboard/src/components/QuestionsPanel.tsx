import { useEffect, useState } from "react";
import { api, qApi, type Questions } from "../api";
import { ErrorNote } from "./ErrorNote";

/** The agents' open questions in a review: answer them (or let AI draft answers), then approve or send back. */
export function QuestionsPanel({ req, onChanged, onSentBack, compact = false }: {
  req: string; onChanged?: (q: Questions) => void; onSentBack?: () => void; compact?: boolean;
}) {
  const [data, setData] = useState<Questions | null>(null);
  const [open, setOpen] = useState(!compact);
  const [filter, setFilter] = useState<"open" | "blocking" | "all">("open");
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [byAi, setByAi] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [more, setMore] = useState<Set<string>>(new Set());

  const load = () => qApi.get(req).then((q) => { setData(q); onChanged?.(q); }, () => undefined);
  useEffect(() => { load(); }, [req]);
  if (!data || !data.questions.length) return null;

  const shown = data.questions.filter((q) => filter === "all" || (!q.answered && (filter === "open" || q.blocking)));
  const filled = Object.entries(draft).filter(([, v]) => v.trim());
  const suggest = () => {
    setBusy("suggest"); setErr("");
    qApi.suggest(req).then((r) => {
      setDraft((d) => ({ ...r.answers, ...Object.fromEntries(Object.entries(d).filter(([, v]) => v.trim())) }));
      setByAi(new Set(Object.keys(r.answers)));
      setNote(`AI drafted ${Object.keys(r.answers).length} answers${r.cost_usd != null ? ` ($${r.cost_usd.toFixed(2)})` : ""}. Review and edit them, then save.`);
    }, (e: Error) => setErr(e.message)).finally(() => setBusy(""));
  };
  const save = (sendBack: boolean) => {
    setBusy(sendBack ? "send" : "save"); setErr("");
    const answers = Object.fromEntries(filled);
    qApi.save(req, answers).then(async (q) => {
      setData(q); onChanged?.(q); setDraft({}); setByAi(new Set());
      setNote(`Saved ${q.saved?.length ?? 0} answer${q.saved?.length === 1 ? "" : "s"} to ${q.file}.`);
      if (sendBack) {
        await api.reject(req, `I answered ${q.saved?.length ?? 0} open questions in ${q.file}. Update the document to `
          + "use these answers (acceptance criteria, rules, NFRs), resolve the questions, and ask only what is still unclear.");
        await api.run(req);
        onSentBack?.();
      }
    }, (e: Error) => setErr(e.message)).finally(() => setBusy(""));
  };

  return (
    <div className={`questions ${data.blocking_open ? "has-blocking" : ""}`}>
      <button className="q-head" onClick={() => setOpen(!open)}>
        <span className="q-icon">?</span>
        <span className="q-title">
          <b>{data.open ? `${data.open} open question${data.open > 1 ? "s" : ""} from the agents` : "All questions answered"}</b>
          {data.blocking_open ? <span className="q-block"> · {data.blocking_open} blocking</span> : null}
          <span className="muted small"> · in {data.file}</span>
        </span>
        <span className="q-toggle">{open ? "Hide" : "Answer them"}</span>
      </button>
      {open && (
        <div className="q-body">
          <div className="q-tools">
            <div className="tabs">
              {(["open", "blocking", "all"] as const).map((f) => (
                <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                  {f === "open" ? `Open ${data.open}` : f === "blocking" ? `Blocking ${data.blocking_open}` : `All ${data.questions.length}`}
                </button>
              ))}
            </div>
            <span className="spacer" />
            <button className="btn" disabled={!!busy || !data.open} onClick={suggest}>
              {busy === "suggest" ? "✨ Reading the spec and repo…" : "✨ Suggest answers with AI"}
            </button>
          </div>
          {note && <div className="q-note">{note}</div>}
          <div className="q-list">
            {shown.map((q) => {
              const long = q.text.length > 220;
              const value = draft[q.id] ?? "";
              return (
                <div key={q.id} className={`q-item ${q.answered ? "done" : ""} ${q.blocking ? "blocking" : ""}`}>
                  <div className="q-item-head">
                    <span className="q-id">{q.id}</span>
                    {q.blocking && <span className="q-badge">blocking</span>}
                    {q.answered && <span className="q-badge ok">answered</span>}
                    {byAi.has(q.id) && <span className="ai-tag">✨ AI draft · review</span>}
                  </div>
                  <div className={`q-text ${long && !more.has(q.id) ? "clamp" : ""}`}>{q.text}</div>
                  {long && (
                    <button className="link small" onClick={() => setMore((m) => { const n = new Set(m); if (n.has(q.id)) n.delete(q.id); else n.add(q.id); return n; })}>
                      {more.has(q.id) ? "Show less" : "Show more"}
                    </button>
                  )}
                  {q.answered && !(q.id in draft) ? (
                    <div className="q-answer">
                      <b>Answer:</b> {q.answer || "(marked done)"}
                      <button className="link small" onClick={() => setDraft({ ...draft, [q.id]: q.answer.replace(/\s*_\([^)]*\)_$/, "") })}>Edit</button>
                    </div>
                  ) : (
                    <>
                      {q.options.length > 0 && (
                        <div className="q-options">
                          {q.options.map((o) => (
                            <button key={o.key} className={`q-opt ${value.startsWith(`(${o.key})`) ? "on" : ""}`}
                              onClick={() => setDraft({ ...draft, [q.id]: `(${o.key}) ${o.label}` })}>({o.key}) {o.label}</button>
                          ))}
                        </div>
                      )}
                      <textarea rows={2} value={value} placeholder="Your answer (leave empty to skip for now)"
                        className={byAi.has(q.id) ? "ai-drafted" : ""}
                        onChange={(e) => setDraft({ ...draft, [q.id]: e.target.value })} />
                    </>
                  )}
                </div>
              );
            })}
            {!shown.length && <div className="muted small">Nothing here.</div>}
          </div>
          <ErrorNote message={err} onRetry={suggest} />
          <div className="q-actions">
            <button className="btn primary" disabled={!!busy || !filled.length} onClick={() => save(false)}>
              {busy === "save" ? "Saving…" : `Save ${filled.length || ""} answer${filled.length === 1 ? "" : "s"}`}
            </button>
            <button className="btn" disabled={!!busy || !filled.length} onClick={() => save(true)}
              title="Save the answers, send the stage back and start the agents so they update the document">
              {busy === "send" ? "Sending…" : "Save & send back to the agents"}
            </button>
            <span className="muted small">Answers are written into {data.file}, which the next agents read.</span>
          </div>
        </div>
      )}
    </div>
  );
}
