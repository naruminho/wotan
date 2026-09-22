/** Minimal line-based LCS diff (no dependencies) for the side-by-side diff view. */

export interface DiffLine {
  type: "add" | "del" | "ctx" | "meta";
  oldNo: number | null;
  newNo: number | null;
  text: string;
}

export function diffLines(oldText: string, newText: string): DiffLine[] {
  const a = oldText.split("\n");
  const b = newText.split("\n");
  const n = a.length;
  const m = b.length;
  // LCS table (fine for file-sized inputs; the heavy work is off the main thread
  // only for giant files which we open read-only anyway).
  const lcs: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      out.push({ type: "ctx", oldNo: i + 1, newNo: j + 1, text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ type: "del", oldNo: i + 1, newNo: null, text: a[i] });
      i++;
    } else {
      out.push({ type: "add", oldNo: null, newNo: j + 1, text: b[j] });
      j++;
    }
  }
  while (i < n) out.push({ type: "del", oldNo: i++ + 1, newNo: null, text: a[i - 1] });
  while (j < m) out.push({ type: "add", oldNo: null, newNo: j++ + 1, text: b[j - 1] });
  return out;
}

/** Parse a unified diff (as produced by the edit engine) into display rows. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---") || line.startsWith("diff ") || line.startsWith("index ")) {
      out.push({ type: "meta", oldNo: null, newNo: null, text: line });
    } else if (line.startsWith("@@")) {
      out.push({ type: "meta", oldNo: null, newNo: null, text: line });
    } else if (line.startsWith("+")) {
      out.push({ type: "add", oldNo: null, newNo: null, text: line.slice(1) });
    } else if (line.startsWith("-")) {
      out.push({ type: "del", oldNo: null, newNo: null, text: line.slice(1) });
    } else if (line.startsWith(" ")) {
      out.push({ type: "ctx", oldNo: null, newNo: null, text: line.slice(1) });
    } else if (line.trim()) {
      out.push({ type: "meta", oldNo: null, newNo: null, text: line });
    }
  }
  return out;
}
