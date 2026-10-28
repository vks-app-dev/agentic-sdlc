import { useEffect, useMemo, useState } from "react";
import { agentTitle, type TeamLead } from "../api";
import { Avatar } from "./Avatar";

// A presentation, not a run: it walks a sample request through this project's real team (its enabled
// stages, their agents in order, and the human gates) so people can see how the work flows.

const REQ = "REQ-042";
const TASK = "Let users export their expenses as CSV";

interface Frame {
  stage: string;
  kind: "ask" | "sort" | "intro" | "step" | "gate" | "deploy" | "done";
  active: string[];
  caption: string;
  detail?: string;
  outputs: string[];
}

function frames(team: TeamLead[]): Frame[] {
  const out: Frame[] = [
    { stage: "", kind: "ask", active: [], caption: "You describe what you need", detail: `“${TASK}”`, outputs: [] },
    { stage: "", kind: "sort", active: [], caption: "Agentic SDLC sorts it", detail: `✨ Feature · ${REQ} · you confirm, agents start`, outputs: [] },
  ];
  for (const t of team) {
    if (t.status === "human") {
      out.push({ stage: t.stage, kind: "deploy", active: [], caption: "You deploy to production",
        detail: "Agents never deploy to production. You merge, deploy, record it, and the smoke check runs after.", outputs: [] });
      continue;
    }
    if (!t.enabled || !t.agents.length) continue;
    const standard = t.agents.filter((a) => !a.for.length && !a.when);
    out.push({ stage: t.stage, kind: "intro", active: [], caption: `${t.lead} picks up ${REQ}`, detail: t.purpose, outputs: [] });
    for (const step of [...new Set(standard.map((a) => a.step))]) {
      const here = standard.filter((a) => a.step === step);
      out.push({
        stage: t.stage, kind: "step", active: here.map((a) => a.name),
        caption: here.length > 1 ? `${here.length} agents work at the same time` : `${agentTitle(here[0].name)} is working`,
        detail: here.map((a) => `${agentTitle(a.name)}: ${a.does}`).join("\n"),
        outputs: here.map((a) => a.output.replace(/\{req\}/g, REQ)),
      });
    }
    out.push(t.gate.length
      ? { stage: t.stage, kind: "gate", active: [], caption: `${t.title} gate: ${t.gate.join(", ")} approves`,
          detail: "The agents stop and wait. You read the work, then approve it or send it back with a comment.", outputs: [] }
      : { stage: t.stage, kind: "gate", active: [], caption: `${t.title} done`, detail: "No approval needed here.", outputs: [] });
  }
  out.push({ stage: "", kind: "done", active: [], caption: `${REQ} is live 🎉`,
    detail: "Every step left its evidence in the repo: specs, designs, plans, code, tests, QA and release reports.", outputs: [] });
  return out;
}

