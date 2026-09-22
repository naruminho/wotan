/** CodeMirror 6 adapter - automatic fallback when Monaco fails to load. */

import type { EditorAdapter, EditorKind, EditorTheme } from "./EditorAdapter";

export async function tryCreateCodeMirror(): Promise<EditorAdapter | null> {
  try {
    const viewMod: any = await import("@codemirror/view");
    const stateMod: any = await import("@codemirror/state");
    const { keymap, EditorView } = viewMod;
    const { Compartment, EditorState } = stateMod;
    const { defaultKeymap, history, historyKeymap, indentWithTab } = await import("@codemirror/commands");
    const { oneDark } = await import("@codemirror/theme-one-dark");
    const { javascript } = await import("@codemirror/lang-javascript");
    const { python } = await import("@codemirror/lang-python");
    const { json } = await import("@codemirror/lang-json");
    const { markdown } = await import("@codemirror/lang-markdown");
    return new CodeMirrorAdapter({ EditorView, Compartment, EditorState, keymap, defaultKeymap, history, historyKeymap, indentWithTab, oneDark, javascript, python, json, markdown });
  } catch (err) {
    console.warn("[wotan] CodeMirror failed to load - falling back to textarea", err);
    return null;
  }
}

class CodeMirrorAdapter implements EditorAdapter {
  readonly kind: EditorKind = "codemirror";
  private deps: any;
  private view: any = null;
  private changeCb: ((v: string) => void) | null = null;
  private langComp: any;
  private themeComp: any;
  private readOnlyComp: any;
  private host: HTMLElement | null = null;

  constructor(deps: any) {
    this.deps = deps;
    this.langComp = new deps.Compartment();
    this.themeComp = new deps.Compartment();
    this.readOnlyComp = new deps.Compartment();
  }

  mount(host: HTMLElement): void {
    this.host = host;
    const { EditorView, EditorState, keymap, defaultKeymap, history, historyKeymap, indentWithTab } = this.deps;
    this.view = new EditorView({
      state: EditorState.create({
        doc: "",
        extensions: [
          history(),
          keymap.of([...defaultKeymap, ...historyKeymap, indentWithTab]),
          EditorView.updateListener.of((u: any) => {
            if (u.docChanged) this.changeCb?.(u.state.doc.toString());
          }),
          this.langComp.of([]),
          this.themeComp.of([]),
          this.readOnlyComp.of([]),
          EditorView.lineWrapping,
        ],
      }),
      parent: host,
    });
  }

  private langExt(language: string): any {
    const { javascript, python, json, markdown } = this.deps;
    switch (language) {
      case "python":
        return python();
      case "javascript":
      case "typescript":
        return javascript({ typescript: language === "typescript" });
      case "json":
        return json();
      case "markdown":
        return markdown();
      default:
        return [];
    }
  }

  setValue(text: string): void {
    if (!this.view) return;
    this.view.dispatch({ changes: { from: 0, to: this.view.state.doc.length, insert: text } });
  }

  getValue(): string {
    return this.view ? this.view.state.doc.toString() : "";
  }

  setLanguage(language: string): void {
    if (!this.view) return;
    this.view.dispatch({ effects: this.langComp.reconfigure(this.langExt(language)) });
  }

  setTheme(theme: EditorTheme): void {
    if (!this.view) return;
    this.view.dispatch({ effects: this.themeComp.reconfigure(theme === "dark" ? this.deps.oneDark : []) });
  }

  setReadOnly(readOnly: boolean): void {
    if (!this.view) return;
    this.view.dispatch({ effects: this.readOnlyComp.reconfigure(this.deps.EditorView.editable.of(!readOnly)) });
  }

  onChange(cb: (value: string) => void): void {
    this.changeCb = cb;
  }

  revealLine(line: number): void {
    if (!this.view) return;
    const doc = this.view.state.doc;
    const pos = Math.min(doc.line(Math.min(line, doc.lines)).from, doc.length);
    this.view.dispatch({ selection: { anchor: pos }, scrollIntoView: true });
  }

  find(): void {
    // CodeMirror has no built-in find panel in the core; use the browser's.
    this.focus();
  }

  focus(): void {
    this.view?.focus();
  }

  dispose(): void {
    this.view?.destroy();
    this.view = null;
  }
}
