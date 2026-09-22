"""Post-edit validation: the guardrails that stop 'Frankenstein' files.

Checks run after a candidate edit and BEFORE it is written to disk:
 * leftover markers (merge conflicts, SEARCH/REPLACE, textual delimiters, stray
   markdown fences in the middle of code),
 * placeholder truncation ("... rest of the code ...", "// unchanged"),
 * syntax check per language (ast.parse / json / yaml / toml / tree-sitter or
   bracket-balance for JS-TS) - auto-rollback when a valid file turns invalid,
 * diff sanity (deleting large unrequested blocks, suspiciously huge changes),
 * emoji policy (decorative Unicode symbols break Windows terminals),
 * mojibake detection.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field

from ..security import fix_mojibake, looks_like_mojibake, scan_emoji

# ---------------------------------------------------------------------------
# Leftover markers
# ---------------------------------------------------------------------------

_MARKER_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("merge_conflict", re.compile(r"^<{7}( |$)|^={7}$|^>{7}( |$)", re.MULTILINE)),
    ("search_replace_block", re.compile(r"^\s*(<<<<<<<\s*SEARCH|>>>>>>>\s*REPLACE)\s*$", re.MULTILINE)),
    ("apply_patch_markers", re.compile(r"^\*\*\* (Begin|End) Patch\s*$|^\*\*\* (Update|Add|Delete) File:", re.MULTILINE)),
    ("wotan_tool_delimiter", re.compile(r"<<<WOTAN_TOOL(_END)?>>>")),
    ("rest_placeholder", re.compile(r"(?://|#|/\*)\s*\.{0,3}\s*(rest of (the )?code|rest( is)? unchanged|code (below|above) (is )?(unchanged|the same)|existing code|unchanged)\s*\.{0,3}\s*(\*/)?", re.IGNORECASE)),
    ("ellipsis_placeholder", re.compile(r"^\s*\.\.\.\s*(rest|existing|remaining).*$", re.MULTILINE)),
    ("html_comment_placeholder", re.compile(r"<!--\s*\.{0,3}\s*(rest|existing|remaining).{0,40}\s*-->", re.IGNORECASE)),
]

_FENCE_RE = re.compile(r"^```", re.MULTILINE)


@dataclass
class ValidationIssue:
    kind: str
    message: str
    why: str = ""
    how_to_fix: str = ""

    def render(self) -> str:
        parts = [f"ERROR: {self.message}"]
        if self.why:
            parts.append(f"WHY: {self.why}")
        if self.how_to_fix:
            parts.append(f"HOW TO FIX: {self.how_to_fix}")
        return "\n".join(parts)


@dataclass
class ValidationReport:
    ok: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, issue: ValidationIssue) -> None:
        self.ok = False
        self.issues.append(issue)


def scan_markers(text: str) -> list[tuple[str, str]]:
    """Return (kind, excerpt) for every leftover-marker hit."""
    hits: list[tuple[str, str]] = []
    for kind, pat in _MARKER_PATTERNS:
        for m in pat.finditer(text):
            line = text[: m.start()].count("\n") + 1
            excerpt = text.splitlines()[line - 1][:100] if text.splitlines() else m.group(0)[:100]
            hits.append((kind, f"line {line}: {excerpt}"))
    return hits


def check_new_markers(before: str, after: str, language: str = "") -> ValidationReport:
    """Reject edit output that introduces markers/placeholders not there before."""
    report = ValidationReport()
    before_hits = set(scan_markers(before))
    for kind, excerpt in scan_markers(after):
        if (kind, excerpt) in before_hits:
            continue
        # Merge-conflict markers counted per exact line before vs after.
        if kind == "merge_conflict":
            if scan_markers(before) and all(h[0] == "merge_conflict" for h in scan_markers(before)):
                continue
        report.fail(
            ValidationIssue(
                kind=kind,
                message=f"edit output contains a forbidden marker/placeholder ({kind}): {excerpt}",
                why="markers like '<<<<<<<', 'SEARCH/REPLACE', '<<<WOTAN_TOOL>>>' or '... rest of the code ...' left in files break the code",
                how_to_fix="remove the marker and apply the real code; never write placeholders in place of code",
            )
        )
    # Stray markdown fences in the middle of non-markdown code.
    if language not in ("markdown", "md", ""):
        fence_lines = [i for i, l in enumerate(after.splitlines(), 1) if l.startswith("```")]
        if len(fence_lines) % 2 == 1:
            report.fail(
                ValidationIssue(
                    kind="stray_fence",
                    message=f"odd number of ``` fences in {language or 'code'} file (line {fence_lines[-1]})",
                    why="a stray markdown fence in the middle of code means a code block was pasted uncleanly",
                    how_to_fix="remove the ``` line; code files must not contain markdown fences",
                )
            )
    return report


# ---------------------------------------------------------------------------
# Syntax checks
# ---------------------------------------------------------------------------

_JS_BRACKETS = {"(": ")", "[": "]", "{": "}"}


def _balanced_brackets(text: str) -> bool:
    """Crude JS/TS sanity: bracket balance ignoring strings/comments/templates."""
    stack: list[str] = []
    i, n = 0, len(text)
    in_str: str | None = None
    in_line_comment = False
    in_block_comment = False
    prev = ""
    while i < n:
        ch = text[i]
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            if ch == "/" and prev == "*":
                in_block_comment = False
        elif in_str:
            if ch == "\\":
                i += 2
                prev = ""
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch == "/" and i + 1 < n and text[i + 1] == "/":
                in_line_comment = True
            elif ch == "/" and i + 1 < n and text[i + 1] == "*":
                in_block_comment = True
            elif ch in "\"'`":
                in_str = ch
            elif ch in _JS_BRACKETS:
                stack.append(_JS_BRACKETS[ch])
            elif ch in _JS_BRACKETS.values():
                if not stack or stack.pop() != ch:
                    return False
        prev = ch
        i += 1
    return not stack and in_str is None


def check_syntax(text: str, language: str, path: str = "") -> ValidationReport:
    """Validate syntax for known languages. Unknown languages pass with a note.

    Returns report.ok=False only on definite syntax errors (or when a file that
    was valid before became invalid - handled by engine comparing before/after).
    """
    report = ValidationReport()
    lang = (language or "").lower().strip()
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    lang = lang or {
        "py": "python", "pyw": "python", "json": "json", "yaml": "yaml", "yml": "yaml",
        "toml": "toml", "js": "javascript", "jsx": "javascript", "ts": "typescript",
        "tsx": "typescript", "mjs": "javascript", "cjs": "javascript",
    }.get(ext, "")

    def fail(msg: str, why: str, fix: str) -> None:
        report.fail(ValidationIssue(kind="syntax", message=msg, why=why, how_to_fix=fix))

    if lang == "python":
        try:
            ast.parse(text)
        except SyntaxError as exc:
            fail(
                f"Python syntax error: {exc.msg} at line {exc.lineno}",
                "the edited file no longer parses with ast.parse",
                "fix the syntax (unclosed bracket/quote, bad indent, stray token) and retry the edit",
            )
    elif lang == "json":
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            fail(
                f"JSON syntax error: {exc.msg} at line {exc.lineno}",
                "the edited file no longer parses as JSON",
                "fix the JSON (trailing commas, quotes, brackets) and retry",
            )
    elif lang in ("yaml", "yml"):
        try:
            import yaml

            yaml.safe_load(text)
        except Exception as exc:
            fail(
                f"YAML syntax error: {exc}",
                "the edited file no longer parses as YAML",
                "check indentation and ':' spacing in the YAML",
            )
    elif lang == "toml":
        try:
            import tomllib

            tomllib.loads(text)
        except Exception as exc:
            fail(
                f"TOML syntax error: {exc}",
                "the edited file no longer parses as TOML",
                "check quoting and section headers",
            )
    elif lang in ("javascript", "typescript", "js", "ts", "jsx", "tsx"):
        if not _balanced_brackets(text):
            fail(
                "JS/TS bracket balance check failed",
                "brackets/quotes are unbalanced after the edit (unclosed string or block)",
                "close the unbalanced bracket or quote and retry",
            )
    else:
        report.warnings.append(f"no syntax checker for language {lang or 'unknown'} - skipped")
    return report


# ---------------------------------------------------------------------------
# Diff sanity
# ---------------------------------------------------------------------------

def diff_sanity(before: str, after: str, old_string: str, new_string: str, requested_removal: bool = False) -> ValidationReport:
    """Catch runaway rewrites: removal much bigger than requested, truncation."""
    report = ValidationReport()
    before_lines = before.count("\n") + 1
    after_lines = after.count("\n") + 1
    expected_delta = (new_string.count("\n") + 1) - (old_string.count("\n") + 1)
    actual_delta = after_lines - before_lines
    if before_lines > 30 and after_lines < before_lines * 0.5 and not requested_removal:
        report.fail(
            ValidationIssue(
                kind="mass_deletion",
                message=f"edit deleted most of the file ({before_lines} -> {after_lines} lines)",
                why="a small edit should not remove large unrequested blocks",
                how_to_fix="re-run with a smaller old_string/new_string pair covering only the change",
            )
        )
    if before_lines > 30 and abs(actual_delta - expected_delta) > max(20, before_lines // 2):
        report.fail(
            ValidationIssue(
                kind="delta_mismatch",
                message=f"line count changed by {actual_delta}, expected about {expected_delta}",
                why="the change is much larger than the requested edit - the match likely ate too much text",
                how_to_fix="narrow old_string to the exact unique snippet to change",
            )
        )
    # Truncation heuristics: file that used to end cleanly now ends mid-token.
    tail = after.rstrip()[-200:]
    if tail.endswith(("...", "…")) and not before.rstrip().endswith(("...", "…")):
        report.fail(
            ValidationIssue(
                kind="truncated",
                message="edited file now ends with an ellipsis placeholder",
                why="'...' at the end usually means the model cut the file instead of editing it",
                how_to_fix="restore the removed tail and apply a targeted edit_file",
            )
        )
    return report


def check_emoji_policy(text: str, enabled: bool, allow: frozenset[str] = frozenset()) -> ValidationReport:
    report = ValidationReport()
    if not enabled:
        return report
    for f in scan_emoji(text, allow):
        report.fail(
            ValidationIssue(
                kind="emoji",
                message=f"decorative Unicode symbol {f.char!r} on line {f.line}",
                why="emojis and decorative symbols break or render wrong in Windows terminals (cp1252/cp850)",
                how_to_fix=f"replace with ASCII, e.g. {f.suggestion}",
            )
        )
    return report


def check_mojibake(text: str) -> ValidationReport:
    report = ValidationReport()
    if looks_like_mojibake(text):
        fixed, changed = fix_mojibake(text)
        if changed:
            report.warnings.append("mojibake detected and auto-fix is unambiguous - fix will be applied")
        else:
            report.warnings.append(
                "mojibake-like sequences detected (e.g. 'Ã©') but the fix is ambiguous - check accented characters"
            )
    return report


def merge_reports(*reports: ValidationReport) -> ValidationReport:
    out = ValidationReport()
    for r in reports:
        if not r.ok:
            out.ok = False
        out.issues.extend(r.issues)
        out.warnings.extend(r.warnings)
    return out
