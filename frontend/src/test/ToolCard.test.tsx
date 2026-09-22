import React from "react";
import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ToolCard from "../components/ToolCard";
import type { ToolCallCard } from "../state/store";

const base: ToolCallCard = {
  id: "c1",
  tool: "fs_edit",
  arguments: '{"path": "a.py", "old_string": "x", "new_string": "y"}',
  result: '{"status": "ok", "snippet": "1| y"}',
  durationS: 0.42,
  live: "",
  status: "done",
};

describe("ToolCard", () => {
  it("renders a friendly tool name and duration", () => {
    render(<ToolCard call={base} />);
    expect(screen.getByText("Edit file")).toBeInTheDocument();
    expect(screen.getByText(/0\.42s/)).toBeInTheDocument();
  });

  it("auto-expands errors and marks them", () => {
    render(<ToolCard call={{ ...base, status: "error", result: '{"error": "old_string found 3 times"}' }} />);
    expect(screen.getByText("[ERROR]")).toBeInTheDocument();
    expect(screen.getByText(/found 3 times/)).toBeInTheDocument();
  });

  it("toggles details on click", () => {
    render(<ToolCard call={base} />);
    const header = screen.getByRole("button");
    expect(screen.queryByText(/"status": "ok"/)).not.toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.getByText(/"status": "ok"/)).toBeInTheDocument();
    fireEvent.click(header);
    expect(screen.queryByText(/"status": "ok"/)).not.toBeInTheDocument();
  });

  it("shows running state", () => {
    render(<ToolCard call={{ ...base, status: "running", durationS: 0 }} />);
    expect(screen.getByText("[run]")).toBeInTheDocument();
    expect(screen.getByText(/\.\.\./)).toBeInTheDocument();
  });
});
