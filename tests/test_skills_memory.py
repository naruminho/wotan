"""Skills (Agent Skills standard) and persistent memory."""

from __future__ import annotations

from pathlib import Path

from wotan.agent.memory import MemoryStore
from wotan.agent.skills import SkillRegistry, parse_skill_md


SKILL_MD = """---
name: internal-platform
description: Knowledge about the internal generative AI platform and its gateway
aliases: [AcmeAI, the platform]
---

# Platform
Use the configured gateway. Models: ...
"""


def test_parse_skill_md(tmp_path: Path):
    d = tmp_path / "skills" / "internal-platform"
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "discover.py").write_text("# discovery\n", encoding="utf-8")
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    skill = parse_skill_md((d / "SKILL.md").read_text(encoding="utf-8"), d / "SKILL.md")
    assert skill is not None
    assert skill.name == "internal-platform"
    assert "gateway" in skill.description
    assert skill.scripts == ["scripts/discover.py"]
    body = skill.load_full()
    assert "Use the configured gateway" in body


def test_registry_scan_and_alias(ws: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / "skills" / "internal-platform"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    reg = SkillRegistry(ws)
    skills = reg.scan()
    assert [s.name for s in skills] == ["internal-platform"]
    assert reg.find("internal-platform") is not None
    # platform alias recognized in chat text
    assert reg.find_by_alias("please use AcmeAI for this") is not None
    listing = reg.listing_for_prompt()
    assert listing[0]["name"] == "internal-platform"


def test_three_phase_listing_hides_body(ws: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WOTAN_HOME", str(tmp_path / "home"))
    d = tmp_path / "home" / "skills" / "s1"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(SKILL_MD.replace("internal-platform", "s1"), encoding="utf-8")
    reg = SkillRegistry(ws)
    listing = reg.listing_for_prompt()
    text = str(listing)
    assert "Use the configured gateway" not in text  # body not in listing
    skill = reg.find("s1")
    assert "Use the configured gateway" in skill.load_full()


def test_memory_store(tmp_path: Path):
    store = MemoryStore(tmp_path / "mem")
    store.append_fact("user.md", "Preferences", "prefers dark theme")
    store.append_fact("user.md", "Preferences", "works in Python 3.11")
    mf = store.read("user.md")
    assert "dark theme" in mf.content
    assert "Preferences" in mf.summary
    summary = store.summary_for_prompt()
    assert "user.md" in summary
    names = [m.name for m in store.list()]
    assert names == ["user.md"]
