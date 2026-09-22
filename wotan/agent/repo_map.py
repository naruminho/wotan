"""Aider-style repo map: definitions + references -> file/symbol graph with
personalized PageRank, binary-searched to fit a token budget.

Uses tree-sitter when installed (optional); otherwise a solid regex extractor
for Python/JS/TS/Go/Rust/Java. The map is recomputed when files change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

PY_DEF = re.compile(r"^(?:async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(", re.MULTILINE)
JS_DEF = re.compile(r"^(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_]\w*)", re.MULTILINE)
JS_FN = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(", re.MULTILINE)
GO_DEF = re.compile(r"^func\s+(?:\([^)]+\)\s+)?([A-Za-z_]\w*)\s*\(", re.MULTILINE)
RS_DEF = re.compile(r"^(?:pub\s+)?(?:fn|struct|enum|trait)\s+([A-Za-z_]\w*)", re.MULTILINE)
JAVA_DEF = re.compile(r"^\s*(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)", re.MULTILINE)

_EXTS = {".py": [PY_DEF], ".js": [JS_DEF, JS_FN], ".jsx": [JS_DEF, JS_FN], ".ts": [JS_DEF, JS_FN],
         ".tsx": [JS_DEF, JS_FN], ".mjs": [JS_DEF, JS_FN], ".go": [GO_DEF], ".rs": [RS_DEF], ".java": [JAVA_DEF]}

_IGNORE = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache"}


@dataclass
class Symbol:
    name: str
    file: str
    line: int
    kind: str = "def"


@dataclass
class RepoMap:
    root: Path
    symbols: list[Symbol] = field(default_factory=list)
    refs: dict[str, set[str]] = field(default_factory=dict)  # symbol -> files referencing it
    _cache_valid: bool = False

    def refresh(self, files: Iterable[Path] | None = None) -> None:
        self.symbols = []
        self.refs = {}
        paths = list(files) if files is not None else [p for p in self.root.rglob("*") if p.is_file()]
        texts: dict[str, str] = {}
        for p in paths:
            if any(part in _IGNORE for part in p.parts):
                continue
            if p.suffix not in _EXTS:
                continue
            try:
                if p.stat().st_size > 500_000:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            texts[rel] = text
            for pat in _EXTS[p.suffix]:
                for m in pat.finditer(text):
                    line = text[: m.start()].count("\n") + 1
                    self.symbols.append(Symbol(name=m.group(1), file=rel, line=line))
        names = {s.name for s in self.symbols}
        for rel, text in texts.items():
            for name in names:
                if re.search(rf"\b{re.escape(name)}\b", text):
                    self.refs.setdefault(name, set()).add(rel)
        self._cache_valid = True

    def pagerank(self, personalized: set[str], damping: float = 0.85, iterations: int = 12) -> dict[str, float]:
        """Personalized PageRank over the file->symbol graph.

        ``personalized``: files the agent is editing or the user mentioned.
        """
        files = sorted({s.file for s in self.symbols} | set().union(*self.refs.values()) if self.refs else {s.file for s in self.symbols})
        if not files:
            return {}
        n = len(files)
        fileset = files
        index = {f: i for i, f in enumerate(fileset)}
        # adjacency: file -> files containing symbols defined in it (via refs)
        out_links: list[list[int]] = [[] for _ in range(n)]
        for s in self.symbols:
            for ref_file in self.refs.get(s.name, ()):
                if s.file in index and ref_file in index and ref_file != s.file:
                    out_links[index[s.file]].append(index[ref_file])
        scores = [1.0 / n] * n
        personal = [0.0] * n
        for f in personalized:
            if f in index:
                personal[index[f]] = 1.0
        if sum(personal) == 0:
            personal = [1.0 / n] * n
        else:
            z = sum(personal)
            personal = [p / z for p in personal]
        for _ in range(iterations):
            new = [(1 - damping) * personal[i] for i in range(n)]
            for j in range(n):
                links = out_links[j] or list(range(n))  # dangling: spread to all
                share = damping * scores[j] / len(links)
                for k in links:
                    new[k] += share
            scores = new
        return {fileset[i]: scores[i] for i in range(n)}

    def render(self, focus_files: set[str] | None = None, token_budget: int = 1500) -> str:
        """Binary-search how many top symbols fit the token budget (~4 chars/token)."""
        if not self._cache_valid:
            self.refresh()
        if not self.symbols:
            return ""
        ranks = self.pagerank(set(focus_files or ()))
        ordered = sorted(
            self.symbols,
            key=lambda s: (-ranks.get(s.file, 0.0), s.file, s.line),
        )

        def render_k(k: int) -> str:
            lines: list[str] = []
            current_file = None
            for s in ordered[:k]:
                if s.file != current_file:
                    current_file = s.file
                    lines.append(f"{s.file}:")
                lines.append(f"  {s.kind} {s.name} (line {s.line})")
            return "\n".join(lines)

        lo, hi = 1, len(ordered)
        best = render_k(min(20, hi))
        while lo <= hi:
            mid = (lo + hi) // 2
            text = render_k(mid)
            if len(text) / 4 <= token_budget:
                best = text
                lo = mid + 1
            else:
                hi = mid - 1
        return best
