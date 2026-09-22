/** Editor factory with automatic fallback:
 *  Monaco -> CodeMirror 6 -> textarea. */

import type { EditorAdapter } from "./EditorAdapter";
import { tryCreateCodeMirror } from "./CodeMirrorAdapter";
import { tryCreateMonaco } from "./MonacoAdapter";
import { createTextarea } from "./TextareaAdapter";

export async function createEditor(): Promise<EditorAdapter> {
  const monaco = await tryCreateMonaco();
  if (monaco) return monaco;
  const cm = await tryCreateCodeMirror();
  if (cm) return cm;
  return createTextarea();
}

export type { EditorAdapter } from "./EditorAdapter";
