/** REST + WebSocket client. Same-origin relative URLs only (the backend serves
 * this bundle; previews are proxied under /preview/<name>/). */

export interface ApiFile {
  path: string;
  content?: string;
  encoding?: string;
  eol?: "lf" | "crlf" | "mixed";
  read_only?: boolean;
  protected?: boolean;
  binary?: boolean;
  hash?: string;
  lines?: number;
  error?: string;
  conflict?: boolean;
}

export interface TreeEntry {
  name: string;
  path: string;
  type: "file" | "dir";
  size: number;
}

export interface ModelInfo {
  id: string;
  ref: string;
  name: string;
  provider: string;
  provider_type: string;
  context_window: number;
  tools: boolean;
  streaming: boolean;
  weak: boolean;
  multimodal: boolean;
  edit_format: string;
  description: string;
}

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { raw: text };
  }
  if (!res.ok && !data?.error && !data?.conflict) {
    throw new Error(`${res.status}: ${text.slice(0, 200)}`);
  }
  return data as T;
}

export const api = {
  health: () => req<{ ok: boolean; version: string; workspace: string }>("/api/health"),
  tree: (path = "") => req<{ path: string; entries: TreeEntry[] }>(`/api/fs/tree?path=${encodeURIComponent(path)}`),
  readFile: (path: string) => req<ApiFile>(`/api/fs/file?path=${encodeURIComponent(path)}`),
  saveFile: (path: string, content: string, expectedHash?: string) =>
    req<ApiFile>("/api/fs/file", { method: "PUT", body: JSON.stringify({ path, content, expected_hash: expectedHash }) }),
  create: (path: string, type: "file" | "dir") =>
    req<{ ok: boolean; error?: string }>("/api/fs/create", { method: "POST", body: JSON.stringify({ path, type }) }),
  rename: (path: string, newPath: string) =>
    req<{ ok: boolean; error?: string }>("/api/fs/rename", { method: "POST", body: JSON.stringify({ path, new_path: newPath }) }),
  remove: (path: string) =>
    req<{ ok: boolean; error?: string }>("/api/fs/delete", { method: "POST", body: JSON.stringify({ path }) }),
  search: (q: string, glob = "") =>
    req<{ query: string; files: { path: string; matches: { line: number; text: string }[] }[]; count: number }>(
      `/api/search?q=${encodeURIComponent(q)}&glob=${encodeURIComponent(glob)}`,
    ),
  gitStatus: () => req<{ ok: boolean; branch: string; files: { status: string; path: string }[]; error?: string }>("/api/git/status"),
  gitDiff: (path = "", staged = false) =>
    req<{ ok: boolean; diff: string }>(`/api/git/diff?path=${encodeURIComponent(path)}&staged=${staged}`),
  gitStage: (paths: string[]) => req<{ ok: boolean; output: string }>("/api/git/stage", { method: "POST", body: JSON.stringify({ paths }) }),
  gitUnstage: (paths: string[]) => req<{ ok: boolean; output: string }>("/api/git/unstage", { method: "POST", body: JSON.stringify({ paths }) }),
  gitCommit: (message: string) => req<{ ok: boolean; output: string }>("/api/git/commit", { method: "POST", body: JSON.stringify({ message }) }),
  models: () =>
    req<{ groups: Record<string, ModelInfo[]>; default: string; roles: Record<string, string> }>("/api/models"),
  settings: () => req<{ yaml: string; path: string; summary: any }>("/api/settings"),
  saveSettings: (yaml: string) => req<{ ok: boolean; error?: string; path?: string }>("/api/settings", { method: "POST", body: JSON.stringify({ yaml }) }),
  testConnection: (providerId: string) =>
    req<{ ok: boolean; report?: any[]; rendered?: string; error?: string }>("/api/settings/test-connection", {
      method: "POST",
      body: JSON.stringify({ provider_id: providerId }),
    }),
  logs: (level = "", q = "") => req<{ entries: any[] }>(`/api/logs?level=${level}&q=${encodeURIComponent(q)}`),
  feedback: (payload: { kind: string; message: string; stack?: string }) =>
    req("/api/feedback", { method: "POST", body: JSON.stringify(payload) }),
  diagnostics: () => window.open("/api/diagnostics", "_blank"),
  checkpoints: (sessionId = "") => req<{ checkpoints: any[] }>(`/api/checkpoints?session_id=${sessionId}`),
  checkpointDiff: (id: string) => req<{ diff: string }>(`/api/checkpoints/${id}/diff`),
  checkpointUndo: (id: string) => req<{ ok: boolean; path?: string; error?: string }>(`/api/checkpoints/${id}/undo`, { method: "POST" }),
  sessions: () => req<{ sessions: any[] }>("/api/sessions"),
  sessionMessages: (id: string) => req<{ messages: any[]; todos: any[] }>(`/api/sessions/${id}/messages`),
  deleteSession: (id: string) => req(`/api/sessions/${id}`, { method: "DELETE" }),
  memory: () => req<{ files: { name: string; summary: string; content: string }[] }>("/api/memory"),
  saveMemory: (name: string, content: string) => req("/api/memory", { method: "POST", body: JSON.stringify({ name, content }) }),
  skills: () => req<{ skills: any[] }>("/api/skills"),
  inbox: () => req<{ items: any[] }>("/api/inbox"),
  inboxRead: (id: string) => req("/api/inbox/read", { method: "POST", body: JSON.stringify({ id }) }),
  scheduler: () => req<{ tasks: any[] }>("/api/scheduler"),
  addScheduled: (name: string, prompt: string, when: string) =>
    req<{ ok: boolean; task?: any; error?: string }>("/api/scheduler", { method: "POST", body: JSON.stringify({ name, prompt, when }) }),
  cancelScheduled: (id: string) => req(`/api/scheduler/${id}`, { method: "DELETE" }),
  experiments: () => req<{ experiments: any[] }>("/api/experiments"),
  runExperiment: (name: string) =>
    req<{ ok: boolean; preview?: string; port?: number; error?: string }>(`/api/experiments/${name}/run`, { method: "POST" }),
  stopExperiment: (name: string) => req(`/api/experiments/${name}/stop`, { method: "POST" }),
};

