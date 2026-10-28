// Types mirror schema/state.schema.json and schema/event.schema.json.

export type StageStatus = "pending" | "in_progress" | "awaiting_approval" | "approved" | "skipped" | "blocked";
export type TaskStatus = "pending" | "running" | "in_review" | "done" | "failed" | "blocked";

export interface StageState {
  status: StageStatus;
  gate: string[];
  artifacts: string[];
  feedback: { by: string; comment: string; at: string }[];
  summary?: string;
  started?: string;
  ended?: string;
  awaiting_since?: string;
  decision?: { result: string; by: string; comment: string; at: string };
}

export interface Task {
  id: string;
  title: string;
  deps: string[];
  files: string[];
  acceptance: string[];
  status: TaskStatus;
  attempts: number;
  branch: string;
  log: { ts: string; action: string; note: string }[];
}

export type WorkTypeName = "feature" | "change" | "bug" | "task";

export interface Link {
  rel: "updates" | "fixes" | "blocked_by";
  req: string;
}

export interface Defect {
  id: string;
  title: string;
  severity: "critical" | "high" | "medium" | "low";
  blocker: boolean;
  status: string;
  task: string | null;
  found_by: string;
  duplicate_of?: string;
  log: { ts: string; action: string; note: string; by: string }[];
}

export interface Item {
  id: string;
  title: string;
  type?: WorkTypeName; // missing on items made before work types: a feature
  links?: Link[];
  size?: string | null;
  defects?: Defect[];
  created: string;
  updated: string;
  status: "active" | "blocked" | "done";
  current_stage: string | null;
  stages: Record<string, StageState>;
  tasks: Task[];
  history: { ts: string; what: string; [k: string]: unknown }[];
  deployments?: { env: string; ok: boolean; version?: string; ts: string; by: string }[];
}

export interface Run {
  id: string;
  req: string;
  command: string;
  log: string;
  started: string;
  pid: number | null;
  status: "running" | "finished" | "failed" | "stopped";
  exit_code: number | null;
  ended: string | null;
  error?: string | null;
  summary?: string | null;
  cost_usd?: number | null;
}

export interface FlowStage {
  stage: string;
  title: string;
  status: StageStatus;
  lead: string;
  human: boolean;
  automated: boolean;
  gate: string[];
  decided_by?: string | null;
  agents: { name: string; step: number; status: "done" | "working" | "waiting"; runs: number }[];
  extra: { name: string; status: "done" | "working" | "waiting"; runs: number }[];
}

export interface GoLive {
  state: "ready" | "live";
  env: string;
  version: string;
  pr?: { number: number; url: string; draft?: boolean; label?: string } | null;
  deploy_command?: string | null;
  url?: string | null;
  deployed?: { version?: string; by: string; ts: string } | null;
}

export interface ItemDetail extends Item {
  stage_titles: Record<string, string>;
  can_retype: boolean;
  checks?: { ts: string; head: string; passed: boolean; results: { name: string; status: string }[] };
  pr?: { number: number; url: string; head: string; updated: string; draft?: boolean; label?: string;
         statuses?: Record<string, string> } | null;
  deployments?: { env: string; commit: string; version?: string; ts: string; by: string; ok: boolean }[];
  smoke?: { env: string; ok: boolean; ts: string }[];
  reviews: Record<string, string>;
  run: Run | null;
  user?: string;
  flow?: FlowStage[];
  last_run?: Run | null;
  agents_active?: boolean; // agents at work, started here or elsewhere (e.g. from a terminal)
  latest_run?: Run | null;
  go_live?: GoLive | null;
  blockers?: Blocker[];
  next: { action: string; message?: string; stage?: string; steps?: { agent: string; does: string; output: string }[] };
}

export interface StageInfo {
  name: string;
  title: string;
  purpose: string;
  automated: boolean;
  agents: string[];
  enabled?: boolean;
  autonomy?: string;
  gate?: string[];
  parallel_coders?: number;
}

export interface WorkType {
  name: WorkTypeName;
  title: string;
  purpose: string;
  stages: string[];
  stage_titles: Record<string, string>;
  design_if_size: string[];
  agents: Record<string, string[]>;
}

export interface Project {
  root: string;
  project: { name: string; type: string; compliance: string[] };
  stack: { languages: string[]; framework: string | null; commands: Record<string, string> };
  stages: StageInfo[];
  work_types: WorkType[];
}

export interface SdlcEvent {
  ts: string;
  source: "engine" | "hook" | "agent";
  event: string;
  req?: string;
  stage?: string;
  task?: string;
  agent?: string;
  agent_id?: string;
  status?: string;
  detail?: string;
  tool?: string;
  by?: string;
}

