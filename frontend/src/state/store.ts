/** Global UI state (zustand). */
import { create } from "zustand";
import { AgentSocket, api, ModelInfo, type ApiFile, type TreeEntry } from "../api/client";

export interface Tab {
  id: string;
  path: string;
  title: string;
  kind: "file" | "diff" | "settings" | "memory";
  modified: boolean;
  readOnly: boolean;
  content: string;
  savedContent: string;
  encoding: string;
  eol: string;
  hash: string;
  pane: 0 | 1;
  diff?: string;
  language?: string;
}

export interface ToolCallCard {
  id: string;
  tool: string;
  arguments: string;
  result: string;
  durationS: number;
  live: string;
  status: "running" | "done" | "error";
}

export interface PendingAttachment {
  id: string;
  name: string;
  mime: string;
  dataUrl: string;
}

export interface ChatItem {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  tools: ToolCallCard[];
  approvals: { id: string; action: string; detail: string; answered?: string }[];
  questions: { id: string; question: string; answered?: string }[];
  streaming: boolean;
  attachments?: PendingAttachment[];
}

export interface TodoItem {
  id: string;
  text: string;
  status: "pending" | "in_progress" | "done";
}

export interface AgentStatusState {
  status: string;
  tool: string;
  step: number;
  startedAt: number;
  running: boolean;
  inputTokens: number;
  outputTokens: number;
  dirty: boolean;
  stallWarning: string;
}

type SidebarView = "explorer" | "search" | "git" | "assistant";
type BottomView = "terminal" | "logs";

const langOf = (path: string): string => {
  const ext = path.split(".").pop()?.toLowerCase() || "";
  return (
    { py: "python", js: "javascript", jsx: "javascript", ts: "typescript", tsx: "typescript", json: "json", yaml: "yaml", yml: "yaml", md: "markdown", toml: "toml", sh: "shell", ps1: "shell", css: "css", html: "html", sql: "sql" }[ext] || "plaintext"
  );
};

let tabSeq = 0;

export interface Store {
  theme: "dark" | "light";
  setTheme: (t: "dark" | "light") => void;
  toggleTheme: () => void;
  followSystem: boolean;
  setFollowSystem: (v: boolean) => void;

  workspace: string;
  version: string;
  initialized: boolean;
  init: () => Promise<void>;
  openFolder: (path: string) => Promise<string | undefined>;
  folderPickerOpen: boolean;
  openFolderPicker: () => void;
  closeFolderPicker: () => void;

  sidebarView: SidebarView;
  setSidebarView: (v: SidebarView) => void;
  sidebarVisible: boolean;
  toggleSidebar: () => void;
  bottomVisible: boolean;
  toggleBottom: () => void;
  bottomView: BottomView;
  setBottomView: (v: BottomView) => void;
  chatVisible: boolean;
  toggleChat: () => void;
  zoom: number;
  setZoom: (z: number) => void;

  tree: TreeEntry[];
  treePath: string;
  loadTree: (path?: string) => Promise<void>;

  tabs: Tab[];
  activeTabId: string | null;
  split: boolean;
  toggleSplit: () => void;
  openFile: (path: string) => Promise<void>;
  openDiff: (path: string, diff: string) => void;
  openSettings: () => void;
  openMemory: () => void;
  closeTab: (id: string) => void;
  setActiveTab: (id: string) => void;
  moveTab: (id: string, delta: number) => void;
  setTabContent: (id: string, content: string) => void;
  saveTab: (id: string) => Promise<string | null>;
  activeTab: () => Tab | null;

  searchResults: { path: string; matches: { line: number; text: string }[] }[];
  searchQuery: string;
  runSearch: (q: string, glob: string) => Promise<void>;

  gitFiles: { status: string; path: string }[];
  gitBranch: string;
  loadGit: () => Promise<void>;

  models: Record<string, ModelInfo[]>;
  modelDefault: string;
  modelSelected: string;
  setModel: (ref: string) => void;
  agentMode: "Chat" | "Agent" | "Plan";
  setAgentMode: (m: "Chat" | "Agent" | "Plan") => void;
  permissionMode: "ask" | "edits" | "autonomous";
  setPermissionMode: (m: "ask" | "edits" | "autonomous") => void;

