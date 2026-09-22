import React, { useEffect, useRef } from "react";
import { createEditor, type EditorAdapter } from "../editor/createEditor";
import { useStore, type Tab } from "../state/store";
import DiffView from "./DiffView";
import SettingsPanel from "./SettingsPanel";
import MemoryPanel from "./MemoryPanel";

const adapters = new Map<string, EditorAdapter>();

function EditorHost({ tab }: { tab: Tab }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const setTabContent = useStore((s) => s.setTabContent);
  const theme = useStore((s) => s.theme);

  useEffect(() => {
    let disposed = false;
    let adapter: EditorAdapter | null = null;
    void (async () => {
      adapter = adapters.get(tab.id) || null;
      if (!adapter) {
        adapter = await createEditor();
        if (disposed) {
          adapter.dispose();
          return;
        }
        adapters.set(tab.id, adapter);
      }
      if (!hostRef.current) return;
      adapter.mount(hostRef.current);
      adapter.setValue(tab.content);
      adapter.setLanguage(tab.language || "plaintext");
      adapter.setTheme(theme);
      adapter.setReadOnly(tab.readOnly);
      adapter.onChange((v) => setTabContent(tab.id, v));
      adapter.focus();
    })();
    return () => {
      disposed = true;
      const a = adapters.get(tab.id);
      a?.dispose();
      adapters.delete(tab.id);
    };
    // Remount only when the tab identity changes; content updates flow through
    // the adapter setters below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab.id]);

  useEffect(() => {
    const a = adapters.get(tab.id);
    if (!a) return;
    if (a.getValue() !== tab.content) a.setValue(tab.content);
    a.setTheme(theme);
    a.setReadOnly(tab.readOnly);
  }, [tab.content, theme, tab.readOnly, tab.id]);

  return <div className="editor-host" ref={hostRef} data-testid={`editor-host-${tab.id}`} />;
}

function TabBar() {
  const tabs = useStore((s) => s.tabs);
  const activeTabId = useStore((s) => s.activeTabId);
  const setActiveTab = useStore((s) => s.setActiveTab);
  const closeTab = useStore((s) => s.closeTab);
  const moveTab = useStore((s) => s.moveTab);

  return (
    <div className="tabbar" role="tablist" aria-label="Open editors">
      {tabs.map((t) => (
        <div
          key={t.id}
          className={`tab ${t.id === activeTabId ? "active" : ""}`}
          role="tab"
          aria-selected={t.id === activeTabId}
          title={`${t.path} (drag-less reorder: shift+wheel)`}
          onClick={() => setActiveTab(t.id)}
          onAuxClick={(e) => {
            if (e.button === 1) closeTab(t.id);
          }}
          onWheel={(e) => {
            if (e.shiftKey) moveTab(t.id, e.deltaY > 0 ? 1 : -1);
          }}
        >
          <span>{t.title}</span>
          {t.modified && <span className="dirty" title="Unsaved changes">●</span>}
          <button
            className="close"
            title="Close (Ctrl+W)"
            aria-label={`Close ${t.title}`}
            onClick={(e) => {
              e.stopPropagation();
              closeTab(t.id);
            }}
          >
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

export default function EditorArea() {
  const tabs = useStore((s) => s.tabs);
  const activeTabId = useStore((s) => s.activeTabId);
  const split = useStore((s) => s.split);
  const toggleSplit = useStore((s) => s.toggleSplit);
  const active = tabs.find((t) => t.id === activeTabId) || null;

  const paneTabs = (pane: 0 | 1) => tabs.filter((t) => t.pane === pane && t.id === activeTabId);

  return (
    <div className="editor-area">
      <TabBar />
      <div style={{ display: "flex", alignItems: "center", padding: "2px 8px", gap: 8, borderBottom: "1px solid var(--border)" }}>
        <button onClick={toggleSplit} title="Toggle split editor">
          {split ? "◫ unsplit" : "▯ split"}
        </button>
        {active && (
          <span style={{ color: "var(--text-dim)", fontSize: 11.5 }}>
            {active.path} {active.readOnly ? "(read-only)" : ""} {active.encoding} {active.eol.toUpperCase()}
          </span>
        )}
      </div>
      <div className="editor-panes">
        {tabs.length === 0 && (
          <div className="empty-state">
            <img src="./logo.svg" alt="" />
            <strong>Wotan</strong>
            <div>All-seeing workspace - open a file or ask the agent.</div>
            <div className="hints">
              <span><kbd>Ctrl</kbd>+<kbd>P</kbd> quick open</span>
              <span><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> command palette</span>
              <span><kbd>Ctrl</kbd>+<kbd>B</kbd> sidebar · <kbd>Ctrl</kbd>+<kbd>J</kbd> panel</span>
            </div>
          </div>
        )}
        <div className="editor-pane">
          {paneTabs(0).map((t) =>
            t.kind === "diff" ? (
              <DiffView key={t.id} diff={t.diff || ""} path={t.path} />
            ) : t.kind === "settings" ? (
              <SettingsPanel key={t.id} />
            ) : t.kind === "memory" ? (
              <MemoryPanel key={t.id} />
            ) : (
              <EditorHost key={t.id} tab={t} />
            ),
          )}
          {split && paneTabs(1).length === 0 && active?.kind === "file" && (
            <EditorHost tab={{ ...active, id: `${active.id}-split`, pane: 1 }} />
          )}
        </div>
      </div>
    </div>
  );
}
