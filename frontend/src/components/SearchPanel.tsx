import React, { useState } from "react";
import { useStore } from "../state/store";

export default function SearchPanel() {
  const runSearch = useStore((s) => s.runSearch);
  const results = useStore((s) => s.searchResults);
  const openFile = useStore((s) => s.openFile);
  const [q, setQ] = useState("");
  const [glob, setGlob] = useState("");
  const [openPath, setOpenPath] = useState<string | null>(null);

  return (
    <div>
      <input
        style={{ width: "100%", marginBottom: 4 }}
        placeholder="Search across files (regex)"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void runSearch(q, glob)}
        aria-label="Search query"
      />
      <input
        style={{ width: "100%", marginBottom: 8 }}
        placeholder="File filter (glob, e.g. *.py)"
        value={glob}
        onChange={(e) => setGlob(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && void runSearch(q, glob)}
        aria-label="File filter"
      />
      <div style={{ color: "var(--text-dim)", marginBottom: 6 }}>
        {results.length === 0 && q ? "No matches." : results.length ? `${results.length} files with matches (details on demand)` : ""}
      </div>
      {results.map((f) => (
        <div key={f.path}>
          <button
            className="tree-item"
            onClick={() => setOpenPath(openPath === f.path ? null : f.path)}
            aria-expanded={openPath === f.path}
          >
            <span className="icon">{openPath === f.path ? "▾" : "▸"}</span>
            <span className="path" style={{ fontFamily: "var(--font-mono)", fontSize: 11.5 }}>{f.path}</span>
            <span className="dim">{f.matches.length}</span>
          </button>
          {openPath === f.path &&
            f.matches.map((m) => (
              <button
                key={m.line}
                className="tree-item"
                style={{ paddingLeft: 24, fontFamily: "var(--font-mono)", fontSize: 11 }}
                onClick={() => void openFile(f.path)}
                title={`Go to line ${m.line}`}
              >
                <span className="dim">{m.line}</span>
                <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{m.text}</span>
              </button>
            ))}
        </div>
      ))}
    </div>
  );
}
