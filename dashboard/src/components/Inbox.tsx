import { useEffect, useState } from "react";
import { api, fixApi, since, stageTitle, typeOf, type Blocker, type Item, type PendingRequest, type Project, type WorkTypeName } from "../api";
import { TypeBadge } from "./Status";

function GateCard({ item, stageTitle, onDone, onOpen }: { item: Item; stageTitle: string; onDone: () => void; onOpen: () => void }) {
  const st = item.stages[item.current_stage!];
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function decide(kind: "approve" | "reject" | "reject_run") {
    if (kind !== "approve" && !comment.trim()) {
      setErr("Say what the agents should change before sending it back.");
      return;
    }
    setBusy(true);
    setErr("");
    try {
      await (kind === "approve" ? api.approve(item.id, comment) : api.reject(item.id, comment));
      if (kind === "reject_run") {
        await api.run(item.id);
        onOpen();
      }
      setComment("");
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <div className="meta">
        <TypeBadge type={typeOf(item)} /> {stageTitle} gate · <span className="mono">{item.id}</span> · approvers: {st.gate.join(", ")} · waiting{" "}
        {since(st.awaiting_since)}
      </div>
      <div className="title">{item.title}</div>
      {st.summary && <div className="summary">{st.summary}</div>}
      <div className="actions" style={{ marginTop: 0, marginBottom: 8 }}>
        <button className="link" onClick={onOpen}>
          Review {st.artifacts.length} file{st.artifacts.length === 1 ? "" : "s"} →
        </button>
      </div>
      <textarea
        placeholder="Comment (required to send back): what should change?"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
      />
      <div className="actions">
        <button className="btn primary" disabled={busy} onClick={() => decide("approve")}>
          Approve
        </button>
        <button className="btn danger" disabled={busy} onClick={() => decide("reject")}>
          Send back
        </button>
        <button className="btn danger" disabled={busy} onClick={() => decide("reject_run")}
          title="Send back with your comment and start the agents right away">
          Send back &amp; run agents
        </button>
        {err && <span className="error">{err}</span>}
      </div>
    </div>
  );
}

export function Inbox({ items, project, refresh, select }: { items: Item[]; project: Project | null; refresh: () => void; select: (id: string) => void }) {
  const waiting = items.filter((i) => i.current_stage && i.stages[i.current_stage].status === "awaiting_approval");
  const blocked = items.flatMap((i) => i.tasks.filter((t) => t.status === "blocked").map((t) => ({ item: i, task: t })));
  const [pending, setPending] = useState<PendingRequest[]>([]);
  const [held, setHeld] = useState<{ req: string; title: string; type: WorkTypeName; blockers: Blocker[] }[]>([]);
  const [pErr, setPErr] = useState("");
  useEffect(() => {
    api.pending().then(setPending, () => undefined);
    fixApi.all().then(setHeld, () => undefined);
  }, [items]);
  const decide = async (p: PendingRequest, ok: boolean) => {
    try {
      await (ok ? api.confirm(p.id) : api.dismiss(p.id));
      setPErr("");
      setPending((xs) => xs.filter((x) => x.id !== p.id));
      refresh();
    } catch (e) {
      setPErr((e as Error).message);
    }
  };
  const total = waiting.length + blocked.length + pending.length + held.length;

  return (
    <section className="panel">
      <h2>
        Waiting for you <span className="count">{total}</span>
      </h2>
      {total === 0 ? (
        <div className="empty">Nothing needs a decision. Agents are working or idle.</div>
      ) : (
        <div className="body">
          {pending.map((p) => (
            <div className="card" key={p.id}>
              <div className="meta">
                Confirm a request · <span className="mono">{p.req}</span> · from {p.from} · {since(p.ts)} ago
              </div>
              <div className="title">{p.summary}</div>
              <div className="summary">Only a person can do this. Nothing has changed yet.</div>
              <div className="actions">
                <button className="btn primary" onClick={() => decide(p, true)}>Confirm</button>
                <button className="btn" onClick={() => decide(p, false)}>Dismiss</button>
                {pErr && <span className="error">{pErr}</span>}
              </div>
            </div>
          ))}
          {held.map((h) => (
            <div className="card held" key={h.req}>
              <div className="meta">
                <TypeBadge type={h.type} />{" "}
                {h.blockers.some((b) => b.blocking !== false) ? "Before agents can start" : "The agents asked for help"}
                {" "}· <span className="mono">{h.req}</span> · {h.blockers.length} thing{h.blockers.length > 1 ? "s" : ""} need you
              </div>
              <div className="title">{h.title}</div>
              <ul className="held-list">
                {h.blockers.map((b) => <li key={b.code}>{b.title}</li>)}
              </ul>
              <div className="actions">
                <button className="btn primary" onClick={() => {
                  select(h.req);
                  window.setTimeout(() => document.querySelector(".needs-you")?.scrollIntoView({ behavior: "smooth", block: "center" }), 350);
                }}>Fix them →</button>
              </div>
            </div>
          ))}
          {waiting.map((i) => (
            <GateCard key={i.id} item={i} stageTitle={stageTitle(project, i, i.current_stage!)} onDone={refresh}
              onOpen={() => {
                select(i.id);
                // open the item and take the person to the files waiting for review
                window.setTimeout(() => {
                  const el = document.getElementById("item-files");
                  if (!el) return;
                  el.scrollIntoView({ behavior: "smooth", block: "center" });
                  el.classList.remove("flash");
                  void el.offsetWidth;
                  el.classList.add("flash");
                }, 400);
              }} />
          ))}
          {blocked.map(({ item, task }) => (
            <div className="card blocked" key={`${item.id}/${task.id}`}>
              <div className="meta">
                <TypeBadge type={typeOf(item)} /> Blocked after {task.attempts} attempts · <span className="mono">{item.id}/{task.id}</span>
              </div>
              <div className="title">{task.title}</div>
              <div className="summary">{task.log.filter((l) => l.note).at(-1)?.note ?? "See the review file for details."}</div>
              <div className="actions">
                <button className="btn" onClick={() => select(item.id)}>Open</button>
                <button className="btn" onClick={() => api.unblock(item.id, task.id).then(refresh)}>
                  Unblock and retry
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