export interface LiveAgent {
  agent_id: string;
  agent: string;
  req?: string;
  task?: string;
  started: string;
  last: string | null;
  last_ts: string;
  actions: number;
}

export interface Metrics {
  items: { total: number; active: number; blocked: number; done: number };
  gates: { approved: number; rejected: number; rework_rate: number | null; avg_wait_min: number | null };
  tasks: { total: number; done: number; first_try: number; blocked: number };
  agents: { runs: number; tool_calls: number };
  stage_minutes: { stage: string; title: string; avg: number | null; count: number }[];
  by_type: { type: WorkTypeName; title: string; total: number; open: number; done: number; avg_cycle_h: number | null }[];
  ai: Usage;
}

export interface UsageRow {
  name: string;
  tokens: number;
  cost_usd: number;
  unpriced_tokens: number;
}

export interface Usage {
  total: UsageRow;
  runs_actual_usd: number;
  by_item: UsageRow[];
  by_stage: UsageRow[];
  by_agent: UsageRow[];
  by_model: UsageRow[];
  note: string;
}

export const tokens = (n: number) =>
  n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1000 ? `${Math.round(n / 1000)}K` : String(n);
export const usd = (n: number) => (n >= 100 ? `$${Math.round(n)}` : `$${n.toFixed(2)}`);

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail ?? msg;
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return r.json();
}

// The server puts a per-launch token in the page; human actions must carry it (agents calling the API cannot).
const humanToken = () => document.querySelector<HTMLMetaElement>('meta[name="sdlc-human"]')?.content ?? "";

const post = (path: string, body: unknown) =>
  req<{ ok: boolean; next_stage?: string | null; message?: string }>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-SDLC-Human": humanToken() },
    body: JSON.stringify(body),
  });

export interface PendingRequest {
  id: string;
  action: string;
  req: string;
  summary: string;
  ts: string;
  from: string;
}

export const api = {
  project: () => req<Project>("/api/project"),
  items: () => req<Item[]>("/api/items"),
  item: (id: string) => req<ItemDetail>(`/api/items/${id}`),
  events: (limit = 200, reqId?: string) =>
    req<SdlcEvent[]>(`/api/events?limit=${limit}${reqId ? `&req=${reqId}` : ""}`),
  live: () => req<LiveAgent[]>("/api/live"),
  metrics: () => req<Metrics>("/api/metrics"),
  file: (path: string) => req<{ path: string; content: string }>(`/api/file?path=${encodeURIComponent(path)}`),
  approve: (id: string, comment: string) => post(`/api/items/${id}/approve`, { comment }),
  reject: (id: string, comment: string) => post(`/api/items/${id}/reject`, { comment }),
  decideDefect: (id: string, defect: string, action: "defer" | "accept" | "reopen", comment: string) =>
    post(`/api/items/${id}/defects/${defect}`, { action, comment }),
  retype: (id: string, type: WorkTypeName, comment: string) => post(`/api/items/${id}/retype`, { type, comment }),
  unblock: (id: string, task: string) => post(`/api/items/${id}/tasks/${task}/unblock`, {}),
  run: (id: string) => req<Run>(`/api/items/${id}/run`, { method: "POST", headers: { "X-SDLC-Human": humanToken() } }),
  pending: () => req<PendingRequest[]>("/api/pending"),
  usage: (id?: string) => req<Usage>(`/api/usage${id ? `?req=${id}` : ""}`),
  confirm: (rid: string) => post(`/api/pending/${rid}/confirm`, {}),
  dismiss: (rid: string) => post(`/api/pending/${rid}/dismiss`, {}),
  runs: () => req<Run[]>("/api/runs"),
  runLog: (runId: string) => req<{ run: Run; text: string }>(`/api/runs/${runId}/log`),
  stopRun: (runId: string) =>
    req<Run>(`/api/runs/${runId}/stop`, { method: "POST", headers: { "X-SDLC-Human": humanToken() } }),
};

