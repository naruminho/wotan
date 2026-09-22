import React, { useEffect, useState } from "react";
import { api } from "../api/client";
import { useStore } from "../state/store";

/** Assistant extras: skills, inbox (scheduled results), scheduler, experiments. */
export default function AssistantPanel() {
  const setToast = useStore((s) => s.setToast);
  const openMemory = useStore((s) => s.openMemory);
  const sendPrompt = useStore((s) => s.sendPrompt);
  const [skills, setSkills] = useState<any[]>([]);
  const [inbox, setInbox] = useState<any[]>([]);
  const [tasks, setTasks] = useState<any[]>([]);
  const [experiments, setExperiments] = useState<any[]>([]);
  const [when, setWhen] = useState("daily 09:00");
  const [prompt, setPrompt] = useState("");

  const load = () => {
    void api.skills().then((r) => setSkills(r.skills || []));
    void api.inbox().then((r) => setInbox(r.items || []));
    void api.scheduler().then((r) => setTasks(r.tasks || []));
    void api.experiments().then((r) => setExperiments(r.experiments || []));
  };

  useEffect(load, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <section>
        <div className="sidebar-header" style={{ padding: "4px 0" }}>Memory</div>
        <button className="tree-item" onClick={openMemory}>▤ Open persistent memory (markdown)</button>
      </section>

      <section>
        <div className="sidebar-header" style={{ padding: "4px 0" }}>Skills</div>
        {skills.length === 0 && <div style={{ color: "var(--text-dim)", fontSize: 12 }}>No skills yet. The agent proposes skills after finishing new kinds of tasks.</div>}
        {skills.map((s) => (
          <div className="list-row" key={s.name} title={s.description}>
            <span className="badge">{s.name}</span>
            <span style={{ fontSize: 11.5, color: "var(--text-dim)", overflow: "hidden", textOverflow: "ellipsis" }}>{s.description}</span>
          </div>
        ))}
      </section>

      <section>
        <div className="sidebar-header" style={{ padding: "4px 0" }}>Inbox</div>
        {inbox.length === 0 && <div style={{ color: "var(--text-dim)", fontSize: 12 }}>No messages.</div>}
        {inbox.map((m) => (
          <div className="list-row" key={m.id} style={{ alignItems: "flex-start" }}>
            <div>
              <div style={{ fontWeight: 600 }}>{m.title}</div>
              <div style={{ fontSize: 11.5, color: "var(--text-dim)" }}>{m.body}</div>
            </div>
          </div>
        ))}
      </section>

      <section>
        <div className="sidebar-header" style={{ padding: "4px 0" }}>Scheduled tasks</div>
        <input style={{ width: "100%", marginBottom: 4 }} placeholder="What should run?" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        <div style={{ display: "flex", gap: 4 }}>
          <input style={{ flex: 1 }} value={when} onChange={(e) => setWhen(e.target.value)} title="e.g. 'in 10 minutes', 'every 2h', 'daily 09:30'" />
          <button
            onClick={() => {
              if (!prompt.trim()) return;
              void api.addScheduled(prompt.slice(0, 40), prompt, when).then((r) => {
                setToast(r.ok ? "Scheduled" : r.error || "Failed");
                setPrompt("");
                load();
              });
            }}
          >
            add
          </button>
        </div>
        {tasks.map((t) => (
          <div className="list-row" key={t.id}>
            <span className="badge">{t.when}</span>
            <span style={{ fontSize: 11.5, flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{t.name}</span>
            <button
              title="Delete"
              onClick={() => void api.cancelScheduled(t.id).then(load)}
            >
              ×
            </button>
          </div>
        ))}
      </section>

      <section>
        <div className="sidebar-header" style={{ padding: "4px 0" }}>Experiments</div>
        {experiments.length === 0 && (
          <div style={{ color: "var(--text-dim)", fontSize: 12 }}>
            None yet. Ask the agent for a generative AI experiment (e.g. "extract entities from a PDF").
          </div>
        )}
        {experiments.map((x) => (
          <div className="list-row" key={x.name}>
            <span style={{ fontSize: 11.5, flex: 1 }}>{x.name}</span>
            {x.running ? (
              <>
                <button
                  title="Open preview"
                  onClick={() => window.open(x.preview, "_blank")}
                >
                  preview
                </button>
                <button title="Stop" onClick={() => void api.stopExperiment(x.name).then(load)}>
                  stop
                </button>
              </>
            ) : (
              <button
                title="Run"
                onClick={() =>
                  void api.runExperiment(x.name).then((r) => {
                    setToast(r.ok ? `Running on port ${r.port} (preview proxied)` : r.error || "Failed");
                    load();
                  })
                }
              >
                run
              </button>
            )}
          </div>
        ))}
        <button
          className="tree-item"
          onClick={() => sendPrompt("Create a new generative AI experiment using the internal platform skill and the experiment templates, then test it against the mock gateway.")}
        >
          ✦ Ask the agent for a new experiment
        </button>
      </section>
    </div>
  );
}
