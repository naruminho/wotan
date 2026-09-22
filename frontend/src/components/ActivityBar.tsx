import React from "react";
import { useStore } from "../state/store";

const items = [
  { id: "explorer", icon: "▤", label: "Explorer (Ctrl+Shift+E)" },
  { id: "search", icon: "⌕", label: "Search (Ctrl+Shift+F)" },
  { id: "git", icon: "⎇", label: "Source control (Ctrl+Shift+G)" },
  { id: "assistant", icon: "✦", label: "Assistant: memory, skills, inbox, experiments" },
] as const;

export default function ActivityBar() {
  const view = useStore((s) => s.sidebarView);
  const setView = useStore((s) => s.setSidebarView);
  const sidebarVisible = useStore((s) => s.sidebarVisible);
  const toggleChat = useStore((s) => s.toggleChat);
  const chatVisible = useStore((s) => s.chatVisible);
  return (
    <nav className="activitybar" aria-label="Activity bar">
      {items.map((it) => (
        <button
          key={it.id}
          className={view === it.id && sidebarVisible ? "active" : ""}
          title={it.label}
          aria-label={it.label}
          onClick={() => {
            if (view === it.id) useStore.getState().toggleSidebar();
            else setView(it.id);
          }}
        >
          {it.icon}
        </button>
      ))}
      <div style={{ flex: 1 }} />
      <button
        className={chatVisible ? "active" : ""}
        title="Toggle agent chat panel"
        aria-label="Toggle agent chat panel"
        onClick={toggleChat}
      >
        ✧
      </button>
    </nav>
  );
}
