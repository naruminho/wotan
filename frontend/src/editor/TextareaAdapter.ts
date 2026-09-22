/** Textarea adapter - final fallback: a textarea with line numbers. Never
 * fails to load (no dependencies). */

import type { EditorAdapter, EditorKind, EditorTheme } from "./EditorAdapter";

export function createTextarea(): EditorAdapter {
  return new TextareaAdapter();
}

class TextareaAdapter implements EditorAdapter {
  readonly kind: EditorKind = "textarea";
  private container: HTMLDivElement | null = null;
  private textarea: HTMLTextAreaElement | null = null;
  private gutter: HTMLDivElement | null = null;
  private changeCb: ((v: string) => void) | null = null;
  private theme: EditorTheme = "dark";

  mount(host: HTMLElement): void {
    this.container = document.createElement("div");
    this.container.style.cssText = "display:flex;height:100%;font-family:var(--font-mono);font-size:12.5px;";
    this.gutter = document.createElement("div");
    this.gutter.style.cssText = "padding:6px 8px;text-align:right;user-select:none;color:var(--text-dim);overflow:hidden;white-space:pre;background:var(--bg-panel);min-width:40px;";
    this.textarea = document.createElement("textarea");
    this.textarea.setAttribute("aria-label", "Code editor (fallback)");
    this.textarea.style.cssText = "flex:1;border:none;border-radius:0;resize:none;padding:6px 8px;outline:none;font:inherit;background:var(--bg);color:var(--text);white-space:pre;overflow:auto;";
    this.textarea.addEventListener("input", () => {
      this.renderGutter();
      this.changeCb?.(this.textarea!.value);
    });
    this.textarea.addEventListener("scroll", () => {
      if (this.gutter && this.textarea) this.gutter.scrollTop = this.textarea.scrollTop;
    });
    this.container.append(this.gutter, this.textarea);
    host.append(this.container);
  }

  private renderGutter(): void {
    if (!this.gutter || !this.textarea) return;
    const lines = this.textarea.value.split("\n").length;
    let out = "";
    for (let i = 1; i <= lines; i++) out += i + "\n";
    this.gutter.textContent = out;
  }

  setValue(text: string): void {
    if (!this.textarea) return;
    this.textarea.value = text;
    this.renderGutter();
  }

  getValue(): string {
    return this.textarea?.value ?? "";
  }

  setLanguage(): void {
    /* plain textarea: no highlighting */
  }

  setTheme(theme: EditorTheme): void {
    this.theme = theme;
  }

  setReadOnly(readOnly: boolean): void {
    if (this.textarea) this.textarea.readOnly = readOnly;
  }

  onChange(cb: (value: string) => void): void {
    this.changeCb = cb;
  }

  revealLine(line: number): void {
    if (!this.textarea) return;
    const lines = this.textarea.value.split("\n");
    let pos = 0;
    for (let i = 0; i < Math.min(line - 1, lines.length); i++) pos += lines[i].length + 1;
    this.textarea.focus();
    this.textarea.setSelectionRange(pos, pos);
    this.textarea.scrollTop = Math.max(0, (line - 5) * 18);
  }

  find(): void {
    this.focus();
  }

  focus(): void {
    this.textarea?.focus();
  }

  dispose(): void {
    this.container?.remove();
    this.container = null;
    this.textarea = null;
    this.gutter = null;
  }
}
