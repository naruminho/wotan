import React, { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../api/client";

interface Command {
  id: string;
  title: string;
  detail?: string;
  run: () => void | Promise<void>;
}

export default function CommandPalette() {
  const open = useStore((s) => s.paletteOpen);
  const mode = useStore((s) => s.paletteMode);
  const close = useStore((s) => s.closePalette);
  const [query, setQuery] = useState("");
  const [sel, setSel] = useState(0);
  const [files, setFiles] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSel(0);
      setFiles([]);
      setTimeout(() => inputRef.current?.focus(), 10);
    }
  }, [open]);

  // Quick open: gather file paths lazily via tree walking (bounded).
  useEffect(() => {
    if (!open || mode !== "files" || files.length) return;
    void (async () => {
      const acc: string[] = [];
      const walk = async (path: string, depth: number) => {
        if (depth > 4 || acc.length > 800) return;
        const res = await api.tree(path);
        for (const e of res.entries) {
          if (e.type === "file") acc.push(e.path);
          else if (e.name !== "node_modules" && e.name !== ".git") await walk(e.path, depth + 1);
        }
      };
      await walk("", 0);
      setFiles(acc);
    })();
  }, [open, mode, files.length]);

  const commands: Command[] = useMemo(() => {
    const s = useStore.getState();
    return [
      { id: "file.open", title: "File: Quick open", detail: "Ctrl+P", run: () => s.openPalette("files") },
      { id: "file.save", title: "File: Save", detail: "Ctrl+S", run: () => void s.saveTab(s.activeTabId || "") },
      { id: "view.theme", title: "View: Toggle theme", run: () => s.toggleTheme() },
      { id: "view.sidebar", title: "View: Toggle sidebar", detail: "Ctrl+B", run: () => s.toggleSidebar() },
      { id: "view.panel", title: "View: Toggle bottom panel", detail: "Ctrl+J", run: () => s.toggleBottom() },
      { id: "view.split", title: "View: Split editor", run: () => s.toggleSplit() },
      { id: "view.zoomIn", title: "View: Zoom in", run: () => s.setZoom(Math.min(1.6, s.zoom + 0.1)) },
      { id: "view.zoomOut", title: "View: Zoom out", run: () => s.setZoom(Math.max(0.7, s.zoom - 0.1)) },
      { id: "term.new", title: "Terminal: New terminal", run: () => s.setBottomView("terminal") },
      { id: "logs.open", title: "Logs: Open log panel", run: () => s.setBottomView("logs") },
      { id: "logs.diagnostics", title: "Logs: Generate diagnostic bundle", run: () => api.diagnostics() },
      { id: "git.open", title: "Git: Open source control", run: () => s.setSidebarView("git") },
      { id: "search.open", title: "Search: Find in files", detail: "Ctrl+Shift+F", run: () => s.setSidebarView("search") },
      { id: "agent.settings", title: "Wotan: Provider & model settings", run: () => s.openSettings() },
      { id: "agent.memory", title: "Wotan: Open persistent memory", run: () => s.openMemory() },
      { id: "agent.newSession", title: "Wotan: New agent session", run: () => s.newSession() },
      { id: "agent.stop", title: "Wotan: Stop the running agent", run: () => s.stopAgent() },
    ];
  }, []);

  const items = useMemo(() => {
    if (mode === "files") {
      const q = query.toLowerCase();
      return files.filter((f) => f.toLowerCase().includes(q)).slice(0, 50).map((f) => ({
        id: f,
        title: f.split("/").pop() || f,
        detail: f,
        run: () => void useStore.getState().openFile(f),
      }));
    }
    const q = query.toLowerCase().replace(/^>/, "");
    return commands.filter((c) => c.title.toLowerCase().includes(q) || c.id.includes(q));
  }, [mode, query, files, commands]);

  if (!open) return null;

  return (
    <div className="palette-overlay" onClick={close}>
      <div className="palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={mode === "files" ? "Quick open" : "Command palette"}>
        <input
          ref={inputRef}
          value={query}
          placeholder={mode === "files" ? "Open file by name..." : "> Type a command..."}
          onChange={(e) => {
            setQuery(e.target.value);
            setSel(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSel((s) => Math.min(s + 1, items.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setSel((s) => Math.max(s - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              const item = items[sel];
              if (item) {
                close();
                void item.run();
              }
            }
          }}
          aria-label="Command input"
        />
        <div className="results">
          {items.map((it, i) => (
            <button
              key={it.id}
              className={`result ${i === sel ? "selected" : ""}`}
              onClick={() => {
                close();
                void it.run();
              }}
            >
              <span>{it.title}</span>
              {it.detail && <span className="detail">{it.detail}</span>}
            </button>
          ))}
          {items.length === 0 && <div style={{ padding: 12, color: "var(--text-dim)" }}>No matches.</div>}
        </div>
      </div>
    </div>
  );
}
