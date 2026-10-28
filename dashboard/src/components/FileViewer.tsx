import { useEffect, useRef, useState } from "react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import { api } from "../api";

let diagramId = 0;

/** Replace ```mermaid code blocks with rendered diagrams. Mermaid is loaded only when a file has one. */
async function renderDiagrams(el: HTMLElement) {
  const blocks = [...el.querySelectorAll("pre > code.language-mermaid")];
  if (!blocks.length) return;
  const mermaid = (await import("mermaid")).default;
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral" });
  for (const code of blocks) {
    const src = code.textContent ?? "";
    if (!src.trim().split("\n").slice(1).some((l) => l.trim())) continue; // template stub: keep as code
    const box = document.createElement("div");
    box.className = "diagram";
    try {
      const { svg } = await mermaid.render(`diagram-${++diagramId}`, src);
      box.innerHTML = svg;
      code.parentElement!.replaceWith(box);
    } catch (e) {
      const note = document.createElement("div");
      note.className = "diagram-error";
      note.textContent = `Diagram could not be drawn: ${(e as Error).message.split("\n")[0]}`;
      code.parentElement!.before(note);
    }
  }
}

export function FileViewer({ path, onClose }: { path: string; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const mdRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (content != null && mdRef.current) void renderDiagrams(mdRef.current);
  }, [content]);

  useEffect(() => {
    setContent(null);
    setErr("");
    api.file(path).then((f) => setContent(f.content), (e: Error) => setErr(e.message));
  }, [path]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const isMd = path.endsWith(".md");
  return (
    <div className="viewer-backdrop" onClick={onClose}>
      <div className="viewer" onClick={(e) => e.stopPropagation()}>
        <header>
          <code>{path}</code>
          <button className="btn" onClick={() => api.file(path).then((f) => setContent(f.content))}>Reload</button>
          <button className="btn" onClick={onClose}>Close</button>
        </header>
        <div className="content">
          {err && <p className="error">{err}</p>}
          {content == null && !err && <p className="empty">Loading…</p>}
          {content != null &&
            (isMd ? (
              <div className="md" ref={mdRef} dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(marked.parse(content, { async: false })) }} />
            ) : (
              <pre className="raw">{content}</pre>
            ))}
        </div>
      </div>
    </div>
  );
}
