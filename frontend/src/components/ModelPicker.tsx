import React from "react";
import { useStore } from "../state/store";

const ADD_PREFIX = "__add__:";

/** GitHub Copilot-style model picker: grouped by provider, with metadata.
 * Each group ends with a "save model" entry that records a new model id under
 * that provider (UI-level list, merged by the server - config.yaml untouched). */
export default function ModelPicker() {
  const models = useStore((s) => s.models);
  const selected = useStore((s) => s.modelSelected);
  const def = useStore((s) => s.modelDefault);
  const setModel = useStore((s) => s.setModel);
  const addModel = useStore((s) => s.addModel);
  const value = selected || def;

  const onChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const v = e.target.value;
    if (v.startsWith(ADD_PREFIX)) {
      const provider = v.slice(ADD_PREFIX.length);
      const id = window.prompt(`Model id to save under "${provider}" (e.g. vendor/model):`);
      if (id && id.trim()) void addModel(provider, id.trim());
      e.target.value = value; // snap back; addModel selects it on success
      return;
    }
    setModel(v);
  };

  return (
    <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }} data-testid="model-picker">
      <span style={{ color: "var(--text-dim)" }}>model</span>
      <select
        value={value}
        onChange={onChange}
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
            <option value={`${ADD_PREFIX}${provider}`}>＋ save model…</option>
          </optgroup>
        ))}
        {Object.keys(models).length === 0 && <option value="">(no models configured)</option>}
      </select>
    </label>
  );
}
