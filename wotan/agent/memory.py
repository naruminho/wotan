"""Persistent memory: markdown files editable from the UI, loaded on demand."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..paths import memory_dir


@dataclass
class MemoryFile:
    name: str
    path: Path
    content: str
    summary: str


_MEMORY_SECTIONS = re.compile(r"^##\s+(.+)$", re.MULTILINE)


class MemoryStore:
    def __init__(self, directory: Path | None = None) -> None:
        self.dir = directory or memory_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def _safe_name(self, name: str) -> str:
        name = re.sub(r"[^\w\-. ]", "", name).strip().replace(" ", "-") or "memory"
        name = name.lstrip(".")  # no hidden files / path tricks
        return name if name.endswith(".md") else f"{name}.md"

    def list(self) -> list[MemoryFile]:
        out: list[MemoryFile] = []
        for f in sorted(self.dir.glob("*.md")):
            content = f.read_text(encoding="utf-8", errors="replace")
            out.append(MemoryFile(name=f.name, path=f, content=content, summary=self._summary(content)))
        return out

    def _summary(self, content: str) -> str:
        heads = _MEMORY_SECTIONS.findall(content)
        if heads:
            return "sections: " + ", ".join(heads[:8])
        first = next((l for l in content.splitlines() if l.strip()), "")
        return first[:120]

    def read(self, name: str) -> MemoryFile | None:
        p = self.dir / self._safe_name(name)
        if not p.is_file():
            return None
        content = p.read_text(encoding="utf-8", errors="replace")
        return MemoryFile(name=p.name, path=p, content=content, summary=self._summary(content))

    def write(self, name: str, content: str) -> Path:
        p = self.dir / self._safe_name(name)
        p.write_text(content, encoding="utf-8")
        return p

    def append_fact(self, name: str, section: str, fact: str) -> Path:
        p = self.dir / self._safe_name(name)
        if p.is_file():
            content = p.read_text(encoding="utf-8", errors="replace")
        else:
            content = f"# {name[:-3] if name.endswith('.md') else name}\n\n## {section}\n"
        if f"## {section}" not in content:
            content += f"\n\n## {section}\n"
        content = content.rstrip() + f"\n- {fact.strip()}\n"
        return self.write(name, content)

    def summary_for_prompt(self, limit_chars: int = 1500) -> str:
        parts = []
        total = 0
        for mf in self.list():
            line = f"- {mf.name}: {mf.summary}"
            total += len(line)
            if total > limit_chars:
                break
            parts.append(line)
        return "\n".join(parts)
