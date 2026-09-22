import React, { useEffect, useState } from "react";
import { useStore } from "../state/store";
import { api } from "../api/client";

interface DirEntry {
  name: string;
  path: string;
}

export default function FolderPicker() {
  const open = useStore((s) => s.folderPickerOpen);
  const close = useStore((s) => s.closeFolderPicker);
  const openFolder = useStore((s) => s.openFolder);
  const workspace = useStore((s) => s.workspace);
  const setToast = useStore((s) => s.setToast);

  const [path, setPath] = useState("");
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<DirEntry[]>([]);
  const [recent, setRecent] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async (target: string) => {
    setBusy(true);
    const res = await api.browseDirs(target);
    setBusy(false);
    if (res.error) {
      setError(res.error);
      return;
    }
    setError("");
    setPath(res.path);
    setParent(res.parent);
    setDirs(res.dirs);
  };

  useEffect(() => {
    if (!open) return;
    void load(workspace || "");
    void api.recentWorkspaces().then((r) => setRecent(r.paths || []));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  if (!open) return null;

  const confirmOpen = async (target: string) => {
    const err = await openFolder(target);
    if (err) setToast(err);
  };

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="modal" style={{ width: "min(640px, 92vw)" }} onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Open folder">
        <h2>Open folder</h2>

        {recent.length > 0 && (
          <div>
            <div style={{ color: "var(--text-dim)", fontSize: 11, textTransform: "uppercase", marginBottom: 4 }}>Recent</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {recent.map((r) => (
                <button key={r} onClick={() => void load(r)} title={r} style={{ border: "1px solid var(--border)", padding: "4px 10px", borderRadius: "var(--radius-sm)" }}>
                  {r.split(/[\\/]/).filter(Boolean).pop() || r}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ display: "flex", gap: 6 }}>
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && void load(path)}
            placeholder="Absolute path..."
            aria-label="Current path"
            style={{ flex: 1 }}
          />
          <button onClick={() => void load(path)} title="Go">Go</button>
        </div>

        {error && <div style={{ color: "var(--error)", fontSize: 12 }}>{error}</div>}

        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", maxHeight: 280, overflowY: "auto" }}>
          {parent !== null && (
            <div className="tree-item" onClick={() => void load(parent)} role="treeitem">
              <span className="icon">..</span>
              <span>Up</span>
            </div>
          )}
          {busy && <div style={{ padding: 10, color: "var(--text-dim)" }}>Loading...</div>}
          {!busy && dirs.length === 0 && parent !== null && (
            <div style={{ padding: 10, color: "var(--text-dim)" }}>No subfolders here.</div>
          )}
          {dirs.map((d) => (
            <div key={d.path} className="tree-item" onClick={() => void load(d.path)} role="treeitem">
              <span className="icon">📁</span>
              <span>{d.name}</span>
            </div>
          ))}
        </div>

        <div className="actions">
          <button className="ghost" onClick={close}>Cancel</button>
          <button
            className="primary"
            style={{ background: "var(--accent)", color: "var(--text-inverse)", padding: "6px 16px" }}
            disabled={!path || !!error}
            onClick={() => void confirmOpen(path)}
            data-testid="open-folder-confirm"
          >
            Open "{path || "..."}"
          </button>
        </div>
      </div>
    </div>
  );
}
