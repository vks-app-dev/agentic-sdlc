import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Item, type LiveAgent, type Metrics, type Project, type SdlcEvent } from "./api";
import { Activity } from "./components/Activity";
import { FileViewer } from "./components/FileViewer";
import { Home } from "./components/Home";
import { Library } from "./components/Library";
import { Inbox } from "./components/Inbox";
import { ItemDetail } from "./components/ItemDetail";
import { Kpis } from "./components/Kpis";
import { LiveAgents } from "./components/LiveAgents";
import { UsagePanel } from "./components/UsagePanel";
import { Pipeline } from "./components/Pipeline";

type View = "home" | "board" | "library";

const hashId = () => (location.hash.match(/REQ-\d+/) ?? [null])[0];

export default function App() {
  const [project, setProject] = useState<Project | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [events, setEvents] = useState<SdlcEvent[]>([]);
  const [live, setLive] = useState<LiveAgent[]>([]);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [selected, setSelected] = useState<string | null>(hashId());
  const [file, setFile] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [connected, setConnected] = useState(false);
  const [version, setVersion] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [error, setError] = useState("");
  const [view, setView] = useState<View>(
    hashId() || location.hash === "#board" ? "board" : location.hash === "#library" ? "library" : "home");
  const timer = useRef<number>();

  const refresh = useCallback(() => {
    Promise.all([api.items(), api.live(), api.metrics()])
      .then(([i, l, m]) => {
        setItems(i);
        setLive(l);
        setMetrics(m);
        setVersion((v) => v + 1);
        setError("");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Coalesce bursts of events (an agent editing many files) into one refresh.
  const soon = useCallback(() => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(refresh, 300);
  }, [refresh]);

  useEffect(() => {
    api.project().then(setProject, (e: Error) => setError(e.message));
    api.events(500).then(setEvents, () => undefined);
    refresh();
    const es = new EventSource("/api/stream");
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = (m) => {
      const ev = JSON.parse(m.data) as SdlcEvent;
      if (!ev.event) return;
      setEvents((prev) => [...prev.slice(-999), ev]);
      soon();
    };
    const poll = window.setInterval(refresh, 15000);
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => {
      es.close();
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, [refresh, soon]);

  const select = (id: string | null) => {
    setSelected(id);
    history.replaceState(null, "", id ? `#${id}` : view === "board" ? "#board" : location.pathname);
  };

  const show = (v: View) => {
    setView(v);
    history.replaceState(null, "", v === "board" ? (selected ? `#${selected}` : "#board")
      : v === "library" ? "#library" : location.pathname);
  };

  const openItem = (id: string) => {
    setView("board");
    setSelected(id);
    history.replaceState(null, "", `#${id}`);
    refresh();
    // bring the item (and its live agent run) into view once the board has rendered it
    window.setTimeout(() => document.getElementById("item-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 350);
  };

  const stack = project ? [...project.stack.languages, project.stack.framework].filter(Boolean).join(" · ") : "";

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          Agentic SDLC <span>/ {project?.project.name ?? "…"}</span>
        </div>
        {project && <span className="chip">{project.project.type === "existing" ? "Existing code" : "New code"}</span>}
        {stack && <span className="chip">{stack}</span>}
        <span className="spacer" />
        <nav className="tabs">
          <button className={view === "home" ? "on" : ""} onClick={() => show("home")}>Home</button>
          <button className={view === "board" ? "on" : ""} onClick={() => show("board")}>Board</button>
          <button className={view === "library" ? "on" : ""} onClick={() => show("library")}>Library</button>
        </nav>
        {error && <span className="error">{error}</span>}
        <span className="chip" title={connected ? "Receiving live events" : "Reconnecting to the event stream…"}>
          <span className={`live-dot ${connected ? "on" : ""}`} />
          {connected ? "Live" : "Offline"}
        </span>
      </header>

      {view === "library" ? <Library openFile={setFile} /> : view === "home" ? <Home openItem={openItem} refresh={refresh} /> : <>
      <Kpis items={items} metrics={metrics} live={live} />

      <div className="grid">
        <div className="col">
          <Inbox items={items} project={project} refresh={refresh} select={select} />
          {selected && (
            <ItemDetail id={selected} project={project} version={version} openFile={setFile} select={select} refresh={refresh}
              close={() => select(null)} />
          )}
          <Pipeline items={items} project={project} metrics={metrics} selected={selected} select={select} />
        </div>
        <div className="col">
          <LiveAgents live={live} now={now} select={select} />
          <UsagePanel id={selected} version={version} />
          <Activity events={events} reqs={items.map((i) => i.id)} filter={filter} setFilter={setFilter} />
        </div>
      </div>
      </>}

      {file && <FileViewer path={file} onClose={() => setFile(null)} />}
    </div>
  );
}
