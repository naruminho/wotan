/** Monaco adapter - primary editor. Workers are bundled locally via Vite
 * (?worker imports); nothing is fetched from a CDN. */

import type { EditorAdapter, EditorKind, EditorTheme } from "./EditorAdapter";

let monacoPromise: Promise<typeof import("monaco-editor") | null> | null = null;

async function loadMonaco(): Promise<typeof import("monaco-editor") | null> {
  if (!monacoPromise) {
    monacoPromise = (async () => {
      try {
        const monaco = await import("monaco-editor");
        const EditorWorker = (await import("monaco-editor/esm/vs/editor/editor.worker?worker")).default;
        const JsonWorker = (await import("monaco-editor/esm/vs/language/json/json.worker?worker")).default;
        const CssWorker = (await import("monaco-editor/esm/vs/language/css/css.worker?worker")).default;
        const HtmlWorker = (await import("monaco-editor/esm/vs/language/html/html.worker?worker")).default;
        const TsWorker = (await import("monaco-editor/esm/vs/language/typescript/ts.worker?worker")).default;
        (self as any).MonacoEnvironment = {
          getWorker(_id: string, label: string) {
            if (label === "json") return new JsonWorker();
            if (label === "css" || label === "scss" || label === "less") return new CssWorker();
            if (label === "html" || label === "handlebars" || label === "razor") return new HtmlWorker();
            if (label === "typescript" || label === "javascript") return new TsWorker();
            return new EditorWorker();
          },
        };
        return monaco;
      } catch (err) {
        console.warn("[wotan] Monaco failed to load - falling back to CodeMirror", err);
        return null;
      }
    })();
  }
  return monacoPromise;
}

export async function tryCreateMonaco(): Promise<EditorAdapter | null> {
  const monaco = await loadMonaco();
  if (!monaco) return null;
  return new MonacoAdapter(monaco);
}

class MonacoAdapter implements EditorAdapter {
  readonly kind: EditorKind = "monaco";
  private monaco: typeof import("monaco-editor");
  private editor: import("monaco-editor").editor.IStandaloneCodeEditor | null = null;
  private changeCb: ((v: string) => void) | null = null;
  private host: HTMLElement | null = null;

  constructor(monaco: typeof import("monaco-editor")) {
    this.monaco = monaco;
  }

  mount(host: HTMLElement): void {
    this.host = host;
    this.editor = this.monaco.editor.create(host, {
      value: "",
      language: "plaintext",
      theme: "vs-dark",
      automaticLayout: true,
      minimap: { enabled: false },
      fontSize: 12.5,
      fontFamily: "Cascadia Mono, Consolas, Courier New, monospace",
      scrollBeyondLastLine: false,
      renderWhitespace: "selection",
      padding: { top: 6 },
    });
    this.editor.onDidChangeModelContent(() => {
      this.changeCb?.(this.editor!.getValue());
    });
  }

  setValue(text: string): void {
    if (!this.editor) return;
    const pos = this.editor.getPosition();
    this.editor.setValue(text);
    if (pos) this.editor.setPosition(pos);
  }

  getValue(): string {
    return this.editor?.getValue() ?? "";
  }

  setLanguage(language: string): void {
    if (!this.editor) return;
    const model = this.editor.getModel();
    if (model) this.monaco.editor.setModelLanguage(model, language);
  }

  setTheme(theme: EditorTheme): void {
    this.monaco.editor.setTheme(theme === "dark" ? "vs-dark" : "vs");
  }

  setReadOnly(readOnly: boolean): void {
    this.editor?.updateOptions({ readOnly });
  }

  onChange(cb: (value: string) => void): void {
    this.changeCb = cb;
  }

  revealLine(line: number): void {
    this.editor?.revealLineInCenter(line);
    this.editor?.setPosition({ lineNumber: line, column: 1 });
  }

  find(): void {
    this.editor?.getAction("actions.find")?.run();
  }

  focus(): void {
    this.editor?.focus();
  }

  dispose(): void {
    this.editor?.dispose();
    this.editor = null;
  }
}
