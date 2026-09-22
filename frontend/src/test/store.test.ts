import { beforeEach, describe, expect, it } from "vitest";
import { useStore } from "../state/store";
import type { Tab } from "../state/store";

const mkTab = (id: string, path: string): Tab => ({
  id,
  path,
  title: path,
  kind: "file",
  modified: false,
  readOnly: false,
  content: "x",
  savedContent: "x",
  encoding: "utf-8",
  eol: "lf",
  hash: "",
  pane: 0,
});

describe("editor tab store", () => {
  beforeEach(() => {
    useStore.setState({ tabs: [], activeTabId: null });
  });

  it("marks tabs modified when content diverges from the saved copy", () => {
    useStore.setState({ tabs: [mkTab("t1", "a.py")], activeTabId: "t1" });
    useStore.getState().setTabContent("t1", "changed");
    expect(useStore.getState().tabs[0].modified).toBe(true);
    useStore.getState().setTabContent("t1", "x");
    expect(useStore.getState().tabs[0].modified).toBe(false);
  });

  it("reorders tabs with moveTab", () => {
    useStore.setState({ tabs: [mkTab("t1", "a"), mkTab("t2", "b"), mkTab("t3", "c")], activeTabId: "t1" });
    useStore.getState().moveTab("t1", 1);
    expect(useStore.getState().tabs.map((t) => t.id)).toEqual(["t2", "t1", "t3"]);
    useStore.getState().moveTab("t3", -1);
    expect(useStore.getState().tabs.map((t) => t.id)).toEqual(["t2", "t3", "t1"]);
  });

  it("closeTab activates a neighbor", () => {
    useStore.setState({ tabs: [mkTab("t1", "a"), mkTab("t2", "b")], activeTabId: "t1" });
    useStore.getState().closeTab("t1");
    expect(useStore.getState().activeTabId).toBe("t2");
  });

  it("agent status updates on send/stop", () => {
    useStore.getState().stopAgent();
    expect(useStore.getState().agentStatus.running).toBe(false);
  });
});
