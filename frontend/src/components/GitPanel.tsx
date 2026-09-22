import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

export default function GitPanel() {
  const files = useStore((s) => s.gitFiles);
  const branch = useStore((s) => s.gitBranch);
  const loadGit = useStore((s) => s.loadGit);
  const openDiff = useStore((s) => s.openDiff);
  const setToast = useStore((s) => s.setToast);
  const [message, setMessage] = useState("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});

  useEffect(() => {
    void loadGit();
  }, [loadGit]);

  const showDiff = async (path: string) => {
    const res = await api.gitDiff(path);
    openDiff(path, res.diff || "(no diff)");
  };

  const stage = async () => {
    const paths = Object.keys(selected).filter((k) => selected[k]);
    if (!paths.length) return;
    const res = await api.gitStage(paths);
    if (!res.ok) setToast(res.output);
    setSelected({});
    void loadGit();
  };

  const commit = async () => {
    if (!message.trim()) return setToast("Commit message is empty");
    const res = await api.gitCommit(message.trim());
    setToast(res.output.slice(0, 300));
    if (res.ok) setMessage("");
    void loadGit();
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}>
        <span className="badge">⎇ {branch || "no repo"}</span>
        <button title="Refresh status" onClick={() => void loadGit()}>↻</button>
        <button title="Stage selected" onClick={() => void stage()}>stage</button>
      </div>
      {files.length === 0 && <div style={{ color: "var(--text-dim)" }}>Working tree clean.</div>}
      {files.map((f) => (
        <div className="list-row" key={f.path}>
          <input
            type="checkbox"
            aria-label={`Select ${f.path}`}
            checked={!!selected[f.path]}
            onChange={(e) => setSelected({ ...selected, [f.path]: e.target.checked })}
          />
          <span className={`st ${f.status.trim()}`}>{f.status.trim() || "?"}</span>
          <button className="path" style={{ textAlign: "left", flex: 1 }} onClick={() => void showDiff(f.path)} title="Open diff">
            {f.path}
          </button>
        </div>
      ))}
      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6 }}>
        <input
          placeholder="Commit message (no secrets, no emoji)"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          aria-label="Commit message"
        />
        <button onClick={() => void commit()} style={{ background: "var(--accent)", color: "var(--text-inverse)", padding: "5px 0" }}>
          Commit
        </button>
      </div>
    </div>
  );
}