export function Demo({ team, user, onClose }: { team: TeamLead[]; user: string; onClose: () => void }) {
  const list = useMemo(() => frames(team), [team]);
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  const f = list[i];
  const stages = team.filter((t) => (t.enabled && t.agents.length) || t.status === "human");
  const at = stages.findIndex((t) => t.stage === f.stage);
  const lead = stages[at];
  const done = (s: number) => (f.kind === "done" ? true : s < at);
  const outputs = list.slice(0, i + 1).flatMap((x) => x.outputs);

  useEffect(() => {
    if (!playing) return;
    if (i >= list.length - 1) { setPlaying(false); return; }
    const t = window.setTimeout(() => setI(i + 1), f.kind === "step" ? 2600 : 2000);
    return () => window.clearTimeout(t);
  }, [i, playing, list.length, f.kind]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setI((x) => Math.min(list.length - 1, x + 1));
      if (e.key === "ArrowLeft") setI((x) => Math.max(0, x - 1));
      if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [list.length, onClose]);

  return (
    <div className="viewer-backdrop demo-backdrop" onClick={onClose}>
      <div className="demo" onClick={(e) => e.stopPropagation()}>
        <div className="demo-head">
          <b>How the team works on a task</b>
          <span className="chip">Presentation · nothing runs</span>
          <span className="spacer" />
          <button className="btn" onClick={onClose}>Close</button>
        </div>

        <div className="track">
          {stages.map((t, s) => (
            <div key={t.stage} className={`node ${s === at ? "now" : done(s) ? "done" : ""}`}>
              <Avatar seed={t.status === "human" ? user : t.lead} stage={t.stage} size={40} person={t.status === "human"}
                working={s === at && f.kind === "step"} />
              <span>{t.title}</span>
              {done(s) && <span className="tick">✓</span>}
              {t.gate.length > 0 && t.status !== "human" && s < stages.length - 1 && (
                <span className="gate-pin" title={`You review: ${t.gate.join(", ")}`}>
                  <Avatar seed={user} stage="production" size={20} person />
                </span>
              )}
            </div>
          ))}
        </div>

        <div className="stage-room" key={i}>
          <div className="room-main">
            {f.kind === "ask" || f.kind === "sort" || f.kind === "done" ? (
              <div className="room-center">
                {f.kind === "ask" ? <Avatar seed={user} stage="production" size={84} person />
                  : <span className="room-emoji">{f.kind === "sort" ? "🧭" : "🚀"}</span>}
                <div className="caption">{f.caption}</div>
                <div className={`bubble ${f.kind === "ask" ? "you" : ""}`}>{f.detail}</div>
              </div>
            ) : f.kind === "deploy" ? (
              <div className="room-center">
                <Avatar seed={user} stage="production" size={84} person />
                <div className="caption">{f.caption}</div>
                <div className="bubble you">{f.detail}</div>
              </div>
            ) : lead && (
              <>
                <div className="room-lead">
                  <Avatar seed={lead.lead} stage={lead.stage} size={64} working={f.kind === "step"} />
                  <div>
                    <div className="caption">{f.caption}</div>
                    <div className="muted small">{lead.lead} · {lead.title}</div>
                  </div>
                </div>
                {f.kind === "gate" ? (
                  <div className="gate-row">
                    <Avatar seed={user} stage="production" size={56} person />
                    <div className="bubble you">{f.detail}</div>
                    <span className="stamp">Approved ✓</span>
                  </div>
                ) : (
                  <div className="crew">
                    {lead.agents.filter((a) => !a.for.length && !a.when).map((a) => {
                      const on = f.active.includes(a.name);
                      const past = !on && list.slice(0, i).some((x) => x.active.includes(a.name));
                      return (
                        <div key={a.name} className={`crew-member ${on ? "on" : past ? "past" : ""}`}>
                          <Avatar seed={a.name} stage={lead.stage} size={on ? 56 : 40} working={on} />
                          <span className="crew-name">{agentTitle(a.name)}{past ? " ✓" : ""}</span>
                          {on && <div className="bubble">{a.does}</div>}
                        </div>
                      );
                    })}
                  </div>
                )}
                {f.kind === "intro" && <div className="muted room-purpose">{f.detail}</div>}
              </>
            )}
          </div>
          <div className="room-files">
            <div className="section-label">Written to the repo</div>
            {outputs.length === 0 ? <div className="muted small">Nothing yet</div> : outputs.slice(-9).map((o, n) => (
              <div key={`${o}-${n}`} className={`file-chip ${n === Math.min(outputs.length, 9) - 1 && f.outputs.length ? "new" : ""}`}>📄 {o}</div>
            ))}
            {outputs.length > 9 && <div className="muted small">+ {outputs.length - 9} more</div>}
          </div>
        </div>

        <div className="demo-controls">
          <button className="btn" onClick={() => { setI(0); setPlaying(true); }} title="Restart">⏮</button>
          <button className="btn" onClick={() => setI(Math.max(0, i - 1))} title="Back (←)">◀</button>
          <button className="btn primary" onClick={() => {
            if (i >= list.length - 1) setI(0);
            setPlaying(!playing || i >= list.length - 1);
          }} title="Play / pause (space)">{playing ? "❚❚ Pause" : "▶ Play"}</button>
          <button className="btn" onClick={() => setI(Math.min(list.length - 1, i + 1))} title="Next (→)">▶</button>
          <div className="progress"><div style={{ width: `${((i + 1) / list.length) * 100}%` }} /></div>
          <span className="muted small">{i + 1} / {list.length}</span>
        </div>
      </div>
    </div>
  );
}
