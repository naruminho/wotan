/** Markdown rendering for chat (marked + highlight.js, both bundled). */

import { marked } from "marked";
import hljs from "highlight.js/lib/common";

marked.setOptions({ gfm: true, breaks: true });

const renderer = new marked.Renderer();

// Code blocks with copy/apply actions are added by the React layer around
// <pre><code>; here we only ensure language classes for highlighting.
renderer.code = (code: any, language: any) => {
  const text = typeof code === "object" && code !== null ? code.text : String(code ?? "");
  const lang = typeof code === "object" && code !== null ? code.lang : language;
  return `<pre data-lang="${lang || ""}"><code class="language-${lang || "plaintext"}">${escapeHtml(text)}</code></pre>`;
};

export function renderMarkdown(src: string): string {
  try {
    return marked.parse(src, { renderer }) as string;
  } catch {
    return escapeHtml(src);
  }
}

export function highlightCode(code: string, lang: string): string {
  try {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  } catch {
    return escapeHtml(code);
  }
}

export function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
