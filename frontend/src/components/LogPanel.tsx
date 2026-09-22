import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

export default function LogPanel() {
  const logs = useStore((s) => s.logs);
  const loadLogs = useStore((s) => s.loadLogs);
  const setToast = useStore((s) => s.setToast);
  const [level, setLevel] = useState("");
  const [q, setQ] = useState("");

  useEffect(() => {
    void loadLogs(level, q);
    const iv = setInterval(() => void loadLogs(level, q), 4000);
    return () => clearInterval(iv);
  }, [level, q, loadLogs]);

  return (
    <div>
      <div style={{ display: "flex", gap: 6, padding: 6, borderBottom: "1px solid var(--border)", position: "sticky", top: 0, background: "var(--bg)" }}>
        <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label="Log level">
          <option value="">all levels</option>
          <option>DEBUG</option>
          <option>INFO</option>
          <option>WARNING</option>
          <option>ERROR</option>
        </select>
        <input placeholder="filter (search in messages)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Log filter" />
        <button title="Refresh" onClick={() => void loadLogs(level, q)}>↻</button>
        <button
          title="Show destructive/network actions the agent auto-approved without asking (e.g. in autonomous mode or via always_allow) - useful to audit after an unattended run"
          onClick={() => {
            setLevel("WARNING");
            setQ("auto-approved");
          }}
        >
          auto-approved actions
        </button>
        <button
          title="Download a zip with logs, masked config and versions"
          onClick={() => {
            api.diagnostics();
            setToast("Diagnostic bundle download started");
          }}
        >
          diagnostic bundle
        </button>
      </div>
      {logs.length === 0 && <div style={{ color: "var(--text-dim)", padding: 10 }}>No log entries.</div>}
      {logs.map((e, i) => (
        <div className="logrow" key={i}>
          <span className="lvl {e.level}">{e.level}</span>
          <span className="cid">{(e.correlation_id || "").slice(0, 10)}</span>
          <span className="msg">
            <strong>[{e.component}]</strong> {e.message}
          </span>
          <span className="cid">{e.ts}</span>
        </div>
      ))}
    </div>
  );
}
