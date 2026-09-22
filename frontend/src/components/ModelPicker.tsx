import React from "react";
import { useStore } from "../state/store";

/** GitHub Copilot-style model picker: grouped by provider, with metadata. */
export default function ModelPicker() {
  const models = useStore((s) => s.models);
  const selected = useStore((s) => s.modelSelected);
  const def = useStore((s) => s.modelDefault);
  const setModel = useStore((s) => s.setModel);
  const value = selected || def;

  return (
    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }} data-testid="model-picker">
      <span style={{ color: "var(--text-dim)" }}>model</span>
      <select
        value={value}
        onChange={(e) => setModel(e.target.value)}
        aria-label="Select model"
        style={{ maxWidth: 220 }}
      >
        {Object.entries(models).map(([provider, list]) => (
          <optgroup key={provider} label={provider}>
            {list.map((m) => (
              <option key={m.ref} value={m.ref}>
                {m.name}
                {m.weak ? " (weak)" : ""}
                {m.multimodal ? " (vision)" : ""}
              </option>
            ))}
          </optgroup>
        ))}
        {Object.keys(models).length === 0 && <option value="">(no models configured)</option>}
      </select>
    </label>
  );
}
