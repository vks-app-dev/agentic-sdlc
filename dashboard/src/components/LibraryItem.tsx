import DOMPurify from "dompurify";
import { marked } from "marked";
import { useEffect, useRef, useState } from "react";
import { agentTitle, api, libraryApi, type LibKind, type LibSource, type LibStandard } from "../api";
import { AgentAvatar } from "./Avatar";

export interface LibEntry {
  kind: LibKind;
  name: string;
  title: string;
  path: string;
  source: LibSource;
  exists: boolean;
  agents: string[];
  hasDefault: boolean;
  custom: boolean;
}

const SOURCE_TEXT: Record<LibSource, string> = {
  yours: "Your own file (set in sdlc.config.yaml)", edited: "Yours: uploaded or edited in this repo",
  company: "Company default", default: "Agentic SDLC default", missing: "Missing",
};

const html = (md: string) => DOMPurify.sanitize(marked.parse(md, { async: false }) as string);

/** One standard, skill or template: read it, download it, upload a new version, or put the default back. */
export function LibraryItem({ entry, onClose, onChanged }: {
  entry: LibEntry; onClose: () => void; onChanged: () => void;
}) {
  const [content, setContent] = useState<string | null>(null);
  const [pending, setPending] = useState<{ text: string; filename: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const load = () => {
    if (!entry.exists) return setContent("");
    api.file(entry.path).then((f) => setContent(f.content), (e: Error) => setErr(e.message));
  };
  useEffect(load, [entry.path, entry.exists]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [onClose]);

  const pick = (f: File | undefined) => {
    if (!f) return;
    setErr("");
    if (!/\.(md|markdown|txt)$/i.test(f.name)) return setErr("Upload a Markdown or text file (.md, .markdown, .txt)");
    if (f.size > 512 * 1024) return setErr("The file is larger than 512 KB");
    f.text().then((text) => setPending({ text, filename: f.name }));
  };

  const act = (p: Promise<{ path: string; previous?: string | null }>, done: string) => {
    setBusy(true);
    setErr("");
    p.then((r) => {
      setMsg(`${done}${r.previous ? ` · the previous version is kept in ${r.previous}` : ""}`);
      setPending(null);
      onChanged();
      window.setTimeout(load, 100);
    }, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };

  const shown = pending ? pending.text : content;
  return (
    <div className="viewer-backdrop" onClick={onClose}>
      <div className="viewer lib-item" onClick={(e) => e.stopPropagation()}>
        <header className="lib-item-head">
          <div className="lib-item-title">
            <span className="lib-kind">{entry.kind === "template" ? "📄 Template" : entry.path.endsWith("SKILL.md") ? "🧩 Skill" : "📘 Standard"}</span>
            <b>{entry.title}</b>
            <code>{entry.path}</code>
          </div>
          <button className="btn" onClick={onClose}>Close</button>
        </header>

        <div className="lib-item-meta">
          <span className={`lib-src ${entry.source}`}>{SOURCE_TEXT[entry.source]}</span>
          <span className="lib-agents" title={entry.agents.map(agentTitle).join(", ")}>
            {entry.agents.length ? entry.agents.map((a) => <AgentAvatar key={a} name={a} size={22} />)
              : <span className="muted small">{entry.kind === "template" ? "filled by a person" : "every agent"}</span>}
          </span>
          <span className="muted small">{entry.agents.length ? `followed by ${entry.agents.map(agentTitle).join(", ")}` : ""}</span>
        </div>

        <div className="lib-actions">
          <a className={`btn ${entry.exists ? "" : "disabled"}`} href={libraryApi.downloadUrl(entry.kind, entry.name)} download>⬇ Download</a>
          <button className="btn primary" disabled={busy} onClick={() => input.current?.click()}>⬆ Upload new version</button>
          <input ref={input} type="file" accept=".md,.markdown,.txt,text/markdown,text/plain" hidden
            onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
          {entry.hasDefault && entry.source !== "default" && entry.source !== "company" && (
            <button className="btn" disabled={busy} onClick={() => {
              if (confirm(`Put the default ${entry.title} back? Your current version is kept in .sdlc/library-history/.`))
                act(libraryApi.reset(entry.kind, entry.name), "Default restored");
            }}>↺ Reset to default</button>
          )}
          {entry.custom && (
            <button className="btn danger" disabled={busy} onClick={() => {
              if (confirm(`Stop using ${entry.title}? The file stays in the repo.`))
                libraryApi.remove(entry.name).then(() => { onChanged(); onClose(); }, (e: Error) => setErr(e.message));
            }}>Remove</button>
          )}
        </div>

        {pending && (
          <div className="lib-pending">
            <div>
              <b>Preview of {pending.filename}</b> ({pending.text.split("\n").length} lines). Agents will follow this from
              their next run{entry.exists ? "; the current version is kept so you can get it back." : "."}
            </div>
            <div className="lib-pending-actions">
              <button className="btn primary" disabled={busy}
                onClick={() => act(libraryApi.replace(entry.kind, entry.name, pending.text, pending.filename), "Uploaded")}>
                {busy ? "Saving…" : "Replace"}
              </button>
              <button className="btn" onClick={() => setPending(null)}>Cancel</button>
            </div>
          </div>
        )}
        {msg && !pending && <div className="lib-msg">✓ {msg}</div>}
        {err && <div className="error lib-err">{err}</div>}

        <div className="md lib-preview"
          dangerouslySetInnerHTML={{ __html: shown == null ? "<p>Loading…</p>" : shown ? html(shown) : "<p>The file is missing.</p>" }} />
      </div>
    </div>
  );
}

const PRESETS: { label: string; pick: (a: string) => boolean }[] = [
  { label: "Coders & reviewers", pick: (a) => ["dev-coder", "dev-code-reviewer", "dev-pr-reviewer", "dev-pr-responder"].includes(a) },
  { label: "Analysts", pick: (a) => a.startsWith("req-") },
  { label: "Designers", pick: (a) => a.startsWith("design-") },
  { label: "Planners", pick: (a) => a.startsWith("plan-") },
  { label: "Testers", pick: (a) => a.startsWith("qa-") || a === "dev-test-writer" },
  { label: "Release", pick: (a) => a.startsWith("rel-") },
];
const GROUP: Record<string, string> = { req: "Requirements", design: "Design", plan: "Planning", dev: "Development", qa: "QA", rel: "Release" };

type FileIn = { text: string; filename: string };

function readFile(f: File | undefined, set: (x: FileIn) => void, fail: (m: string) => void) {
  if (!f) return;
  if (!/\.(md|markdown|txt)$/i.test(f.name)) return fail("Upload a Markdown or text file (.md, .markdown, .txt)");
  if (f.size > 512 * 1024) return fail("The file is larger than 512 KB");
  f.text().then((text) => set({ text, filename: f.name }));
}

/** Replace one of the standards the project has, or add a new one for the agents the person chooses. */
export function AddStandard({ agents, standards, onClose, onAdded }: {
  agents: string[]; standards: LibStandard[]; onClose: () => void; onAdded: () => void;
}) {
  const [mode, setMode] = useState<"replace" | "new" | null>(null);
  const [target, setTarget] = useState<string>("");
  const [file, setFile] = useState<FileIn | null>(null);
  const [title, setTitle] = useState("");
  const [name, setName] = useState("");
  const [skill, setSkill] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [all, setAll] = useState(false);
  const [match, setMatch] = useState<{ name: string; title: string; reason: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const slug = (x: string) => x.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  const groups = [...new Set(agents.map((a) => a.split("-")[0]))];
  const tgt = standards.find((x) => x.name === target);

  // a new upload that looks like an existing standard: offer to replace that one instead
  useEffect(() => {
    if (mode !== "new" || (!file && !title)) return setMatch(null);
    const t = window.setTimeout(() => libraryApi.similar(title, file?.text ?? "").then((r) => setMatch(r.match), () => undefined), 250);
    return () => window.clearTimeout(t);
  }, [mode, file, title]);

  const done = (p: Promise<unknown>) => {
    setBusy(true);
    setErr("");
    p.then(() => { onAdded(); onClose(); }, (e: Error) => setErr(e.message)).finally(() => setBusy(false));
  };

  const fileInput = (
    <label className="field">
      <span>Your file (.md or .txt)</span>
      <input type="file" accept=".md,.markdown,.txt" onChange={(e) => readFile(e.target.files?.[0], (f) => {
        setFile(f);
        const t = f.text.match(/^#\s+(.+)$/m)?.[1] ?? f.filename.replace(/\.(md|markdown|txt)$/i, "");
        if (!title) setTitle(t);
        if (!name) setName(slug(t));
      }, setErr)} />
    </label>
  );

  return (
    <div className="viewer-backdrop demo-backdrop" onClick={onClose}>
      <div className="add-std" onClick={(e) => e.stopPropagation()}>
        <div className="demo-head"><b>Add your own</b><span className="spacer" /><button className="btn" onClick={onClose}>Close</button></div>
        <div className="add-body">
          <div className="section-label">Is this a new version of something we have, or something new?</div>
          <div className="mode-pick">
            <button className={`mode ${mode === "replace" ? "on" : ""}`} onClick={() => setMode("replace")}>
              <b>🔁 Replace one we have</b>
              <span className="muted small">Your version of Coding, Testing, Security… The same agents follow it; the default is no longer used.</span>
            </button>
            <button className={`mode ${mode === "new" ? "on" : ""}`} onClick={() => setMode("new")}>
              <b>➕ Add something new</b>
              <span className="muted small">A topic none of ours cover (payments rules, a glossary, a compliance policy). You choose who follows it.</span>
            </button>
          </div>

          {mode === "replace" && (
            <>
              <div className="section-label">Which one does it replace?</div>
              <div className="replace-pick">
                {standards.map((x) => (
                  <button key={x.name} className={`replace-opt ${target === x.name ? "on" : ""}`} onClick={() => setTarget(x.name)}>
                    <b>{x.title}</b>
                    <span className="lib-agents">{x.agents.slice(0, 5).map((a) => <AgentAvatar key={a} name={a} size={18} />)}</span>
                    <span className="muted small">{x.all ? "every agent" : `${x.agents.length} agents`}</span>
                  </button>
                ))}
              </div>
              {fileInput}
              {tgt && (
                <div className="muted small">
                  Followed by {tgt.all ? "every agent" : tgt.agents.map(agentTitle).join(", ")}. Your file goes to <code>{tgt.path}</code>;
                  the current version is kept in <code>.sdlc/library-history/</code>.
                </div>
              )}
              {err && <div className="error">{err}</div>}
              <div className="proposal-actions">
                <button className="btn primary big" disabled={busy || !file || !tgt}
                  onClick={() => done(libraryApi.replace("standard", target, file!.text, file!.filename))}>
                  {busy ? "Saving…" : tgt ? `Replace ${tgt.title}` : "Replace"}
                </button>
                <button className="btn" onClick={onClose}>Cancel</button>
              </div>
            </>
          )}

          {mode === "new" && (
            <>
              {fileInput}
              <label className="field"><span>Title</span><input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. Payments rules" /></label>
              <label className="field"><span>Short name</span><input value={name} onChange={(e) => setName(slug(e.target.value))} placeholder="payments-rules" /></label>
              {match && (
                <div className="similar">
                  ⚠ This looks like <b>{match.title}</b>: {match.reason}. Agents would follow two versions.
                  <button className="btn primary" onClick={() => { setMode("replace"); setTarget(match.name); }}>Replace {match.title} instead</button>
                </div>
              )}
              <div className="types">
                <button className={`type ${!skill ? "on" : ""}`} onClick={() => setSkill(false)}>📘 Standard <span className="muted small">docs/standards/</span></button>
                <button className={`type ${skill ? "on" : ""}`} onClick={() => setSkill(true)}>🧩 Claude Code skill <span className="muted small">.claude/skills/</span></button>
              </div>
              <div className="section-label">Who follows it? (required)</div>
              <div className="presets">
                {PRESETS.map((p) => {
                  const these = agents.filter(p.pick);
                  const on = !all && these.length > 0 && these.every((a) => chosen.includes(a));
                  return (
                    <button key={p.label} className={`preset ${on ? "on" : ""}`} onClick={() => {
                      setAll(false);
                      setChosen(on ? chosen.filter((a) => !these.includes(a)) : [...new Set([...chosen, ...these])]);
                    }}>{p.label}</button>
                  );
                })}
                <button className={`preset ${all ? "on" : ""}`} onClick={() => { setAll(!all); setChosen([]); }}>All agents</button>
              </div>
              {!all && (
                <details className="fine-tune" open={chosen.length > 0}>
                  <summary className="muted small">Fine-tune: pick individual agents ({chosen.length} chosen)</summary>
                  <div className="agent-pick">
                    {groups.map((g) => (
                      <div key={g} className="agent-group">
                        <div className="muted small">{GROUP[g] ?? g}</div>
                        {agents.filter((a) => a.startsWith(`${g}-`)).map((a) => (
                          <label key={a} className={`agent-chip ${chosen.includes(a) ? "on" : ""}`}>
                            <input type="checkbox" checked={chosen.includes(a)}
                              onChange={(e) => setChosen(e.target.checked ? [...chosen, a] : chosen.filter((x) => x !== a))} />
                            <AgentAvatar name={a} size={20} /> {agentTitle(a)}
                          </label>
                        ))}
                      </div>
                    ))}
                  </div>
                </details>
              )}
              <div className="muted small">
                {all ? "Every agent will read it before it starts." : chosen.length ? `${chosen.length} agents will read it: ${chosen.map(agentTitle).join(", ")}.` : "Choose who follows it."}
              </div>
              {err && <div className="error">{err}</div>}
              <div className="proposal-actions">
                <button className="btn primary big" disabled={busy || !file || !name || (!all && !chosen.length)}
                  onClick={() => done(libraryApi.add({ name, title, agents: all ? "all" : chosen, skill, content: file!.text, filename: file!.filename }))}>
                  {busy ? "Adding…" : "Add it"}
                </button>
                <button className="btn" onClick={onClose}>Cancel</button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
