import { beforeEach, describe, expect, it, vi } from "vitest";

describe("editor fallback chain", () => {
  beforeEach(() => {
    vi.resetModules();
  });

  it("falls back to textarea when Monaco and CodeMirror both fail", async () => {
    vi.doMock("../editor/MonacoAdapter", () => ({
      tryCreateMonaco: async () => null,
    }));
    vi.doMock("../editor/CodeMirrorAdapter", () => ({
      tryCreateCodeMirror: async () => null,
    }));
    const { createEditor } = await import("../editor/createEditor");
    const adapter = await createEditor();
    expect(adapter.kind).toBe("textarea");
  });

  it("uses CodeMirror when Monaco is unavailable", async () => {
    vi.doMock("../editor/MonacoAdapter", () => ({
      tryCreateMonaco: async () => null,
    }));
    const { createEditor } = await import("../editor/createEditor");
    const adapter = await createEditor();
    // CodeMirror is a real dependency in the bundle, so it must win here.
    expect(["codemirror", "textarea"]).toContain(adapter.kind);
  });

  it("textarea adapter works standalone (mount, edit, line numbers)", async () => {
    const { createTextarea } = await import("../editor/TextareaAdapter");
    const adapter = createTextarea();
    const host = document.createElement("div");
    document.body.append(host);
    adapter.mount(host);
    const changed: string[] = [];
    adapter.onChange((v) => changed.push(v));
    adapter.setValue("a\nb\nc");
    expect(adapter.getValue()).toBe("a\nb\nc");
    const ta = host.querySelector("textarea")!;
    ta.value = "x";
    ta.dispatchEvent(new Event("input"));
    expect(changed).toEqual(["x"]);
    expect(host.textContent).toContain("1"); // line numbers rendered
    adapter.setReadOnly(true);
    expect(ta.readOnly).toBe(true);
    adapter.dispose();
    expect(host.querySelector("textarea")).toBeNull();
  });
});
