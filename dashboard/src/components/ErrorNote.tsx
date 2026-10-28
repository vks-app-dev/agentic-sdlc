import { useState } from "react";
import { authApi, isAuthError } from "../api";

/** An error message; when Claude Code's sign-in has expired, the way to fix it right here (sign in, then retry). */
export function ErrorNote({ message, onRetry }: { message: string; onRetry?: () => void }) {
  const [phase, setPhase] = useState<"idle" | "opened" | "checking">("idle");
  const [who, setWho] = useState("");
  const [err, setErr] = useState("");
  if (!message) return null;
  if (!isAuthError(message)) return <span className="error">{message}</span>;
  return (
    <div className="auth-note">
      <div><b>Claude Code needs you to sign in again.</b> <span className="muted small">{message}</span></div>
      <div className="auth-actions">
        <button className="btn primary" onClick={() => {
          setErr("");
          authApi.login().then(() => setPhase("opened"), (e: Error) => setErr(e.message));
        }}>🔑 Sign in again</button>
        {onRetry && (
          <button className="btn" onClick={() => {
            setPhase("checking");
            authApi.status().then((s) => {
              setWho(s.logged_in ? `Signed in${s.email ? ` as ${s.email}` : ""}` : "");
              if (s.logged_in) onRetry();
              else setErr("Still not signed in: finish signing in first");
            }, (e: Error) => setErr(e.message)).finally(() => setPhase("idle"));
          }}>{phase === "checking" ? "Checking…" : "↻ Retry"}</button>
        )}
        <span className="muted small">
          {phase === "opened" ? "A sign-in page opened in your browser: finish it there, then press Retry."
            : who || "Or in a terminal: run claude, then /login."}
        </span>
        {err && <span className="error">{err}</span>}
      </div>
    </div>
  );
}
