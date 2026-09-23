import React from "react";
import { useStore } from "../state/store";

/** Segmented provider switcher in the chat header: e.g. OpenRouter <-> the
 * corporate gateway, one click each way. Switching restores that provider's
 * own last-used model (server-side memory, shared across browsers). Hidden
 * when there is nothing to alternate between. */
export default function ProviderToggle() {
  const providers = useStore((s) => s.providers);
  const active = useStore((s) => s.activeProvider);
  const switchProvider = useStore((s) => s.switchProvider);

  if (providers.length < 2) return null;

  return (
    <div style={{ display: "flex", alignItems: "center" }} data-testid="provider-toggle" role="tablist" aria-label="Provider">
      {providers.map((p, i) => {
        const isActive = p.id === active;
        return (
          <React.Fragment key={p.id}>
            {i > 0 && (
              <span aria-hidden="true" style={{ color: "var(--text-dim)", fontSize: 10, padding: "0 2px" }}>
                |
              </span>
            )}
            <button
              type="button"
              role="tab"
              aria-selected={isActive}
              title={`${p.name}${p.type ? ` · ${p.type}` : ""} - click to switch (its own last model is restored)`}
              onClick={() => void switchProvider(p.id)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                fontSize: 11,
                padding: "2px 6px",
                borderRadius: 4,
                color: isActive ? "var(--text)" : "var(--text-dim)",
                fontWeight: isActive ? 600 : 400,
                borderBottom: isActive ? "2px solid var(--accent)" : "2px solid transparent",
              }}
            >
              {p.name || p.id}
            </button>
          </React.Fragment>
        );
      })}
    </div>
  );
}
