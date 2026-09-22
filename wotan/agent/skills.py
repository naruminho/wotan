"""Skills compatible with the open Agent Skills standard (agentskills.io):
a folder with SKILL.md (frontmatter: name, description) and optional scripts/,
references/, assets/ subfolders.

Three-phase loading: only name+description in context; the full SKILL.md when
relevant; scripts/references only at execution time. Skills live OUTSIDE the
repository by default (~/.wotan/skills, workspace .wotan/skills).

The platform profile (internal gateway knowledge) is a private skill; the
repository ships only a template with placeholders.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..paths import skills_dirs


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    scripts: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    loaded: bool = False

    def load_full(self) -> str:
        self.loaded = True
        return self.body


_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_skill_md(text: str, path: Path) -> Skill | None:
    m = _FRONTMATTER.match(text)
    if not m:
        return None
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return None
    if not isinstance(fm, dict) or "name" not in fm:
        return None
    body = text[m.end() :]
    skill = Skill(
        name=str(fm.get("name", path.parent.name)),
        description=str(fm.get("description", "")),
        path=path.parent,
        body=body.strip(),
        frontmatter=fm,
    )
    sdir = path.parent / "scripts"
    if sdir.is_dir():
        skill.scripts = [str(p.relative_to(path.parent)) for p in sorted(sdir.rglob("*")) if p.is_file()]
    rdir = path.parent / "references"
    if rdir.is_dir():
        skill.references = [str(p.relative_to(path.parent)) for p in sorted(rdir.rglob("*")) if p.is_file()]
    return skill


class SkillRegistry:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = workspace

    def scan(self) -> list[Skill]:
        skills: list[Skill] = []
        seen: set[str] = set()
        for d in skills_dirs(self.workspace):
            if not d.is_dir():
                continue
            for skill_md in sorted(d.rglob("SKILL.md")):
                try:
                    text = skill_md.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                skill = parse_skill_md(text, skill_md)
                if skill and skill.name not in seen:
                    seen.add(skill.name)
                    skills.append(skill)
        return skills

    def find(self, name: str) -> Skill | None:
        for s in self.scan():
            if s.name == name or s.name.replace("_", "-") == name.replace("_", "-"):
                return s
        return None

    def find_by_alias(self, text: str) -> Skill | None:
        """Recognize platform aliases mentioned in chat (e.g. 'use <platform>')."""
        for s in self.scan():
            aliases = s.frontmatter.get("aliases") or []
            names = [s.name] + ([aliases] if isinstance(aliases, str) else list(aliases))
            for n in names:
                if n and re.search(rf"\b{re.escape(str(n))}\b", text, re.IGNORECASE):
                    return s
        return None

    def listing_for_prompt(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "description": s.description, "has_scripts": bool(s.scripts)} for s in self.scan()]

    def maybe_propose_save(self, session_summary: str) -> dict[str, Any] | None:
        """After a successful new kind of task, propose saving it as a skill."""
        return {
            "proposal": "save_skill",
            "suggested_name": "new-skill",
            "body_template": (
                "---\nname: new-skill\ndescription: When to use this skill (one sentence)\n---\n\n"
                "# Steps\n1. ...\n2. ...\n\n# Verification\n- command(s) that prove it worked\n"
            ),
            "note": "Ask the user to approve saving this procedure as a reusable skill (SKILL.md + scripts/).",
        }
