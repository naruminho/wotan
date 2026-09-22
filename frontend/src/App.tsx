import React, { useCallback, useEffect, useRef, useState } from "react";
import { useStore } from "./state/store";
import ActivityBar from "./components/ActivityBar";
import Sidebar from "./components/Sidebar";
import EditorArea from "./components/EditorArea";
import BottomPanel from "./components/BottomPanel";
import ChatPanel from "./components/ChatPanel";
import CommandPalette from "./components/CommandPalette";
import StatusBar from "./components/StatusBar";
import Onboarding from "./components/Onboarding";

export default function App() {
  const init = useStore((s) => s.init);
  const sidebarVisible = useStore((s) => s.sidebarVisible);
  const chatVisible = useStore((s) => s.chatVisible);
  const bottomVisible = useStore((s) => s.bottomVisible);
  const openPalette = useStore((s) => s.openPalette);
  const toast = useStore((s) => s.toast);
  const [sidebarWidth, setSidebarWidth] = useState(240);
  const [chatWidth, setSidebarChatWidth] = useState(380);
  const [bottomHeight, setBottomHeight] = useState(220);

  useEffect(() => {
    void init();
  }, [init]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey;
      if (ctrl && e.shiftKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        openPalette("commands");
      } else if (ctrl && !e.shiftKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        openPalette("files");
      } else if (ctrl && e.key.toLowerCase() === "b") {
        e.preventDefault();
        useStore.getState().toggleSidebar();
      } else if (ctrl && e.key.toLowerCase() === "j") {
        e.preventDefault();
        useStore.getState().toggleBottom();
      } else if (ctrl && e.key.toLowerCase() === "s") {
        e.preventDefault();
        const s = useStore.getState();
        const tab = s.activeTab();
        if (tab && tab.modified) void s.saveTab(tab.id).then((err) => err && s.setToast(err));
      } else if (ctrl && (e.key === "=" || e.key === "+")) {
        e.preventDefault();
        useStore.getState().setZoom(Math.min(1.6, useStore.getState().zoom + 0.1));
      } else if (ctrl && e.key === "-") {
        e.preventDefault();
        useStore.getState().setZoom(Math.max(0.7, useStore.getState().zoom - 0.1));
      } else if (ctrl && e.key === "0") {
        e.preventDefault();
        useStore.getState().setZoom(1);
      } else if (e.key === "Escape") {
        useStore.getState().closePalette();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openPalette]);

  // "Follow Windows" theme option.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const apply = () => {
      const s = useStore.getState();
      if (s.followSystem) s.setTheme(mq.matches ? "light" : "dark");
    };
    mq.addEventListener("change", apply);
    apply();
    return () => mq.removeEventListener("change", apply);
  }, []);

  const startResize = (which: "sidebar" | "chat" | "bottom") => (e: React.MouseEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startY = e.clientY;
    const startSidebar = sidebarWidth;
    const startChat = chatWidth;
    const startBottom = bottomHeight;
    const onMove = (ev: MouseEvent) => {
      if (which === "sidebar") setSidebarWidth(Math.max(160, Math.min(480, startSidebar + ev.clientX - startX)));
      if (which === "chat") setSidebarChatWidth(Math.max(280, Math.min(720, startChat - (ev.clientX - startX))));
      if (which === "bottom") setBottomHeight(Math.max(100, Math.min(640, startBottom - (ev.clientY - startY))));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  return (
    <div className="app">
      <TitleBar />
      <div className="app-body">
        <ActivityBar />
        {sidebarVisible && (
          <>
            <div className="sidebar" style={{ width: sidebarWidth }} data-testid="sidebar">
              <Sidebar />
            </div>
            <div className="resize-handle" onMouseDown={startResize("sidebar")} />
          </>
        )}
        <div className="main-col">
          <div className="center-row">
            <EditorArea />
          </div>
          {bottomVisible && (
            <>
              <div className="resize-handle row" onMouseDown={startResize("bottom")} />
              <div style={{ height: bottomHeight, display: "flex", flexDirection: "column", minHeight: 0 }}>
                <BottomPanel />
              </div>
            </>
          )}
        </div>
        {chatVisible && (
          <>
            <div className="resize-handle" onMouseDown={startResize("chat")} />
            <div style={{ width: chatWidth, display: "flex", flexDirection: "column", minWidth: 280 }} data-testid="chat-panel">
              <ChatPanel />
            </div>
          </>
        )}
      </div>
      <StatusBar />
      <CommandPalette />
      <Onboarding />
      {toast && (
        <div className="modal-overlay" style={{ pointerEvents: "none", background: "transparent", placeItems: "end center" }}>
          <div className="modal" style={{ width: "auto", maxWidth: 520, margin: 16 }}>{toast}</div>
        </div>
      )}
    </div>
  );
}

function TitleBar() {
  const toggleTheme = useStore((s) => s.toggleTheme);
  const openSettings = useStore((s) => s.openSettings);
  const openPalette = useStore((s) => s.openPalette);
  const workspace = useStore((s) => s.workspace);
  return (
    <div className="titlebar">
      <img className="logo" src="./logo.svg" alt="" />
      <span className="title">Wotan</span>
      <span style={{ color: "var(--text-dim)", fontSize: 11 }}>{workspace}</span>
      <div className="menus">
        <button onClick={() => openPalette("commands")} title="Command palette (Ctrl+Shift+P)">Commands</button>
        <button onClick={() => openPalette("files")} title="Quick open (Ctrl+P)">Open</button>
        <button onClick={openSettings} title="Providers, models and policies">Settings</button>
        <button onClick={toggleTheme} title="Toggle dark / light theme">Theme</button>
      </div>
    </div>
  );
}
