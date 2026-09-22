import React, { useEffect, useRef, useState } from "react";
import { terminalSocket } from "../api/client";

interface TermTab {
  id: string; // local id until the backend assigns one
  label: string;
  api: ReturnType<typeof terminalSocket> | null;
  el: HTMLDivElement | null;
  term: any;
}

/** Integrated terminal: xterm.js over a backend PTY (ConPTY on Windows).
 *  Multiple instances via tabs. */
export default function TerminalPanel() {
  const [terms, setTerms] = useState<TermTab[]>([]);
  const [activeIdx, setActiveIdx] = useState(0);
  const hostRef = useRef<HTMLDivElement>(null);
  const fitRef = useRef<any>(null);
  const termsRef = useRef<TermTab[]>([]);
  termsRef.current = terms;

  const spawn = async () => {
    const [{ Terminal }, { FitAddon }] = await Promise.all([
      import("@xterm/xterm"),
      import("@xterm/addon-fit"),
    ]);
    await import("@xterm/xterm/css/xterm.css");
    const el = document.createElement("div");
    el.className = "terminal-host";
    el.style.height = "100%";
    const term = new Terminal({
      fontFamily: "Cascadia Mono, Consolas, Courier New, monospace",
      fontSize: 12.5,
      theme: {
        background: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#1a1b26",
        foreground: getComputedStyle(document.documentElement).getPropertyValue("--text").trim() || "#c0caf5",
      },
      convertEol: false,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    const localId = `local-${Date.now()}`;
    const tab: TermTab = { id: localId, label: `shell ${terms.length + 1}`, api: null, el, term };
    const sock = terminalSocket(
      localId,
      (data) => term.write(data),
      (backendId) => {
        tab.id = backendId;
        setTerms([...termsRef.current]);
      },
    );
    tab.api = sock;
    term.onData((d: string) => sock.input(d));
    setTerms((prev) => {
      const next = [...prev, tab];
      setActiveIdx(next.length - 1);
      return next;
    });
    // mount after state update
    setTimeout(() => {
      if (hostRef.current && el.parentElement !== hostRef.current) {
        hostRef.current.innerHTML = "";
        hostRef.current.append(el);
        term.open(el);
        try {
          fit.fit();
          sock.resize(term.rows, term.cols);
        } catch {
          /* first paint may not be measurable yet */
        }
        fitRef.current = fit;
        term.focus();
      }
    }, 0);
  };

  useEffect(() => {
    void spawn();
    const onResize = () => {
      try {
        fitRef.current?.fit();
      } catch {
        /* ignore */
      }
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      for (const t of termsRef.current) {
        t.api?.kill();
        t.term?.dispose();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const tab = terms[activeIdx];
    if (tab && hostRef.current && tab.el && tab.el.parentElement === null) {
      hostRef.current.innerHTML = "";
      hostRef.current.append(tab.el);
      tab.term.open(tab.el);
      try {
        fitRef.current = null;
        tab.term.focus();
      } catch {
        /* ignore */
      }
    } else if (tab && tab.el && hostRef.current && tab.el.parentElement !== hostRef.current) {
      hostRef.current.innerHTML = "";
      hostRef.current.append(tab.el);
    }
  }, [activeIdx, terms]);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", gap: 4, padding: "2px 6px", borderBottom: "1px solid var(--border)" }}>
        {terms.map((t, i) => (
          <button
            key={t.id}
            className={i === activeIdx ? "active" : ""}
            style={i === activeIdx ? { background: "var(--bg-active)" } : {}}
            onClick={() => setActiveIdx(i)}
            title={t.label}
          >
            {t.label}
          </button>
        ))}
        <button title="New terminal" onClick={() => void spawn()}>+</button>
        <button
          title="Kill terminal"
          onClick={() => {
            const t = terms[activeIdx];
            t?.api?.kill();
            t?.term?.dispose();
            const next = terms.filter((_, i) => i !== activeIdx);
            setTerms(next);
            setActiveIdx(Math.max(0, activeIdx - 1));
          }}
        >
          ×
        </button>
      </div>
      <div ref={hostRef} style={{ flex: 1, minHeight: 0 }} data-testid="terminal-host" />
    </div>
  );
}
