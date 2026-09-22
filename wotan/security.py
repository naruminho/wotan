"""Security helpers: secret scanning, sensitive paths, emoji/mojibake policy.

Everything that comes from tools, web pages or command output is DATA, never
instructions - see :mod:`wotan.agent.prompts` for the prompt-injection rules.
This module is about content safety: never leaking credentials and never
producing files that break Windows terminals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Secret scanning
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b")),
    ("bearer_header", re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._\-]{8,}")),
    ("generic_api_key", re.compile(r"(?i)\b(?:api[_-]?key|apikey|client[_-]?secret|access[_-]?token)\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("password_assignment", re.compile(r"(?i)\b(?:password|passwd|pwd)\b[\"']?\s*[:=]\s*[\"']([^\"'\s]{4,})[\"']")),
]


@dataclass
class SecretFinding:
    kind: str
    excerpt: str
    line: int


def scan_secrets(text: str) -> list[SecretFinding]:
    """Return findings for credential-looking content (for redaction/guarding)."""
    findings: list[SecretFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for kind, pat in _SECRET_PATTERNS:
            m = pat.search(line)
            if m:
                excerpt = line.strip()
                if len(excerpt) > 60:
                    excerpt = excerpt[:57] + "..."
                findings.append(SecretFinding(kind=kind, excerpt=excerpt, line=line_no))
    return findings


def contains_secret(text: str) -> bool:
    return bool(scan_secrets(text))


SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "id_rsa",
    "id_ed25519",
    "known_hosts",
    "secrets.yaml",
    "secrets.yml",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"}


def is_sensitive_path(path: Path | str) -> bool:
    """.env files, private keys and similar must never enter the model's context
    without explicit user approval."""
    p = Path(path)
    name = p.name.lower()
    if name in SENSITIVE_FILENAMES or name.startswith(".env"):
        return True
    if p.suffix.lower() in SENSITIVE_SUFFIXES:
        return True
    if ".ssh" in [part.lower() for part in p.parts]:
        return True
    return False


# ---------------------------------------------------------------------------
# Emoji policy (Windows terminals with cp1252/cp850 render these as garbage)
# ---------------------------------------------------------------------------

# Decorative Unicode symbols forbidden in code/logs/commits by default.
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # emoji blocks
    "\U00002600-\U000027BF"  # misc symbols + dingbats (incl. checkmarks, arrows)
    "\U0001F000-\U0001F0FF"
    "\U00002B00-\U00002BFF"
    "\uFE0F"  # variation selector-16
    "\u200D"  # ZWJ
    "]+",
    re.UNICODE,
)
ASCII_SUGGESTIONS = {
    "✅": "[OK]",
    "✔": "[OK]",
    "❌": "[ERROR]",
    "⚠": "[WARN]",
    "⚠️": "[WARN]",
    "🚀": "->",
    "➜": "->",
    "→": "->",
    "✨": "*",
    "🎉": "*",
    "💡": "note:",
}


@dataclass
class EmojiFinding:
    char: str
    line: int
    suggestion: str


def scan_emoji(text: str, allow: frozenset[str] = frozenset()) -> list[EmojiFinding]:
    findings: list[EmojiFinding] = []
    for line_no, line in enumerate(text.splitlines(), start=1):
        for m in EMOJI_PATTERN.finditer(line):
            ch = m.group(0)
            if ch in allow or all(c in allow for c in ch):
                continue
            suggestion = ASCII_SUGGESTIONS.get(ch) or ASCII_SUGGESTIONS.get(ch[:1]) or "[ASCII alternative]"
            findings.append(EmojiFinding(char=ch, line=line_no, suggestion=suggestion))
    return findings


def suggest_ascii(text: str, allow: frozenset[str] = frozenset()) -> str:
    """Best-effort replacement of decorative symbols with ASCII."""
    def repl(m: re.Match[str]) -> str:
        ch = m.group(0)
        if ch in allow:
            return ch
        return ASCII_SUGGESTIONS.get(ch) or ASCII_SUGGESTIONS.get(ch[:1]) or ""

    return EMOJI_PATTERN.sub(repl, text)


# ---------------------------------------------------------------------------
# Mojibake (UTF-8 bytes decoded as cp1252/latin-1 and back)
# ---------------------------------------------------------------------------

_MOJIBAKE_HINTS = ("Ã©", "Ã§", "Ã£", "Ã¡", "Ã­", "Ã³", "Ãº", "Ãµ", "Ãª", "â€™", "â€œ", "â€", "Ã\x89", "Ã\x87")


def looks_like_mojibake(text: str) -> bool:
    return any(h in text for h in _MOJIBAKE_HINTS)


def fix_mojibake(text: str) -> tuple[str, bool]:
    """Repair unambiguous mojibake (round trip latin-1 -> utf-8).

    Returns ``(fixed, changed)``. If the round trip is not possible the text is
    returned unchanged (ambiguous cases are reported to the model instead).
    """
    if not looks_like_mojibake(text):
        return text, False
    try:
        fixed = text.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            fixed = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text, False
    if fixed == text or looks_like_mojibake(fixed):
        return text, False
    return fixed, True


@dataclass
class ContentScan:
    """Combined content report used by post-edit validation."""

    secrets: list[SecretFinding] = field(default_factory=list)
    emoji: list[EmojiFinding] = field(default_factory=list)
    mojibake: bool = False


def scan_content(text: str, emoji_allow: frozenset[str] = frozenset()) -> ContentScan:
    return ContentScan(
        secrets=scan_secrets(text),
        emoji=scan_emoji(text, emoji_allow),
        mojibake=looks_like_mojibake(text),
    )
