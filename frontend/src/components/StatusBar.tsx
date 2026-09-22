import React from "react";
import { useStore } from "../state/store";

export default function StatusBar() {
  const st = useStore((s) => s.agentStatus);
  const branch = useStore((s) => s.gitBranch);
  const tab = useStore((s) => s.activeTab());
  const modelSelected = useStore((s) => s.modelSelected);
  const modelDefault = useStore((s) => s.modelDefault);
  const model = modelSelected || modelDefault;
  const openSettings = useStore((s) => s.openSettings);
  const setSidebarView = useStore((s) => s.setSidebarView);
  const sessionId = useStore((s) => s.activeSessionId);

  return (
    <footer className="statusbar" data-testid="statusbar">
      <button title="Current workspace" onClick={() => setSidebarView("explorer")}>
        ⌂ {useStore((s) => s.workspace).split(/[\\/]/).pop() || "workspace"}
      </button>
      {branch && <button title="Git branch">⎇ {branch}</button>}
      <button title="Agent status" onClick={() => useStore.getState().toggleChat()}>
        {st.running ? `${st.status}${st.tool ? `: ${st.tool}` : ""} (${st.step} steps)` : st.status}
      </button>
      {st.dirty && <button title="Edits since the last verification - run verification before finishing">unverified changes</button>}
      <div className="right">
        <span title="Session">{(sessionId || "").slice(0, 10)}</span>
        {tab && <span>{tab.language || ""}</span>}
        {tab && <span>{(tab.encoding || "").toUpperCase()}</span>}
        <button title="Provider & model settings" onClick={openSettings}>
          {model || "no model"}
        </button>
      </div>
    </footer>
  );
}
