import React, { useEffect, useRef, useState } from "react";
import { useStore, type ChatItem } from "../state/store";
import ToolCard from "./ToolCard";
import ModelPicker from "./ModelPicker";
import ProviderToggle from "./ProviderToggle";
import { renderMarkdown } from "../util/markdown";
import { api } from "../api/client";

const MODES = ["Chat", "Agent", "Plan"] as const;
const PERMS = ["ask", "edits", "autonomous"] as const;

function Markdown({ text }: { text: string }) {
  const html = renderMarkdown(text);
  return (
    <div
      className="markdown"
      dangerouslySetInnerHTML={{ __html: html }}
      onClick={(e) => {
        // Copy button on code blocks (apply is offered in the file diff flow).
        const target = e.target as HTMLElement;
        if (target.classList.contains("copy-code")) {
          const pre = target.closest(".codeblock")?.querySelector("pre code");
          if (pre) {
            void navigator.clipboard.writeText(pre.textContent || "");
            target.textContent = "copied";
            setTimeout(() => (target.textContent = "copy"), 1200);
          }
        }
      }}
    />
  );
}

function MessageView({ item }: { item: ChatItem }) {
  const replyTo = useStore((s) => s.replyTo);
  const [answer, setAnswer] = useState("");

  if (item.role === "system") {
    return (
      <div className="msg" style={{ background: "transparent", color: "var(--text-dim)", fontSize: 12, padding: "0 4px" }}>
        <Markdown text={item.text} />
      </div>
    );
  }
  return (
    <div className={`msg ${item.role}`} data-testid={`msg-${item.role}`}>
      <div className="role">
        {item.role === "user" ? "You" : "Wotan"}
        {item.streaming && <span className="badge" style={{ marginLeft: 8 }}>writing</span>}
      </div>
      {item.attachments && item.attachments.length > 0 && (
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: item.text ? 6 : 0 }}>
          {item.attachments.map((a) => (
            <img
              key={a.id}
              src={a.dataUrl}
              alt={a.name}
              title={a.name}
              style={{ maxWidth: 160, maxHeight: 120, borderRadius: 6, border: "1px solid var(--border)", objectFit: "cover" }}
            />
          ))}
        </div>
      )}
      {item.text && <Markdown text={item.text} />}
      {item.tools.map((tc) => (
        <ToolCard key={tc.id} call={tc} />
      ))}
      {item.approvals.map((a) => (
        <div className="approval-card" key={a.id}>
          <strong>Approval required: {a.action}</strong>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 11.5 }}>{a.detail}</pre>
          {a.answered ? (
            <em style={{ color: "var(--text-dim)" }}>You chose: {a.answered}</em>
          ) : (
            <div className="actions">
              <button className="primary" onClick={() => replyTo(a.id, "approve")} data-testid="approve-btn">
                Approve
              </button>
              <button className="ghost" onClick={() => replyTo(a.id, "deny")}>Deny</button>
            </div>
          )}
        </div>
      ))}
      {item.questions.map((q) => (
        <div className="approval-card" key={q.id}>
          <strong>Question:</strong> <Markdown text={q.question} />
          {q.answered ? (
            <em style={{ color: "var(--text-dim)" }}>You answered: {q.answered}</em>
          ) : (
            <div className="actions">
              <input
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="Type your answer..."
                aria-label="Answer"
                onKeyDown={(e) => e.key === "Enter" && answer.trim() && replyTo(q.id, answer.trim())}
              />
              <button className="primary" onClick={() => answer.trim() && replyTo(q.id, answer.trim())}>
                Send
              </button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function TodoList() {
  const todos = useStore((s) => s.todos);
  if (!todos.length) return null;
  return (
    <div data-testid="todo-list" style={{ borderTop: "1px solid var(--border)", padding: "6px 12px", fontSize: 12 }}>
      <div style={{ color: "var(--text-dim)", fontSize: 11, marginBottom: 4, textTransform: "uppercase", letterSpacing: 0.6 }}>Tasks</div>
      {todos.map((t) => (
        <div key={t.id} style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <span className={`badge ${t.status === "done" ? "ok" : t.status === "in_progress" ? "warn" : ""}`}>{t.status}</span>
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}

function AgentStatusBar() {
  const st = useStore((s) => s.agentStatus);
  const elapsed = st.running ? Math.round((Date.now() - st.startedAt) / 1000) : 0;
  const [, force] = useState(0);
  useEffect(() => {
    if (!st.running) return;
    const iv = setInterval(() => force((n) => n + 1), 1000);
    return () => clearInterval(iv);
  }, [st.running]);
  const cls = st.status.includes("waiting") ? "waiting" : st.status === "error" ? "error" : st.running ? "running" : "";
  return (
    <div className={`agent-status ${cls}`} data-testid="agent-status">
      <span className="dot" />
      <span>
        {st.status}
        {st.tool ? ` (${st.tool})` : ""}
      </span>
      {st.running && <span className="badge">step {st.step} · {elapsed}s</span>}
      {(st.inputTokens > 0 || st.outputTokens > 0) && (
        <span className="badge">in {st.inputTokens} / out {st.outputTokens} tok</span>
      )}
      {st.dirty && <span className="badge warn">unverified changes</span>}
      {st.stallWarning && <span className="badge error">{st.stallWarning}</span>}
    </div>
  );
}

const MAX_ATTACHMENT_BYTES = 6 * 1024 * 1024;

export default function ChatPanel() {
  const chat = useStore((s) => s.chat);
  const sendPrompt = useStore((s) => s.sendPrompt);
  const stopAgent = useStore((s) => s.stopAgent);
  const running = useStore((s) => s.agentStatus.running);
  const agentMode = useStore((s) => s.agentMode);
  const setAgentMode = useStore((s) => s.setAgentMode);
  const permissionMode = useStore((s) => s.permissionMode);
  const setPermissionMode = useStore((s) => s.setPermissionMode);
  const newSession = useStore((s) => s.newSession);
  const setToast = useStore((s) => s.setToast);
  const connectAgent = useStore((s) => s.connectAgent);
  const pendingAttachments = useStore((s) => s.pendingAttachments);
  const addPendingAttachment = useStore((s) => s.addPendingAttachment);
  const removePendingAttachment = useStore((s) => s.removePendingAttachment);
  const models = useStore((s) => s.models);
  const modelSelected = useStore((s) => s.modelSelected);
  const modelDefault = useStore((s) => s.modelDefault);
  const [text, setText] = useState("");
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const stallTimer = useRef<number | null>(null);

  useEffect(() => {
    connectAgent();
  }, [connectAgent]);

  useEffect(() => {
    listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
  }, [chat]);

  // Stall detection: if the agent is running but no event arrives, warn the user.
  useEffect(() => {
    if (!running) return;
    stallTimer.current = window.setInterval(() => {
      const sock = useStore.getState().socket;
      if (!sock) return;
      const idle = sock.stalledSeconds;
      if (idle > 60) {
        useStore.setState((s) => ({
          agentStatus: {
            ...s.agentStatus,
            stallWarning: `no response from the model for ${Math.round(idle)}s`,
          },
        }));
      }
    }, 5000);
    return () => {
      if (stallTimer.current) clearInterval(stallTimer.current);
    };
  }, [running]);

  const onSend = () => {
    const t = text.trim();
    if (!t && pendingAttachments.length === 0) return;
    if (pendingAttachments.length > 0) {
      const ref = modelSelected || modelDefault;
      const info = Object.values(models).flat().find((m) => m.ref === ref);
      if (info && !info.multimodal) {
        setToast(`${info.name} can't see images - pick a model marked "(vision)" or remove the attachment`);
        return;
      }
    }
    sendPrompt(t);
    setText("");
    inputRef.current?.focus();
  };

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    for (const f of files) {
      setText((prev) => `${prev}${prev ? "\n" : ""}@${(f as any).path || f.name} `);
    }
    setToast(`${files.length} file(s) attached by mention`);
  };

  const insertMention = () => {
    setText((prev) => `${prev}@`);
    inputRef.current?.focus();
    setToast("Type @ followed by a file path to attach it to the context");
  };

  const attachImageFile = (file: File) => {
    if (file.size > MAX_ATTACHMENT_BYTES) {
      setToast(`${file.name || "image"} is too large (max 6MB)`);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      addPendingAttachment({
        id: `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        name: file.name || "screenshot.png",
        mime: file.type || "image/png",
        dataUrl: String(reader.result || ""),
      });
    };
    reader.readAsDataURL(file);
  };

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData?.items || []);
    const images = items.filter((it) => it.kind === "file" && it.type.startsWith("image/"));
    if (images.length === 0) return;
    e.preventDefault();
    for (const it of images) {
      const file = it.getAsFile();
      if (file) attachImageFile(file);
    }
  };

  return (
    <div className="chat-panel" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
      <div className="chat-header">
        <span className="title">Wotan</span>
        <ProviderToggle />
        <ModelPicker />
        <button title="New session" onClick={newSession}>new</button>
      </div>
      <AgentStatusBar />
      <div className="chat-messages" ref={listRef} aria-live="polite">
        {chat.length === 0 && (
          <div className="empty-state" style={{ height: "auto", marginTop: 24 }}>
            <img src="./logo.svg" alt="" style={{ width: 56, height: 56 }} />
            <div>
              Ask for a change, a script, or any task. The agent plans, edits (safely), runs and{" "}
              <strong>verifies</strong> before claiming done.
            </div>
            <div className="hints">
              <span>try: "add a README section about installation"</span>
              <span>try: "organize the files in /inbox by month"</span>
              <span>drag files here or use @ to attach them</span>
            </div>
          </div>
        )}
        {chat.map((item) => (
          <MessageView key={item.id} item={item} />
        ))}
      </div>
      <TodoList />
      <div className="composer">
        {pendingAttachments.length > 0 && (
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "6px 6px 0" }}>
            {pendingAttachments.map((a) => (
              <div key={a.id} style={{ position: "relative" }}>
                <img
                  src={a.dataUrl}
                  alt={a.name}
                  title={a.name}
                  style={{ width: 56, height: 56, objectFit: "cover", borderRadius: 6, border: "1px solid var(--border)" }}
                />
                <button
                  title="Remove"
                  onClick={() => removePendingAttachment(a.id)}
                  style={{
                    position: "absolute", top: -6, right: -6, width: 18, height: 18, padding: 0,
                    borderRadius: "50%", lineHeight: "16px", fontSize: 11,
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
        <textarea
          ref={inputRef}
          value={text}
          placeholder="Message Wotan... (Enter to send, Shift+Enter for newline; @ to attach files, Ctrl+V to paste a screenshot)"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          onPaste={onPaste}
          aria-label="Message"
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
        />
        <div className="composer-row">
          <select value={agentMode} onChange={(e) => setAgentMode(e.target.value as any)} aria-label="Agent mode" title="Agent mode">
            {MODES.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
          <select
            value={permissionMode}
            onChange={(e) => setPermissionMode(e.target.value as any)}
            aria-label="Permission mode"
            title="Permission mode: ask / auto-approve edits / autonomous"
          >
            {PERMS.map((m) => (
              <option key={m} value={m}>
                {m === "ask" ? "always ask" : m === "edits" ? "auto-approve edits" : "autonomous"}
              </option>
            ))}
          </select>
          <button title="Attach file (@ mention)" onClick={insertMention}>@</button>
          {running ? (
            <button className="stop" onClick={stopAgent} data-testid="stop-btn" title="Stop immediately">
              Stop
            </button>
          ) : (
            <button className="send" onClick={onSend} data-testid="send-btn">
              Send
            </button>
          )}
          <span className="hint">mid-run messages are queued</span>
        </div>
      </div>
    </div>
  );
}
