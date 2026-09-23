import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ProviderToggle from "../components/ProviderToggle";
import { useStore } from "../state/store";

const providers = [
  { id: "openrouter", name: "OpenRouter", type: "custom", enabled: true },
  { id: "corp", name: "Corp Gateway", type: "generic_http", enabled: true },
];

describe("ProviderToggle", () => {
  beforeEach(() => {
    useStore.setState({ providers, activeProvider: "openrouter", switchProvider: vi.fn().mockResolvedValue(undefined) });
  });

  it("renders one segment per provider, active one highlighted", () => {
    render(<ProviderToggle />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(2);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    expect(tabs[1].getAttribute("aria-selected")).toBe("false");
    expect(screen.getByText("OpenRouter")).toBeInTheDocument();
    expect(screen.getByText("Corp Gateway")).toBeInTheDocument();
  });

  it("is hidden when there are fewer than two providers", () => {
    useStore.setState({ providers: [providers[0]] });
    render(<ProviderToggle />);
    expect(screen.queryByTestId("provider-toggle")).not.toBeInTheDocument();
  });

  it("clicking a segment switches to that provider", () => {
    const switchProvider = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ switchProvider });
    render(<ProviderToggle />);
    fireEvent.click(screen.getByText("Corp Gateway"));
    expect(switchProvider).toHaveBeenCalledWith("corp");
  });

  it("tracks the store's active provider", () => {
    useStore.setState({ activeProvider: "corp" });
    render(<ProviderToggle />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  });
});