  chat: ChatItem[];
  todos: TodoItem[];
  sessions: any[];
  activeSessionId: string;
  agentStatus: AgentStatusState;
  socket: AgentSocket | null;
  connectAgent: () => void;
  sendPrompt: (text: string) => void;
  pendingAttachments: PendingAttachment[];
  addPendingAttachment: (a: PendingAttachment) => void;
  removePendingAttachment: (id: string) => void;
  clearPendingAttachments: () => void;
  stopAgent: () => void;
  replyTo: (id: string, value: string) => void;
  loadSessions: () => Promise<void>;
  newSession: () => void;

  logs: any[];
  loadLogs: (level?: string, q?: string) => Promise<void>;

  paletteOpen: boolean;
  paletteMode: "commands" | "files";
  openPalette: (mode: "commands" | "files") => void;
  closePalette: () => void;

  onboarding: boolean;
  dismissOnboarding: () => void;
  toast: string;
  setToast: (t: string) => void;
}

const defaultStatus: AgentStatusState = {
  status: "idle",
  tool: "",
  step: 0,
  startedAt: 0,
  running: false,
  inputTokens: 0,
  outputTokens: 0,
  dirty: false,
  stallWarning: "",
};

const THEME_KEY = "wotan.theme";
const MODEL_KEY = "wotan.model";
const PERMISSION_KEY = "wotan.permissionMode";

