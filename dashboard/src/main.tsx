import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { Portfolio } from "./components/Portfolio";
import "./styles.css";

/** The same UI serves one project (`sdlc dashboard`) or many (`sdlc portfolio --serve`). */
function Root() {
  const [mode, setMode] = useState<string | null>(null);
  useEffect(() => {
    fetch("/api/mode").then((r) => r.json()).then((d) => setMode(d.mode), () => setMode("project"));
  }, []);
  if (mode === null) return null;
  return mode === "portfolio" ? <Portfolio /> : <App />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