export function since(ts: string | undefined, now = Date.now()): string {
  if (!ts) return "";
  const s = Math.max(0, Math.round((now - new Date(ts).getTime()) / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  return `${Math.floor(s / 86400)}d`;
}

export function clock(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export const typeOf = (i: Item): WorkTypeName => i.type ?? "feature";

/** Stage title for this item: a bug's Requirements stage is called Triage. */
export function stageTitle(project: Project | null, item: Item, stage: string): string {
  const wt = project?.work_types?.find((w) => w.name === typeOf(item));
  return wt?.stage_titles[stage] ?? project?.stages.find((s) => s.name === stage)?.title ?? stage;
}

export const shortAgent = (a?: string) => (a ?? "").replace(/^(agentic-sdlc|sdlc-pilot):/, "");

// ---- Home page: the agent team, the last unfinished job, and new requests ----

export interface TeamAgent {
  name: string;
  does: string;
  output: string;
  about: string;
  model: string;
  step: number;
  when: string;
  for: string[];
  status: "ready" | "working";
}

export interface TeamLead {
  stage: string;
  title: string;
  lead: string;
  purpose: string;
  status: "ready" | "working" | "off" | "human";
  enabled: boolean;
  gate: string[];
  agents: TeamAgent[];
}

export interface LastJob {
  req: string;
  title: string;
  type: WorkTypeName;
  updated: string;
  stage: string | null;
  stage_title: string;
  action: string;
  message: string;
  can_resume: boolean;
  running: boolean;
  turn: "agents" | "you" | "waiting";
  others: number;
}

export interface Home {
  user: string;
  project: string;
  team: TeamLead[];
  last_job: LastJob | null;
  work_types: { name: WorkTypeName; title: string; purpose: string; plan: PlanStep[] }[];
}

export interface PlanStep {
  stage: string;
  title: string;
  agents: string[];
  gate: string[];
  automated: boolean;
  note: string;
}

export interface Proposal {
  type: WorkTypeName;
  type_title: string;
  purpose: string;
  title: string;
  links: Link[];
  related: { req: string; title: string; shared: string[]; score: number }[];
  reasons: string[];
  confidence: "high" | "medium" | "low";
  plan: PlanStep[];
  plans: Record<WorkTypeName, PlanStep[]>;
  types: Record<WorkTypeName, string>;
}

export interface Started {
  blockers?: Blocker[];
  req: string;
  title: string;
  type: WorkTypeName;
  stage: string;
  spec: string;
  run: Run | null;
  run_error?: string;
}

export const homeApi = {
  home: () => req<Home>("/api/home"),
  classify: (text: string) =>
    req<Proposal>("/api/home/classify", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }),
    }),
  start: (body: { text: string; title: string; type: WorkTypeName; links: Link[]; run: boolean }) =>
    req<Started>("/api/home/start", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-SDLC-Human": humanToken() },
      body: JSON.stringify(body),
    }),
};

/** "req-ambiguity-checker" → "Ambiguity checker" */
export const agentTitle = (name: string) => {
  const s = shortAgent(name).replace(/^(req|design|plan|dev|qa|rel)-/, "").replace(/-/g, " ");
  const words = s.replace(/\b(nfr|api|ux|ui|uat|pr|qa)\b/g, (w) => w.toUpperCase());
  return words.charAt(0).toUpperCase() + words.slice(1);
};

// ---- Library: standards, skills and templates ----

export type LibSource = "default" | "company" | "edited" | "yours" | "missing";

export interface LibStandard {
  name: string;
  title: string;
  about: string;
  path: string;
  source: LibSource;
  exists: boolean;
  agents: string[];
  all?: boolean;
  kind: "standard" | "skill";
}

export interface LibTemplate {
  name: string;
  title: string;
  path: string;
  group: string;
  source: LibSource;
  exists: boolean;
  agents: string[];
}

export interface Library {
  standards: LibStandard[];
  templates: LibTemplate[];
  found: { path: string; kind: "standard" | "skill"; suggested_for: string }[];
  agents: string[];
}

export type LibKind = "standard" | "template";
interface LibChange { ok: boolean; path: string; previous?: string | null; entry: LibStandard | LibTemplate | null }
const send = <T,>(path: string, method: string, body?: unknown) =>
  req<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-SDLC-Human": humanToken() },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const libraryApi = {
  get: () => req<Library>("/api/library"),
  downloadUrl: (kind: LibKind, name: string) => `/api/library/${kind}/download?name=${encodeURIComponent(name)}`,
  replace: (kind: LibKind, name: string, content: string, filename: string) =>
    send<LibChange>(`/api/library/${kind}/${name}`, "POST", { content, filename }),
  reset: (kind: LibKind, name: string) => send<LibChange>(`/api/library/${kind}/${name}/reset`, "POST"),
  similar: (title: string, content: string) =>
    req<{ match: { name: string; title: string; reason: string } | null }>(
      `/api/library/similar?title=${encodeURIComponent(title)}`,
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }) }),
  add: (body: { name: string; title: string; agents: string[] | "all"; skill: boolean; content: string; filename: string }) =>
    send<LibChange>("/api/library/standard", "POST", body),
  remove: (name: string) => send<LibChange>(`/api/library/standard/${name}`, "DELETE"),
};

