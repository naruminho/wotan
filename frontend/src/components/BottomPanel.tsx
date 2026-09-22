import React from "react";
import { useStore } from "../state/store";
import TerminalPanel from "./TerminalPanel";
import LogPanel from "./LogPanel";

export default function BottomPanel() {
  const view = useStore((s) => s.bottomView);
  const setView = useStore((s) => s.setBottomView);
  const toggleBottom = useStore((s) => s.toggleBottom);
  return (
    <div className="panel-bottom">
      <div className="panel-tabs" role="tablist" aria-label="Bottom panel">
        <button className={view === "terminal" ? "active" : ""} onClick={() => setView("terminal")} role="tab" aria-selected={view === "terminal"}>
          Terminal
        </button>
        <button className={view === "logs" ? "active" : ""} onClick={() => setView("logs")} role="tab" aria-selected={view === "logs"}>
          Logs
        </button>
        <div className="spacer" />
        <button title="Close panel (Ctrl+J)" onClick={toggleBottom}>×</button>
      </div>
      <div className="panel-body">
        {view === "terminal" ? <TerminalPanel /> : <LogPanel />}
      </div>
    </div>
  );
}
