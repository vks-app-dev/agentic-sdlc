import { useEffect, useState } from "react";
import { agentTitle, libraryApi, type LibSource, type Library as LibraryData } from "../api";
import { AgentAvatar } from "./Avatar";
import { AddStandard, LibraryItem, type LibEntry } from "./LibraryItem";

const SOURCE: Record<LibSource, { label: string; tip: string }> = {
  yours: { label: "Yours", tip: "The team's own file, set in sdlc.config.yaml" },
  edited: { label: "Edited here", tip: "Started from the default and changed in this repo" },
  company: { label: "Company default", tip: "From the company policy repo (org.yaml templates)" },
  default: { label: "Agentic SDLC default", tip: "Agentic SDLC's own version; edit the file to make it yours" },
  missing: { label: "Missing", tip: "The file is not there: run sdlc upgrade" },
};

type Filter = "all" | "own" | "default";
const isOwn = (s: LibSource) => s === "yours" || s === "edited";

function Agents({ names, none = "every agent" }: { names: string[]; none?: string }) {
  if (!names.length) return <span className="muted small">{none}</span>;
  return (
    <span className="lib-agents" title={names.map(agentTitle).join(", ")}>
      {names.slice(0, 6).map((n) => <AgentAvatar key={n} name={n} size={22} />)}
      {names.length > 6 && <span className="muted small">+{names.length - 6}</span>}
    </span>
  );
}

export function Library({ openFile }: { openFile: (path: string) => void }) {
  const [data, setData] = useState<LibraryData | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [open, setOpen] = useState<{ kind: "standard" | "template"; name: string } | null>(null);
  const [adding, setAdding] = useState(false);

  const load = () => libraryApi.get().then(setData, (e: Error) => setErr(e.message));
  useEffect(() => { load(); }, []);

  if (err) return <div className="error">{err}</div>;
  if (!data) return <div className="empty">Loading…</div>;

  const keep = (s: LibSource) => filter === "all" || (filter === "own" ? isOwn(s) : !isOwn(s));
  const own = data.standards.filter((s) => isOwn(s.source)).length + data.templates.filter((t) => isOwn(t.source)).length;
  const groups = [...new Set(data.templates.map((t) => t.group))];
  const builtin = new Set(["coding", "testing", "security", "api", "git", "code-review", "documentation", "ui"]);
  const entry: LibEntry | null = !open ? null : open.kind === "standard"
    ? (() => {
      const s = data.standards.find((x) => x.name === open.name);
      return s ? { kind: "standard", name: s.name, title: s.title, path: s.path, source: s.source, exists: s.exists,
        agents: s.agents, hasDefault: builtin.has(s.name), custom: !builtin.has(s.name) } : null;
    })()
    : (() => {
      const t = data.templates.find((x) => x.name === open.name);
      return t ? { kind: "template", name: t.name, title: t.title, path: t.path, source: t.source, exists: t.exists,
        agents: t.agents, hasDefault: true, custom: false } : null;
    })();

  return (
    <div className="library">
      <div className="lib-head">
        <div>
          <h1>Library</h1>
          <p className="muted">
            The standards, skills and templates your agents follow. Your own files win; anything you have not
            replaced uses Agentic SDLC's defaults, which you can edit in the repo.
          </p>
        </div>
        <div className="lib-stats">
          <div><b>{data.standards.length}</b><span>standards &amp; skills</span></div>
          <div><b>{data.templates.length}</b><span>templates</span></div>
          <div><b>{own}</b><span>your own</span></div>
        </div>
      </div>

      <div className="lib-toolbar">
      <div className="lib-filter tabs">
        {(["all", "own", "default"] as Filter[]).map((f) => (
          <button key={f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
            {f === "all" ? "All" : f === "own" ? "Your own" : "Defaults"}
          </button>
        ))}
      </div>
      <span className="spacer" />
      <button className="btn primary big" onClick={() => setAdding(true)}>⬆ Add your own</button>
      </div>

      <section>
        <h2 className="lib-h2">Standards &amp; skills <span className="muted small">agents read these before they start</span></h2>
        <div className="lib-grid">
          {data.standards.filter((s) => keep(s.source)).map((s) => (
            <div key={s.name} className={`lib-card ${s.source}`} role="button" tabIndex={0}
              onClick={() => setOpen({ kind: "standard", name: s.name })}
              onKeyDown={(e) => e.key === "Enter" && setOpen({ kind: "standard", name: s.name })}>
              <div className="lib-card-top">
                <span className="lib-kind">{s.kind === "skill" ? "🧩 Skill" : "📘 Standard"}</span>
                <span className={`lib-src ${s.source}`} title={SOURCE[s.source].tip}>{SOURCE[s.source].label}</span>
              </div>
              <div className="lib-title">{s.title}</div>
              <div className="muted small">{s.about}</div>
              <div className="lib-path mono">{s.path}</div>
              <div className="lib-foot">
                <Agents names={s.agents} />
                <span className="lib-open">Review · Update ›</span>
              </div>
            </div>
          ))}
        </div>
        {data.found.length > 0 && filter !== "default" && (
          <div className="lib-found">
            <b>Found in your repo, not used yet:</b>{" "}
            {data.found.map((f) => (
              <button key={f.path} className="link mono" onClick={() => openFile(f.path)} title={`Could replace: ${f.suggested_for}`}>
                {f.path}
              </button>
            ))}
            <div className="muted small">
              To make agents follow one: <code>sdlc standards use {data.found[0].suggested_for} {data.found[0].path}</code>
            </div>
          </div>
        )}
        <div className="muted small lib-hint">
          Use your own: <code>sdlc standards use coding CONTRIBUTING.md</code> · back to the default:{" "}
          <code>sdlc standards reset coding</code> · or just edit the file in <code>docs/standards/</code>.
        </div>
      </section>

      <section>
        <h2 className="lib-h2">Templates <span className="muted small">the documents agents fill for each work item</span></h2>
        {groups.map((g) => {
          const rows = data.templates.filter((t) => t.group === g && keep(t.source));
          if (!rows.length) return null;
          return (
            <div key={g} className="panel lib-table">
              <div className="lib-group">{g}</div>
              {rows.map((t) => (
                <div key={t.name} className="lib-row" role="button" tabIndex={0}
                  onClick={() => setOpen({ kind: "template", name: t.name })}
                  onKeyDown={(e) => e.key === "Enter" && setOpen({ kind: "template", name: t.name })}>
                  <span className="lib-row-name"><b>{t.title}</b><span className="mono">{t.path}</span></span>
                  <span className={`lib-src ${t.source}`} title={SOURCE[t.source].tip}>{SOURCE[t.source].label}</span>
                  <Agents names={t.agents} none="filled by a person" />
                  <span className="lib-open">Review ›</span>
                </div>
              ))}
            </div>
          );
        })}
        <div className="muted small lib-hint">
          Click any template to review it, download it or upload your own version. In a terminal:{" "}
          <code>sdlc templates --update &lt;name&gt;</code> takes a newer default.
        </div>
      </section>
      {entry && <LibraryItem entry={entry} onClose={() => setOpen(null)} onChanged={load} />}
      {adding && <AddStandard agents={data.agents} standards={data.standards} onClose={() => setAdding(false)} onAdded={load} />}
    </div>
  );
}
