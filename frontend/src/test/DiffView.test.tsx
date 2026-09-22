import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import DiffView from "../components/DiffView";
import { diffLines, parseUnifiedDiff } from "../util/diff";

describe("diff utils", () => {
  it("computes line diffs", () => {
    const rows = diffLines("a\nb\nc", "a\nB\nc");
    expect(rows.filter((r) => r.type === "del").map((r) => r.text)).toEqual(["b"]);
    expect(rows.filter((r) => r.type === "add").map((r) => r.text)).toEqual(["B"]);
  });

  it("parses unified diffs", () => {
    const rows = parseUnifiedDiff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n");
    expect(rows.map((r) => r.type)).toEqual(["meta", "meta", "meta", "del", "add"]);
  });
});

describe("DiffView", () => {
  it("renders added and removed lines", () => {
    render(<DiffView diff={"--- a/f\n+++ b/f\n@@\n-x\n+y\n"} path="f" />);
    expect(screen.getByText("f")).toBeInTheDocument();
    expect(screen.getByText("-")).toBeInTheDocument();
    expect(screen.getByText("+")).toBeInTheDocument();
  });

  it("shows an empty state", () => {
    render(<DiffView diff="" path="f" />);
    expect(screen.getByText("No differences.")).toBeInTheDocument();
  });
});
