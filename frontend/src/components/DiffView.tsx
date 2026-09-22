import React from "react";
import { parseUnifiedDiff } from "../util/diff";

export default function DiffView({ diff, path }: { diff: string; path: string }) {
  const rows = parseUnifiedDiff(diff);
  return (
    <div className="diffview" data-testid="diffview">
      <div className="file-header">{path}</div>
      {rows.map((r, i) => (
        <div key={i} className={`line ${r.type}`}>
          <span className="gutter">{r.type === "add" ? "+" : r.type === "del" ? "-" : " "}</span>
          <span className="content">{r.text}</span>
        </div>
      ))}
      {rows.length === 0 && <div style={{ padding: 12, color: "var(--text-dim)" }}>No differences.</div>}
    </div>
  );
}
