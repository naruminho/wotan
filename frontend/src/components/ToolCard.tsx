import React, { useState } from "react";
import type { ToolCallCard } from "../state/store";

const PRETTY_TOOL: Record<string, string> = {
  fs_read: "Read file",
  fs_edit: "Edit file",
  fs_write: "Write file",
  fs_multi_edit: "Multi edit",
  fs_list: "List dir",
  fs_glob: "Find files",
  fs_grep: "Search",
  shell_exec: "Run command",
  py_run: "Run Python",
  web_search: "Web search",
  web_fetch: "Fetch page",
  todo_write: "Update tasks",
  ask_user: "Ask user",
  finish_task: "Finish task",
  read_app_logs: "Read logs",
  search_sessions: "Search sessions",
};

export default function ToolCard({ call }: { call: ToolCallCard }) {
  const [open, setOpen] = useState(call.status === "error");
  const pretty = PRETTY_TOOL[call.tool] || call.tool;
  const isError = call.status === "error";
  return (
    <div className="toolcard" data-testid="toolcard">
      <button
        className="toolcard-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        title="Expand tool call"
      >
        <span className={isError ? "err" : "ok"}>{call.status === "running" ? "[run]" : isError ? "[ERROR]" : "[OK]"}</span>
        <span className="toolname">{pretty}</span>
        <span style={{ color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 160 }}>
          {call.arguments.replace(/\s+/g, " ").slice(0, 80)}
        </span>
        <span className="meta">{call.durationS ? `${call.durationS}s` : call.status === "running" ? "..." : ""} {open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="toolcard-body">
          <div>arguments: {call.arguments}</div>
          {call.live && <div>live output:\n{call.live}</div>}
          <div className={isError ? "err" : ""}>{call.result || (call.status === "running" ? "running..." : "")}</div>
        </div>
      )}
    </div>
  );
}