// ---- What a person must fix before agents can start ----

export interface Blocker {
  code: "spec" | "uncommitted" | "other_branch" | "stuck_worktree" | "orphan_task" | "bad_commits" | "agent_help";
  blocking?: boolean;
  steps?: string[];
  known?: boolean;
  decision?: boolean;
  title: string;
  detail: string;
  action: "edit_spec" | "commit" | "switch" | "free_worktrees" | "reset_tasks" | "fix_commits" | "read";
  sections?: string[];
  file?: string;
  files?: string[];
  target?: string;
  branch?: string;
}

export interface SpecForm {
  file: string;
  type: WorkTypeName;
  gaps: string[];
  sections: { name: string; required: boolean; kind: "text" | "checklist"; hint: string; value: string; filled: boolean }[];
}

const sendJson = <T,>(path: string, body: unknown) =>
  req<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-SDLC-Human": humanToken() },
    body: JSON.stringify(body),
  });

export const fixApi = {
  all: () => req<{ req: string; title: string; type: WorkTypeName; blockers: Blocker[] }[]>("/api/blockers"),
  spec: (id: string) => req<SpecForm>(`/api/items/${id}/spec`),
  saveSpec: (id: string, values: Record<string, string>) => sendJson<SpecForm>(`/api/items/${id}/spec`, { values }),
  suggestSpec: (id: string) =>
    sendJson<{ values: Record<string, string>; cost_usd: number | null; model: string }>(`/api/items/${id}/spec/suggest`, {}),
  commit: (files: string[], message: string, target: string) =>
    sendJson<{ branch: string; commit: string; switched: boolean; from?: string; note?: string }>(
      "/api/git/commit", { files, message, target }),
  explainHelp: (id: string) => sendJson<{ steps: string[]; cost_usd: number | null }>(`/api/items/${id}/help/explain`, {}),
  decide: (id: string, accepted: boolean, note: string) =>
    sendJson<{ ok: boolean; spec: string }>(`/api/items/${id}/decision`, { accepted, note }),
  helpHandled: (id: string) => sendJson<{ ok: boolean }>(`/api/items/${id}/help-handled`, {}),
  fixCommits: (id: string) => sendJson<{ dropped: string[]; reworded: string[] }>(`/api/items/${id}/fix-commits`, {}),
  resetTasks: (id: string) => sendJson<{ reset: string[] }>(`/api/items/${id}/reset-tasks`, {}),
  freeWorktrees: (id: string) => sendJson<{ freed: string[] }>(`/api/items/${id}/free-worktrees`, {}),
  switchTo: (target: string) => sendJson<{ branch: string; from: string }>("/api/git/switch", { target }),
  runAnyway: (id: string) =>
    req<Run>(`/api/items/${id}/run?force=true`, { method: "POST", headers: { "X-SDLC-Human": humanToken() } }),
};

// ---- Claude Code sign-in ----

export const isAuthError = (m?: string | null) =>
  !!m && /authenticat|oauth|session expired|sign-in has expired|not (logged|signed) in|invalid api key/i.test(m);

export const authApi = {
  status: () => req<{ logged_in: boolean; method?: string; email?: string; detail?: string }>("/api/claude/auth"),
  login: () => req<{ started: boolean }>("/api/claude/login", { method: "POST", headers: { "X-SDLC-Human": humanToken() } }),
};

// ---- Live control-room view of a stage ----

export interface LiveRun {
  id: string; agent: string; task?: string | null; start: string; stop: string | null; actions: number;
  last: string | null; last_ts: string; result: string | null; took: number | null;
}

export interface LiveStage {
  stage: string | null;
  stages: string[];
  title?: string;
  lead?: string;
  status?: StageStatus;
  gate?: string[];
  running?: boolean;
  orchestrator?: { status: "working" | "idle"; last: string | null; last_ts: string | null; actions: number };
  agents?: { name: string; step: number; status: "done" | "working" | "waiting"; planned: boolean; runs: LiveRun[] }[];
  handoffs?: { ts: string; kind: "dispatch" | "done" | "orchestrator" | "human" | "engine"; agent: string; text: string; took?: number | null }[];
}

export const liveApi = {
  stage: (id: string, stage?: string) =>
    req<LiveStage>(`/api/items/${id}/live${stage ? `?stage=${encodeURIComponent(stage)}` : ""}`),
};

export const dur = (s: number | null | undefined) =>
  s == null ? "" : s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
