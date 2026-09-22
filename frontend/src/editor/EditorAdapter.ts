/** EditorAdapter: the abstract editor interface.
 *
 * Implementations: MonacoAdapter (primary), CodeMirrorAdapter (fallback when
 * Monaco fails to load), TextareaAdapter (final fallback). Nothing outside this
 * folder knows which one is active.
 */

export type EditorKind = "monaco" | "codemirror" | "textarea";
export type EditorTheme = "dark" | "light";

export interface EditorAdapter {
  readonly kind: EditorKind;
  mount(host: HTMLElement): void;
  setValue(text: string): void;
  getValue(): string;
  setLanguage(language: string): void;
  setTheme(theme: EditorTheme): void;
  setReadOnly(readOnly: boolean): void;
  onChange(cb: (value: string) => void): void;
  revealLine(line: number): void;
  find(): void;
  focus(): void;
  dispose(): void;
}

export const LANG_MAP: Record<string, string> = {
  python: "python",
  javascript: "javascript",
  typescript: "typescript",
  json: "json",
  yaml: "yaml",
  markdown: "markdown",
  shell: "shell",
  html: "html",
  css: "css",
  sql: "sql",
  plaintext: "plaintext",
};
