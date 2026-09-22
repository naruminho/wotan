import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

/** Persistent memory: markdown files, editable from the UI. */
export default function MemoryPanel() {
  const setToast = useStore((s) => s.setToast);
  const [files, setFiles] = useState<{ name: string; summary: string; content: string }[]>([]);
  const [active, setActive] = useState(0);

  const load = async () => {
    const res = await api.memory();
    setFiles(res.files || []);
  };

  useEffect(() => {
    void load();
  }, []);

  const current = files[active];
  return (
    <div style={{ display: "flex", height: "100%" }} data-testid="memory-panel">
      <div style={{ width: 200, borderRight: "1px solid var(--border)", overflow: "auto", padding: 8 }}>
        {files.map((f, i) => (
          <button key={f.name} className={`tree-item ${i === active ? "selected" : ""}`} onClick={() => setActive(i)}>
            {f.name}
          </button>
        ))}
        <button
          className="tree-item"
          onClick={() => {
            void api.saveMemory(`notes-${Date.now()}.md`, "# New notes\n").then(load);
          }}
        >
          + new memory file
        </button>
      </div>
      <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: 8 }}>
        {current ? (
          <>
            <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
              <strong>{current.name}</strong>
              <span style={{ color: "var(--text-dim)", fontSize: 11.5 }}>{current.summary}</span>
              <button
                style={{ marginLeft: "auto", background: "var(--accent)", color: "var(--text-inverse)", padding: "4px 14px" }}
                onClick={() => void api.saveMemory(current.name, current.content).then(() => setToast("Memory saved"))}
              >
                Save
              </button>
            </div>
            <textarea
              style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: 12.5, whiteSpace: "pre" }}
              value={current.content}
              onChange={(e) => {
                const next = [...files];
                next[active] = { ...current, content: e.target.value };
                setFiles(next);
              }}
              aria-label={`Memory ${current.name}`}
            />
          </>
        ) : (
          <div style={{ color: "var(--text-dim)", padding: 24 }}>
            No memory files yet. The agent saves durable facts and preferences here (with your approval) and loads them
            on demand across sessions.
          </div>
        )}
      </div>
    </div>
  );
}
