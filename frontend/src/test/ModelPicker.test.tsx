import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import ModelPicker from "../components/ModelPicker";
import { useStore } from "../state/store";

describe("ModelPicker", () => {
  beforeEach(() => {
    useStore.setState({
      models: {
        corp_gateway: [
          {
            id: "big-1",
            ref: "corp_gateway/big-1",
            name: "Big model",
            provider: "corp_gateway",
            provider_type: "generic_http",
            context_window: 128000,
            tools: true,
            streaming: true,
            weak: false,
            multimodal: false,
            edit_format: "str_replace",
            description: "",
          },
          {
            id: "small-1",
            ref: "corp_gateway/small-1",
            name: "Small model",
            provider: "corp_gateway",
            provider_type: "generic_http",
            context_window: 8000,
            tools: true,
            streaming: false,
            weak: true,
            multimodal: false,
            edit_format: "hashline",
            description: "",
          },
        ],
      },
      modelDefault: "corp_gateway/big-1",
      modelSelected: "",
    });
  });

  it("groups models by provider with metadata", () => {
    render(<ModelPicker />);
    expect(screen.getByRole("group", { name: "corp_gateway" })).toBeInTheDocument();
    expect(screen.getByText(/Big model/)).toBeInTheDocument();
    expect(screen.getByText(/Small model \(weak\)/)).toBeInTheDocument();
  });

  it("selecting a model persists to the store", () => {
    render(<ModelPicker />);
    fireEvent.change(screen.getByLabelText("Select model"), { target: { value: "corp_gateway/small-1" } });
    expect(useStore.getState().modelSelected).toBe("corp_gateway/small-1");
    expect(localStorage.getItem("wotan.model")).toBe("corp_gateway/small-1");
  });

  it("shows an empty state when nothing is configured", () => {
    useStore.setState({ models: {} });
    render(<ModelPicker />);
    expect(screen.getByText("(no models configured)")).toBeInTheDocument();
  });
});

describe("ModelPicker - save model entries", () => {
  beforeEach(() => {
    useStore.setState({
      models: {
        corp_gateway: [
          {
            id: "big-1",
            ref: "corp_gateway/big-1",
            name: "Big model",
            provider: "corp_gateway",
            provider_type: "generic_http",
            context_window: 128000,
            tools: true,
            streaming: true,
            weak: false,
            multimodal: false,
            edit_format: "str_replace",
            description: "",
          },
        ],
      },
      modelDefault: "corp_gateway/big-1",
      modelSelected: "corp_gateway/big-1",
      addModel: vi.fn().mockResolvedValue(undefined),
    });
  });

  it("offers a save-model entry per provider group", () => {
    render(<ModelPicker />);
    expect(screen.getByText("＋ save model…")).toBeInTheDocument();
  });

  it("prompts for an id and records it under that provider", () => {
    const addModel = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ addModel });
    const prompt = vi.spyOn(window, "prompt").mockReturnValue("x-ai/grok-4");
    render(<ModelPicker />);
    fireEvent.change(screen.getByLabelText("Select model"), { target: { value: "__add__:corp_gateway" } });
    expect(addModel).toHaveBeenCalledWith("corp_gateway", "x-ai/grok-4");
    expect(prompt).toHaveBeenCalledWith(expect.stringContaining("corp_gateway"));
    // selection snaps back while the add runs
    expect(useStore.getState().modelSelected).toBe("corp_gateway/big-1");
    prompt.mockRestore();
  });

  it("cancelling the prompt records nothing", () => {
    const addModel = vi.fn().mockResolvedValue(undefined);
    useStore.setState({ addModel });
    const prompt = vi.spyOn(window, "prompt").mockReturnValue(null);
    render(<ModelPicker />);
    fireEvent.change(screen.getByLabelText("Select model"), { target: { value: "__add__:corp_gateway" } });
    expect(addModel).not.toHaveBeenCalled();
    prompt.mockRestore();
  });
});