export const useStore = create<Store>((set, get) => ({
  theme: (localStorage.getItem(THEME_KEY) as "dark" | "light") || "dark",
  setTheme: (t) => {
    localStorage.setItem(THEME_KEY, t);
    document.documentElement.dataset.theme = t;
    set({ theme: t });
  },
  toggleTheme: () => get().setTheme(get().theme === "dark" ? "light" : "dark"),
  followSystem: localStorage.getItem(THEME_KEY) === null,
  setFollowSystem: (v) => set({ followSystem: v }),

  workspace: "",
  version: "",
  initialized: false,
  init: async () => {
    const health = await api.health();
    const models = await api.models();
    document.documentElement.dataset.theme = get().theme;
    const validModes = ["ask", "edits", "autonomous"];
    const storedMode = localStorage.getItem(PERMISSION_KEY);
    const permissionMode =
      (validModes.includes(storedMode || "") ? storedMode : null) ||
      (validModes.includes(health.default_permission_mode || "") ? health.default_permission_mode : null) ||
      "ask";
    set({
      workspace: health.workspace,
      version: health.version,
      initialized: true,
      models: models.groups,
      modelDefault: models.default,
      modelSelected: localStorage.getItem(MODEL_KEY) || models.default,
      permissionMode: permissionMode as "ask" | "edits" | "autonomous",
    });
    await Promise.all([get().loadTree(), get().loadGit(), get().loadSessions()]);
    if (!localStorage.getItem("wotan.onboarded")) set({ onboarding: true });
  },
  openFolder: async (path: string) => {
    const res = await api.switchWorkspace(path);
    if (!res.ok) return res.error || "failed to open folder";
    // The workspace, config, terminals and agent session are all pinned to a
    // single folder server-side - a full reload is the simplest way to get a
    // clean state for the new one (file tree, open tabs, chat, WS session).
    window.location.reload();
    return undefined;
  },
  folderPickerOpen: false,
  openFolderPicker: () => set({ folderPickerOpen: true }),
  closeFolderPicker: () => set({ folderPickerOpen: false }),

  sidebarView: "explorer",
  setSidebarView: (v) => set((s) => ({ sidebarView: v, sidebarVisible: s.sidebarView !== v ? true : s.sidebarVisible })),
  sidebarVisible: true,
  toggleSidebar: () => set((s) => ({ sidebarVisible: !s.sidebarVisible })),
  bottomVisible: false,
  toggleBottom: () => set((s) => ({ bottomVisible: !s.bottomVisible })),
  bottomView: "terminal",
  setBottomView: (v) => set({ bottomView: v, bottomVisible: true }),
  chatVisible: true,
  toggleChat: () => set((s) => ({ chatVisible: !s.chatVisible })),
  zoom: 1,
  setZoom: (z) => {
    document.documentElement.style.fontSize = `${Math.round(13 * z)}px`;
    set({ zoom: z });
  },

  tree: [],
  treePath: "",
  loadTree: async (path = "") => {
    const res = await api.tree(path);
    set({ tree: res.entries, treePath: res.path });
  },

  tabs: [],
  activeTabId: null,
  split: false,
  toggleSplit: () => set((s) => ({ split: !s.split })),
  openFile: async (path: string) => {
    const existing = get().tabs.find((t) => t.path === path && t.kind === "file");
    if (existing) {
      set({ activeTabId: existing.id });
      return;
    }
    const file = await api.readFile(path);
    if (file.binary) {
      get().setToast(`Binary file (${file.path}) cannot be opened in the editor`);
      return;
    }
    const pane: 0 | 1 = get().split ? (get().tabs.filter((t) => t.pane === 0).length <= get().tabs.filter((t) => t.pane === 1).length ? 0 : 1) : 0;
    const tab: Tab = {
      id: `tab-${++tabSeq}`,
      path,
      title: path.split("/").pop() || path,
      kind: "file",
      modified: false,
      readOnly: !!file.read_only,
      content: file.content || "",
      savedContent: file.content || "",
      encoding: file.encoding || "utf-8",
      eol: file.eol || "lf",
      hash: "",
      pane,
      language: langOf(path),
    };
    set((s) => ({ tabs: [...s.tabs, tab], activeTabId: tab.id }));
  },
  openDiff: (path: string, diff: string) => {
    const id = `tab-${++tabSeq}`;
    const tab: Tab = {
      id,
      path,
      title: `Diff: ${path.split("/").pop() || path}`,
      kind: "diff",
      modified: false,
      readOnly: true,
      content: "",
      savedContent: "",
      encoding: "utf-8",
      eol: "lf",
      hash: "",
      pane: 0,
      diff,
    };
    set((s) => ({ tabs: [...s.tabs, tab], activeTabId: id }));
  },
  openSettings: () => {
    const id = `tab-${++tabSeq}`;
    const tab: Tab = {
      id,
      path: "wotan:settings",
      title: "Settings",
      kind: "settings",
      modified: false,
      readOnly: false,
      content: "",
      savedContent: "",
      encoding: "utf-8",
      eol: "lf",
      hash: "",
      pane: 0,
    };
    set((s) => ({ tabs: [...s.tabs, tab], activeTabId: id }));
  },
  openMemory: () => {
    const id = `tab-${++tabSeq}`;
    const tab: Tab = {
      id,
      path: "wotan:memory",
      title: "Memory",
      kind: "memory",
      modified: false,
      readOnly: false,
      content: "",
      savedContent: "",
      encoding: "utf-8",
      eol: "lf",
      hash: "",
      pane: 0,
    };
    set((s) => ({ tabs: [...s.tabs, tab], activeTabId: id }));
  },
  closeTab: (id) =>
    set((s) => {
      const tabs = s.tabs.filter((t) => t.id !== id);
      const activeTabId = s.activeTabId === id ? tabs[tabs.length - 1]?.id || null : s.activeTabId;
      return { tabs, activeTabId };
    }),
  setActiveTab: (id) => set({ activeTabId: id }),
  moveTab: (id, delta) =>
    set((s) => {
      const idx = s.tabs.findIndex((t) => t.id === id);
      const target = idx + delta;
      if (idx < 0 || target < 0 || target >= s.tabs.length) return s;
      const tabs = [...s.tabs];
      const [t] = tabs.splice(idx, 1);
      tabs.splice(target, 0, t);
      return { tabs };
    }),
  setTabContent: (id, content) =>
    set((s) => ({
      tabs: s.tabs.map((t) => (t.id === id ? { ...t, content, modified: content !== t.savedContent } : t)),
    })),
  saveTab: async (id) => {
    const tab = get().tabs.find((t) => t.id === id);
    if (!tab || tab.kind !== "file") return null;
    const res = await api.saveFile(tab.path, tab.content, tab.hash || undefined);
    if (res.error) return res.error;
    set((s) => ({
      tabs: s.tabs.map((t) => (t.id === id ? { ...t, savedContent: t.content, modified: false, hash: res.hash || "" } : t)),
    }));
    return null;
  },
  activeTab: () => get().tabs.find((t) => t.id === get().activeTabId) || null,

  searchResults: [],
  searchQuery: "",
  runSearch: async (q, glob) => {
    set({ searchQuery: q });
    if (!q) return set({ searchResults: [] });
    const res = await api.search(q, glob);
    set({ searchResults: res.files });
  },

  gitFiles: [],
  gitBranch: "",
  loadGit: async () => {
    const res = await api.gitStatus();
    set({ gitFiles: res.files || [], gitBranch: res.branch || "" });
  },

  models: {},
  modelDefault: "",
  modelSelected: "",
  setModel: (ref) => {
    localStorage.setItem(MODEL_KEY, ref);
    set({ modelSelected: ref });
  },
  agentMode: "Agent",
  setAgentMode: (m) => set({ agentMode: m }),
  permissionMode: "ask",
  setPermissionMode: (m) => {
    localStorage.setItem(PERMISSION_KEY, m);
    set({ permissionMode: m });
  },

  chat: [],
  todos: [],
  sessions: [],
  activeSessionId: "",
  agentStatus: { ...defaultStatus },
  socket: null,
  connectAgent: () => {
    if (get().socket) return;
    const socket = new AgentSocket();
    socket.onEvent = (ev) => {
      const s = get();
      const chat = [...s.chat];
      const last = () => chat[chat.length - 1];
      switch (ev.type) {
        case "ready":
          set({ activeSessionId: ev.session_id });
          if (ev.resumed) {
            const terminal = ["idle", "done", "error", "stopped"].includes(ev.status || "");
            set({
              agentStatus: { ...s.agentStatus, running: !terminal, status: ev.status || s.agentStatus.status, stallWarning: "" },
            });
            s.setToast(terminal ? "Reconnected - the agent had finished while you were disconnected" : "Reconnected - the agent kept running in the background");
          }
          break;
        case "status": {
          const st = { ...s.agentStatus };
          st.status = ev.status || st.status;
          if (ev.tool) st.tool = ev.tool;
          if (ev.step) st.step = ev.step;
          set({ agentStatus: st });
          break;
        }
        case "token": {
          if (!chat.length || !last().streaming || last().role !== "assistant") {
            chat.push({ id: `a-${Date.now()}`, role: "assistant", text: "", tools: [], approvals: [], questions: [], streaming: true });
          }
          last().text += ev.text || "";
          set({ chat });
          break;
        }
        case "tool_start": {
          if (!chat.length || !last().streaming) {
            chat.push({ id: `a-${Date.now()}`, role: "assistant", text: "", tools: [], approvals: [], questions: [], streaming: true });
          }
          last().tools.push({
            id: ev.call_id,
            tool: ev.tool,
            arguments: ev.arguments || "",
            result: "",
            durationS: 0,
            live: "",
            status: "running",
          });
          set({ chat });
          break;
        }
        case "tool_output_live": {
          for (const item of chat) {
            for (const tc of item.tools) {
              if (ev.run_id && tc.result.includes(ev.run_id)) tc.live += ev.chunk || "";
            }
          }
          set({ chat });
          break;
        }
        case "tool_end": {
          for (const item of chat) {
            for (const tc of item.tools) {
              if (tc.id === ev.call_id) {
                tc.result = ev.result || "";
                tc.durationS = ev.duration_s || 0;
                tc.status = (ev.result || "").includes('"status": "error"') || (ev.result || "").includes('"status":"error"') ? "error" : "done";
              }
            }
          }
          set({ chat });
          break;
        }
        case "approval": {
          if (!chat.length || !last().streaming) {
            chat.push({ id: `a-${Date.now()}`, role: "assistant", text: "", tools: [], approvals: [], questions: [], streaming: true });
          }
          last().approvals.push({ id: ev.id, action: ev.action, detail: ev.detail || "" });
          set({ chat, agentStatus: { ...s.agentStatus, status: "waiting for your approval" } });
          break;
        }
        case "ask_user": {
          if (!chat.length || !last().streaming) {
            chat.push({ id: `a-${Date.now()}`, role: "assistant", text: "", tools: [], approvals: [], questions: [], streaming: true });
          }
          last().questions.push({ id: ev.id, question: ev.question || "" });
          set({ chat, agentStatus: { ...s.agentStatus, status: "waiting for your answer" } });
          break;
        }
        case "usage":
          set({ agentStatus: { ...s.agentStatus, inputTokens: ev.input_tokens, outputTokens: ev.output_tokens, step: ev.step || s.agentStatus.step } });
          break;
        case "dirty":
          set({ agentStatus: { ...s.agentStatus, dirty: !!ev.dirty } });
          break;
        case "todos":
          set({ todos: ev.todos || [] });
          break;
        case "finish_verdict": {
          chat.push({
            id: `v-${Date.now()}`,
            role: "system",
            text: ev.finished
              ? `[OK] Completion accepted by the verification gate. ${ev.summary || ""}`
              : `[ERROR] Completion refused by the verification gate.\n${ev.error?.why || ""}`,
            tools: [],
            approvals: [],
            questions: [],
            streaming: false,
          });
          set({ chat });
          break;
        }
        case "warning": {
          chat.push({ id: `w-${Date.now()}`, role: "system", text: ev.message || "", tools: [], approvals: [], questions: [], streaming: false });
          set({ chat });
          break;
        }
        case "error": {
          chat.push({ id: `e-${Date.now()}`, role: "system", text: `[ERROR] ${ev.message || "unknown error"}`, tools: [], approvals: [], questions: [], streaming: false });
          set({ chat, agentStatus: { ...s.agentStatus, status: "error" } });
          break;
        }
        case "compacted":
          chat.push({ id: `c-${Date.now()}`, role: "system", text: `[compacting context] ${ev.summary || ""}`, tools: [], approvals: [], questions: [], streaming: false });
          set({ chat });
          break;
        case "escalate":
          chat.push({ id: `x-${Date.now()}`, role: "system", text: `[escalated to ${ev.model}] ${ev.message || ""}`, tools: [], approvals: [], questions: [], streaming: false });
          set({ chat, modelSelected: ev.model });
          break;
        case "done": {
          for (const item of chat) item.streaming = false;
          set({
            chat,
            agentStatus: {
              ...s.agentStatus,
              running: false,
              status: ev.result?.status || "done",
              inputTokens: ev.usage?.input_tokens ?? s.agentStatus.inputTokens,
              outputTokens: ev.usage?.output_tokens ?? s.agentStatus.outputTokens,
              stallWarning: "",
            },
          });
          void get().loadGit();
          void get().loadTree(get().treePath);
          break;
        }
        case "heartbeat":
          set({ agentStatus: { ...s.agentStatus, stallWarning: "" } });
          break;
      }
    };
    socket.connect();
    set({ socket });
  },
  sendPrompt: (text: string) => {
    const s = get();
    if (!s.socket) s.connectAgent();
    const attachments = s.pendingAttachments;
    const chat = [
      ...s.chat,
      { id: `u-${Date.now()}`, role: "user" as const, text, tools: [], approvals: [], questions: [], streaming: false, attachments },
    ];
    set({
      chat,
      pendingAttachments: [],
      agentStatus: {
        ...s.agentStatus,
        running: true,
        status: "thinking",
        startedAt: s.agentStatus.running ? s.agentStatus.startedAt : Date.now(),
        step: 0,
        stallWarning: "",
      },
    });
    s.socket?.run(
      text,
      s.modelSelected || s.modelDefault,
      s.agentMode,
      s.permissionMode,
      attachments.map((a) => ({ kind: "image", mime: a.mime, data_b64: a.dataUrl.split(",")[1] || "", name: a.name })),
    );
  },
  pendingAttachments: [],
  addPendingAttachment: (a) => set((s) => ({ pendingAttachments: [...s.pendingAttachments, a] })),
  removePendingAttachment: (id) => set((s) => ({ pendingAttachments: s.pendingAttachments.filter((a) => a.id !== id) })),
  clearPendingAttachments: () => set({ pendingAttachments: [] }),
  stopAgent: () => {
    get().socket?.stop();
    set({ agentStatus: { ...get().agentStatus, running: false, status: "stopped" } });
  },
  replyTo: (id, value) => {
    get().socket?.reply(id, value);
    const chat = get().chat.map((item) => ({
      ...item,
      approvals: item.approvals.map((a) => (a.id === id ? { ...a, answered: value } : a)),
      questions: item.questions.map((q) => (q.id === id ? { ...q, answered: value } : q)),
    }));
    set({ chat });
  },
  loadSessions: async () => {
    const res = await api.sessions();
    set({ sessions: res.sessions || [] });
  },
  newSession: () => set({ chat: [], todos: [], agentStatus: { ...defaultStatus } }),

  logs: [],
  loadLogs: async (level = "", q = "") => {
    const res = await api.logs(level, q);
    set({ logs: res.entries || [] });
  },

  paletteOpen: false,
  paletteMode: "commands",
  openPalette: (mode) => set({ paletteOpen: true, paletteMode: mode }),
  closePalette: () => set({ paletteOpen: false }),

  onboarding: false,
  dismissOnboarding: () => {
    localStorage.setItem("wotan.onboarded", "1");
    set({ onboarding: false });
  },
  toast: "",
  setToast: (t) => {
    set({ toast: t });
    if (t) setTimeout(() => set({ toast: "" }), 4000);
  },
}));

