import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

/** Providers & models settings: the YAML remains the source of truth; this
 *  screen reads and writes it. Secrets stay in env vars / the OS keyring. */
export default function SettingsPanel() {
  const setToast = useStore((s) => s.setToast);
  const [yaml, setYaml] = useState("");
  const [path, setPath] = useState("");
  const [providers, setProviders] = useState<{ id: string; type: string; models: string[] }[]>([]);
  const [testResult, setTestResult] = useState<string>("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const res = await api.settings();
    setYaml(res.yaml);
    setPath(res.path);
    setProviders(res.summary?.providers || []);
  };

  useEffect(() => {
    void load();
  }, []);

  const save = async () => {
    setBusy(true);
    const res = await api.saveSettings(yaml);
    setBusy(false);
    if (res.error) setToast(res.error);
    else {
      setToast(`Configuration saved to ${res.path}`);
      void load();
    }
  };

  const test = async (providerId: string) => {
    setBusy(true);
    setTestResult("Testing...");
    const res = await api.testConnection(providerId);
    setBusy(false);
    setTestResult(res.ok ? res.rendered || JSON.stringify(res.report, null, 2) : `ERROR: ${res.error}`);
  };

  return (
    <div style={{ padding: 16, overflow: "auto", height: "100%" }} data-testid="settings-panel">
      <h2 style={{ marginTop: 0 }}>Providers &amp; models</h2>
      <p style={{ color: "var(--text-dim)" }}>
        YAML is the source of truth (<code>{path}</code>). Secrets are referenced as <code>env:VAR</code> or{" "}
        <code>keyring:service:user</code> and never stored in the file.
      </p>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        {providers.map((p) => (
          <div key={p.id} className="toolcard" style={{ padding: 12, minWidth: 220 }}>
            <strong>{p.id}</strong> <span className="badge">{p.type}</span>
            <div style={{ color: "var(--text-dim)", fontSize: 12, margin: "6px 0" }}>{p.models.join(", ")}</div>
            <button onClick={() => void test(p.id)} disabled={busy} style={{ border: "1px solid var(--border)", padding: "4px 12px" }}>
              Test connection
            </button>
          </div>
        ))}
        {providers.length === 0 && (
          <div style={{ color: "var(--text-dim)" }}>
            No providers configured yet. Start from <code>config.example.yaml</code> and paste the YAML below.
          </div>
        )}
      </div>
      {testResult && (
        <pre className="toolcard-body" style={{ maxHeight: 240, marginBottom: 12 }} data-testid="test-result">
          {testResult}
        </pre>
      )}
      <textarea
        style={{ width: "100%", height: 380, fontFamily: "var(--font-mono)", fontSize: 12, whiteSpace: "pre" }}
        value={yaml}
        onChange={(e) => setYaml(e.target.value)}
        aria-label="Configuration YAML"
        spellCheck={false}
      />
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button onClick={() => void save()} disabled={busy} style={{ background: "var(--accent)", color: "var(--text-inverse)", padding: "6px 16px" }}>
          Save configuration
        </button>
        <button onClick={() => void load()} style={{ border: "1px solid var(--border)", padding: "6px 16px" }}>
          Reload
        </button>
      </div>
    </div>
  );
}
