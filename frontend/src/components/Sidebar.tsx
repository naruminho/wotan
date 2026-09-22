import React, { useEffect, useState } from "react";
import { useStore } from "../state/store";
import FileTree from "./FileTree";
import SearchPanel from "./SearchPanel";
import GitPanel from "./GitPanel";
import AssistantPanel from "./AssistantPanel";

export default function Sidebar() {
  const view = useStore((s) => s.sidebarView);
  return (
    <>
      <div className="sidebar-header">
        <span>{view === "explorer" ? "Explorer" : view === "search" ? "Search" : view === "git" ? "Source Control" : "Assistant"}</span>
      </div>
      <div className="sidebar-body">
        {view === "explorer" && <FileTree />}
        {view === "search" && <SearchPanel />}
        {view === "git" && <GitPanel />}
        {view === "assistant" && <AssistantPanel />}
      </div>
    </>
  );
}