export type AgentEvent = {
  type: string;
  [key: string]: any;
};

/** Agent WebSocket with heartbeat + stall detection support. */
export class AgentSocket {
  private ws: WebSocket | null = null;
  private queue: any[] = [];
  onEvent: ((ev: AgentEvent) => void) | null = null;
  onOpen: (() => void) | null = null;
  onClose: (() => void) | null = null;
  lastEventAt = Date.now();
  sessionId = "";

  connect(): void {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    this.ws = new WebSocket(`${proto}://${location.host}/ws/agent`);
    this.ws.onopen = () => {
      this.lastEventAt = Date.now();
      this.onOpen?.();
      for (const m of this.queue.splice(0)) this.ws?.send(JSON.stringify(m));
    };
    this.ws.onmessage = (e) => {
      this.lastEventAt = Date.now();
      try {
        const ev = JSON.parse(e.data) as AgentEvent;
        if (ev.type === "ready") this.sessionId = ev.session_id;
        this.onEvent?.(ev);
      } catch {
        /* ignore malformed frames */
      }
    };
    this.ws.onclose = () => {
      this.onClose?.();
    };
  }

  send(msg: any): void {
    const frame = JSON.stringify(msg);
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(frame);
    else this.queue.push(msg);
  }

  run(text: string, model: string, mode: string, permissionMode: string): void {
    this.send({ type: "run", text, model, mode, permission_mode: permissionMode });
  }

  reply(id: string, value: string): void {
    this.send({ type: "reply", id, value });
  }

  stop(): void {
    this.send({ type: "stop" });
  }

  get stalledSeconds(): number {
    return (Date.now() - this.lastEventAt) / 1000;
  }
}

/** Terminal WebSocket factory. */
export function terminalSocket(termId: string, onOutput: (data: string) => void, onReady: (id: string, shell: string) => void) {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/terminal/${termId}`);
  ws.onmessage = (e) => {
    try {
      const m = JSON.parse(e.data);
      if (m.type === "output") onOutput(m.data);
      else if (m.type === "ready") onReady(m.id, m.shell);
    } catch {
      /* ignore */
    }
  };
  return {
    input: (data: string) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "input", data })),
    resize: (rows: number, cols: number) => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "resize", rows, cols })),
    kill: () => ws.readyState === WebSocket.OPEN && ws.send(JSON.stringify({ type: "kill" })),
    close: () => ws.close(),
  };
}
