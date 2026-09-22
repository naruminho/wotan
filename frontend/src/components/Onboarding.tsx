import React from "react";
import { useStore } from "../state/store";

export default function Onboarding() {
  const open = useStore((s) => s.onboarding);
  const dismiss = useStore((s) => s.dismissOnboarding);
  const openSettings = useStore((s) => s.openSettings);
  if (!open) return null;
  return (
    <div className="modal-overlay">
      <div className="modal" role="dialog" aria-label="Welcome to Wotan">
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <img src="./logo.svg" alt="" style={{ width: 48, height: 48 }} />
          <h2 style={{ margin: 0 }}>Welcome to Wotan</h2>
        </div>
        <p style={{ margin: 0, color: "var(--text-dim)" }}>
          The all-seeing, wisdom-seeking workbench: a VS Code-style IDE with a built-in coding agent on the right.
        </p>
        <ol className="onboard-steps">
          <li>
            Point the agent at your LLM gateway in <strong>Settings</strong> - contracts are configured in YAML, no code
            changes needed. Use <em>Test connection</em> to tune it.
          </li>
          <li>Open a folder&apos;s files in the explorer, or just ask the agent to do it.</li>
          <li>
            The agent plans, edits safely (exact-match edits, syntax checks, checkpoints with undo), runs and{" "}
            <strong>verifies</strong> before it may claim a task is done.
          </li>
          <li>
            Keyboard: <kbd>Ctrl</kbd>+<kbd>P</kbd> open, <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>P</kbd> commands,{" "}
            <kbd>Ctrl</kbd>+<kbd>B</kbd> sidebar, <kbd>Ctrl</kbd>+<kbd>J</kbd> terminal.
          </li>
        </ol>
        <div className="actions">
          <button
            className="ghost"
            onClick={() => {
              dismiss();
              openSettings();
            }}
          >
            Open settings
          </button>
          <button className="primary" style={{ background: "var(--accent)", color: "var(--text-inverse)", padding: "6px 16px" }} onClick={dismiss}>
            Start working
          </button>
        </div>
      </div>
    </div>
  );
}
