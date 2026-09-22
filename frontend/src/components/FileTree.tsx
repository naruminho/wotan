import React, { useState } from "react";
import { useStore } from "../state/store";
import { api } from "../api/client";

const FILE_ICONS: Record<string, string> = {
  py: "🐍", js: "𝗝", ts: "𝗧", json: "{}", yml: "𝑌", yaml: "𝑌", md: "𝑀", sh: ">_",
};

// NOTE: icons are text glyphs (system fonts) - no icon CDN.

export default function FileTree() {
  const tree = useStore((s) => s.tree);
  const treePath = useStore((s) => s.treePath);
  const loadTree = useStore((s) => s.loadTree);
  const openFile = useStore((s) => s.openFile);
  const setToast = useStore((s) => s.setToast);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [children, setChildren] = useState<Record<string, any[]>>({});
  const [context, setContext] = useState<string | null>(null);

  const toggleDir = async (path: string) => {
    if (expanded[path]) {
      setExpanded({ ...expanded, [path]: false });
      return;
    }
    if (!children[path]) {
      const res = await api.tree(path);
      setChildren({ ...children, [path]: res.entries });
    }
    setExpanded({ ...expanded, [path]: true });
  };

  const onCreate = async (type: "file" | "dir") => {
    const name = prompt(type === "file" ? "New file name:" : "New folder name:");
    if (!name) return;
    const res = await api.create(treePath ? `${treePath}/${name}` : name, type);
    if (res.error) setToast(res.error);
    void loadTree(treePath);
  };

  const onRename = async (path: string) => {
    const next = prompt("Rename to:", path.split("/").pop() || "");
    if (!next) return;
    const parent = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
    const res = await api.rename(path, parent ? `${parent}/${next}` : next);
    if (res.error) setToast(res.error);
    void loadTree(treePath);
  };

  const onDelete = async (path: string) => {
    if (!confirm(`Delete ${path}? This cannot be undone from the tree (checkpoints may still restore agent edits).`)) return;
    const res = await api.remove(path);
    if (res.error) setToast(res.error);
    void loadTree(treePath);
  };

  const renderEntries = (entries: any[], depth: number): React.ReactNode =>
    entries.map((e) => (
      <React.Fragment key={e.path}>
        <div
          className="tree-item"
          style={{ paddingLeft: 8 + depth * 14 }}
          onClick={() => (e.type === "dir" ? void toggleDir(e.path) : void openFile(e.path))}
          onContextMenu={(ev) => {
            ev.preventDefault();
            setContext(e.path);
          }}
          role="treeitem"
          aria-expanded={e.type === "dir" ? !!expanded[e.path] : undefined}
        >
          <span className="icon">{e.type === "dir" ? (expanded[e.path] ? "▾" : "▸") : FILE_ICONS[e.name.split(".").pop() || ""] || "·"}</span>
          <span>{e.name}</span>
          {e.type === "file" && <span className="dim">{e.size > 1024 ? `${Math.round(e.size / 1024)}k` : e.size}</span>}
          {context === e.path && (
            <span style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
              <button title="Rename" onClick={(ev) => { ev.stopPropagation(); setContext(null); void onRename(e.path); }}>✎</button>
              <button title="Delete" onClick={(ev) => { ev.stopPropagation(); setContext(null); void onDelete(e.path); }}>×</button>
            </span>
          )}
        </div>
        {e.type === "dir" && expanded[e.path] && renderEntries(children[e.path] || [], depth + 1)}
      </React.Fragment>
    ));

  return (
    <div role="tree" aria-label="File explorer">
      <div style={{ display: "flex", gap: 4, padding: "0 8px 6px" }}>
        <button title="New file" onClick={() => void onCreate("file")}>+ file</button>
        <button title="New folder" onClick={() => void onCreate("dir")}>+ folder</button>
        <button title="Refresh" onClick={() => void loadTree(treePath)}>↻</button>
      </div>
      {tree.length === 0 && <div style={{ color: "var(--text-dim)", padding: 8 }}>Empty workspace - create a file to start.</div>}
      {renderEntries(tree, 0)}
    </div>
  );
}
