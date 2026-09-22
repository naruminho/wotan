"""Frankenstein-proof edit engine.

Public operations (all structured - never marker-based):

 * :meth:`EditEngine.read`         - windowed read, optional hashline format,
 * :meth:`EditEngine.edit_file`    - single edit (old_string/new_string,
   must match exactly once; tolerant fallback controlled and reported),
 * :meth:`EditEngine.multi_edit`   - several edits to one file, ATOMIC
   (all-or-nothing),
 * :meth:`EditEngine.write_file`   - new files / justified rewrites only,
 * :meth:`EditEngine.hashline_edit`- line:hash anchored edits for weak models,
 * :meth:`EditEngine.apply_patch`  - unified-diff style for models trained on it.

Guards: mandatory read-before-edit, stale-file detection (content hash),
post-edit validation (markers, placeholders, syntax, diff sanity, emoji,
mojibake) with automatic rollback, checkpoints for per-step undo, and the
resulting snippet with line numbers returned after every edit.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..logging_setup import get_logger
from ..security import fix_mojibake
from ..util import ToolError, truncate
from .checkpoints import CheckpointStore
from .encodings import FileFormat, decode_bytes, encode_text, new_file_bytes
from .validators import (
    ValidationIssue,
    ValidationReport,
    check_emoji_policy,
    check_mojibake,
    check_new_markers,
    check_syntax,
    diff_sanity,
    merge_reports,
)

log = get_logger("wotan.editing", component="editing")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def line_hash(line: str) -> str:
    """Hashline anchor hash: first 8 hex of sha1 of the line content (no EOL)."""
    return hashlib.sha1(line.rstrip("\r\n").encode("utf-8", errors="replace")).hexdigest()[:8]


@dataclass
class EditOutcome:
    ok: bool
    message: str = ""
    diff: str = ""
    snippet: str = ""
    warnings: list[str] = field(default_factory=list)
    used_fallback: bool = False
    checkpoint_id: str = ""
    error: ToolError | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_tool_result(self) -> dict[str, Any]:
        if self.ok:
            out: dict[str, Any] = {"status": "ok", "message": self.message, "snippet": self.snippet}
            if self.warnings:
                out["warnings"] = self.warnings
            if self.used_fallback:
                out["used_fallback"] = True
            return out
        return {"status": "error", **(self.error.to_dict() if self.error else {"error": self.message})}


@dataclass
class ReadResult:
    ok: bool
    content: str = ""
    total_lines: int = 0
    offset: int = 1
    truncated: bool = False
    encoding: str = "utf-8"
    eol: str = "lf"
    content_hash: str = ""
    is_binary: bool = False
    error: ToolError | None = None


class EditEngine:
    def __init__(
        self,
        root: Path,
        checkpoints: CheckpointStore | None = None,
        new_file_eol: str = "lf",
        new_file_encoding: str = "utf-8",
        rewrite_threshold_lines: int = 40,
        emoji_policy: bool = True,
        emoji_allow: frozenset[str] = frozenset(),
        syntax_check: bool = True,
        post_edit_hook: Callable[[Path, str], list[str]] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.checkpoints = checkpoints
        self.new_file_eol = new_file_eol
        self.new_file_encoding = new_file_encoding
        self.rewrite_threshold_lines = rewrite_threshold_lines
        self.emoji_policy = emoji_policy
        self.emoji_allow = emoji_allow
        self.syntax_check = syntax_check
        self.post_edit_hook = post_edit_hook
        # read-before-edit + stale detection: (session, path) -> sha256 of text at last read
        self._read_state: dict[tuple[str, str], str] = {}
        self.step_counter = 0

    # -- path safety ---------------------------------------------------------
    def _resolve(self, path: str | Path) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.root / p
        p = p.resolve()
        try:
            p.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"path escapes the workspace: {path}") from exc
        return p

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return str(p)

    # -- read ----------------------------------------------------------------
    def read(self, path: str, session_id: str = "", offset: int = 1, limit: int = 200,
             hashline: bool = False) -> ReadResult:
        """Windowed read (SWE-agent style viewer). Lines are numbered from 1."""
        try:
            p = self._resolve(path)
        except PermissionError as exc:
            return ReadResult(ok=False, error=ToolError(str(exc), "path outside workspace", "use a path inside the workspace folder"))
        if not p.is_file():
            return ReadResult(
                ok=False,
                error=ToolError(
                    f"file not found: {self._rel(p)}",
                    "the path does not exist or is a directory",
                    "use fs_list to see the folder contents, or fs_glob to find the file",
                ),
            )
        data = p.read_bytes()
        if b"\x00" in data[:8192]:
            return ReadResult(ok=True, is_binary=True, content=f"[binary file: {self._rel(p)}, {len(data)} bytes]")
        fmt_eol = FileFormat()
        text, enc, had_bom = decode_bytes(data)
        fmt_eol.has_bom = had_bom
        fmt_eol.encoding = enc
        crlf = text.count("\r\n")
        lf_only = text.count("\n") - crlf
        fmt_eol.eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
        lines = text.splitlines()
        total = len(lines)
        offset = max(1, offset)
        window = lines[offset - 1 : offset - 1 + max(1, limit)]
        truncated = offset - 1 + len(window) < total
        if hashline:
            body = "\n".join(f"{offset + i}:{line_hash(l)}|{l}" for i, l in enumerate(window))
        else:
            width = len(str(offset + len(window) - 1))
            body = "\n".join(f"{offset + i:>{width}}| {l}" for i, l in enumerate(window))
        chash = sha256_text(text)
        if session_id:
            self._read_state[(session_id, self._rel(p))] = chash
        return ReadResult(
            ok=True,
            content=body,
            total_lines=total,
            offset=offset,
            truncated=truncated,
            encoding=enc + ("+bom" if fmt_eol.has_bom else ""),
            eol=fmt_eol.eol,
            content_hash=chash,
        )

    def mark_read(self, session_id: str, path: str, text: str) -> None:
        self._read_state[(session_id, self._rel(self._resolve(path)))] = sha256_text(text)

    def _check_fresh(self, session_id: str, rel: str, current_hash: str) -> ToolError | None:
        if not session_id:
            return None
        key = (session_id, rel)
        last = self._read_state.get(key)
        if last is None:
            return ToolError(
                f"mandatory read before edit: {rel} has not been read in this session",
                "edits without reading first caused stale overwrites of other people's changes",
                f"call fs_read on {rel} first, then repeat the edit with the same old_string/new_string",
            )
        if last != current_hash:
            return ToolError(
                f"stale file: {rel} changed since it was last read (hash mismatch)",
                "the file was modified after the last read (for example by the user in the IDE)",
                f"re-read {rel} and re-apply your edit against the current content",
            )
        return None

    # -- matching ------------------------------------------------------------
    def _find_exact(self, text: str, needle: str) -> list[int]:
        if not needle:
            return []
        out: list[int] = []
        start = 0
        while True:
            i = text.find(needle, start)
            if i < 0:
                return out
            out.append(i)
            start = i + 1

    def _eol_variant(self, text: str, needle: str) -> str:
        """Try needle with the file's dominant line endings."""
        if "\r\n" in text and "\r\n" not in needle:
            return needle.replace("\n", "\r\n")
        if "\r\n" not in text and "\r\n" in needle:
            return needle.replace("\r\n", "\n")
        return needle

    def _find_tolerant(self, text: str, needle: str) -> list[tuple[int, int]]:
        """Whitespace/crlf tolerant line-sequence match. Returns (start, end) offsets."""
        src = text.splitlines(keepends=True)
        tgt = [l.rstrip("\r\n") for l in needle.splitlines()]
        if not tgt:
            return []
        norm = [re.sub(r"\s+", " ", l.strip()) for l in tgt]
        hits: list[tuple[int, int]] = []
        n = len(norm)
        for i in range(len(src) - n + 1):
            window = [re.sub(r"\s+", " ", src[i + k].rstrip("\r\n").strip()) for k in range(n)]
            if window == norm:
                start_off = sum(len(l) for l in src[:i])
                end_off = start_off + sum(len(l) for l in src[i : i + n])
                hits.append((start_off, end_off))
        return hits

    def _closest_snippet(self, text: str, needle: str, radius: int = 2) -> str:
        lines = text.splitlines()
        n_lines = [l.strip() for l in needle.splitlines() if l.strip()]
        if not n_lines or not lines:
            return ""
        key = n_lines[0]
        scored = sorted(
            ((difflib.SequenceMatcher(None, key, l.strip()).ratio(), i) for i, l in enumerate(lines)),
            reverse=True,
        )[:5]
        best = ""
        best_score = -1.0
        for score, i in scored:
            lo = max(0, i - 1)
            hi = min(len(lines), i + len(n_lines) + 1)
            candidate = "\n".join(lines[lo:hi])
            full = difflib.SequenceMatcher(None, "\n".join(n_lines), "\n".join(l.strip() for l in lines[lo:hi])).ratio()
            if full > best_score:
                best_score = full
                best = candidate
        return best

    def _adapt_replacement(self, text: str, start: int, end: int, new_string: str) -> str:
        """For tolerant matches: keep the file's indentation style and line
        structure. The model's whitespace is adapted to the file, not the
        reverse (tabs vs spaces, missing trailing EOL)."""
        region = text[start:end]
        region_lines = region.splitlines(keepends=True)
        new_lines = new_string.splitlines(keepends=True)
        region_tab = any(l.startswith("\t") for l in region_lines)
        region_space = any(l.startswith(" ") for l in region_lines)
        default_eol = "\r\n" if region.count("\r\n") >= region.count("\n") - region.count("\r\n") else "\n"
        out: list[str] = []
        for i, line in enumerate(new_lines):
            m = re.match(r"[ \t]*", line)
            ws = m.group(0)
            rest = line[len(ws) :]
            body = rest.rstrip("\r\n")
            eol = rest[len(body) :]
            if region_tab and ws and "\t" not in ws:
                ws = ws.replace("    ", "\t")
            elif region_space and "\t" in ws:
                ws = ws.replace("\t", "    ")
            if not eol and i < len(region_lines):
                src = region_lines[i]
                src_eol = src[len(src.rstrip("\r\n")) :]
                eol = src_eol or default_eol
            out.append(ws + body + eol)
        return "".join(out)

    def _locate(self, text: str, old_string: str) -> tuple[list[tuple[int, int]], bool, str]:
        """Find old_string. Returns (spans, used_fallback, fallback_note)."""
        exact = self._find_exact(text, old_string)
        if exact:
            return [(i, i + len(old_string)) for i in exact], False, ""
        variant = self._eol_variant(text, old_string)
        if variant != old_string:
            exact2 = self._find_exact(text, variant)
            if exact2:
                return [(i, i + len(variant)) for i in exact2], True, "line-ending normalization used (LF/CRLF)"
        tol = self._find_tolerant(text, old_string)
        if len(tol) == 1:
            return tol, True, "whitespace-tolerant fallback used (indentation/trailing whitespace normalized)"
        if len(tol) > 1:
            return tol, True, "whitespace-tolerant match found several candidates"
        return [], False, ""

    # -- validation pipeline --------------------------------------------------
    def _validate(self, before: str, after: str, old_string: str, new_string: str,
                  language: str, path: str, requested_removal: bool = False) -> ValidationReport:
        reports = [
            check_new_markers(before, after, language),
            diff_sanity(before, after, old_string, new_string, requested_removal=requested_removal),
            check_emoji_policy(after, self.emoji_policy, self.emoji_allow),
            check_mojibake(after),
        ]
        if self.syntax_check and language:
            before_syn = check_syntax(before, language, path)
            after_syn = check_syntax(after, language, path)
            if not before_syn.ok:
                after_syn.warnings.append("file had syntax errors before the edit too (not blocking)")
                # treat pre-broken files as non-blocking
                after_syn.ok = True
                after_syn.issues = []
            reports.append(after_syn)
        return merge_reports(*reports)

    def _language_of(self, p: Path) -> str:
        ext = p.suffix.lower().lstrip(".")
        return {
            "py": "python", "pyw": "python", "json": "json", "yaml": "yaml", "yml": "yaml",
            "toml": "toml", "js": "javascript", "jsx": "javascript", "mjs": "javascript",
            "cjs": "javascript", "ts": "typescript", "tsx": "typescript",
        }.get(ext, "")

    def _snippet_around(self, text: str, centers: list[int], radius: int = 3) -> str:
        lines = text.splitlines()
        ranges: list[tuple[int, int]] = []
        for c in centers:
            ln = text[:c].count("\n")
            ranges.append((max(0, ln - radius), min(len(lines), ln + radius + 1)))
        # merge overlaps
        ranges.sort()
        merged: list[list[int]] = []
        for lo, hi in ranges:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        chunks: list[str] = []
        for lo, hi in merged:
            width = len(str(hi))
            chunk = "\n".join(f"{i + 1:>{width}}| {lines[i]}" for i in range(lo, hi))
            chunks.append(chunk)
        return truncate("\n...\n".join(chunks), 3000)

    def _diff_of(self, before: str, after: str, rel: str) -> str:
        return "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{rel}",
                tofile=f"b/{rel}",
                n=2,
            )
        )

    def _finish_edit(
        self,
        p: Path,
        rel: str,
        session_id: str,
        before: str,
        after: str,
        fmt: FileFormat,
        label: str,
        centers: list[int],
        report: ValidationReport,
        used_fallback: bool,
        fallback_note: str,
    ) -> EditOutcome:
        if not report.ok:
            # Automatic rollback: nothing is written.
            msgs = "\n\n".join(i.render() for i in report.issues)
            return EditOutcome(
                ok=False,
                message="edit rejected by validation - file left unchanged",
                error=ToolError(
                    error="edit failed post-edit validation and was rolled back",
                    why=msgs,
                    how_to_fix="adjust old_string/new_string and try again; the file on disk is unchanged",
                ),
            )
        data = encode_text(after, fmt, new_eol="preserve")
        before_bytes = p.read_bytes() if p.exists() else b""
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        cp_id = ""
        if self.checkpoints is not None:
            self.step_counter += 1
            cp_id = self.checkpoints.snapshot(
                session_id=session_id or "adhoc",
                step=self.step_counter,
                path=self._rel(p),
                content_before=before_bytes,
                content_after=data,
                encoding=fmt.encoding,
                eol=fmt.eol,
                label=label,
            )
        if session_id:
            self._read_state[(session_id, rel)] = sha256_text(after)
        warnings = list(report.warnings)
        if used_fallback:
            warnings.append(f"NOTE: {fallback_note} - matched text differs from old_string; verify the snippet")
        if self.post_edit_hook is not None:
            try:
                warnings.extend(self.post_edit_hook(p, after) or [])
            except Exception as exc:  # hooks must not break edits
                warnings.append(f"post-edit hook failed: {exc}")
        return EditOutcome(
            ok=True,
            message=f"edited {rel}",
            diff=truncate(self._diff_of(before, after, rel), 4000),
            snippet=self._snippet_around(after, centers),
            warnings=warnings,
            used_fallback=used_fallback,
            checkpoint_id=cp_id,
        )

    # -- public ops -----------------------------------------------------------
    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        session_id: str = "",
    ) -> EditOutcome:
        try:
            p = self._resolve(path)
        except PermissionError as exc:
            return EditOutcome(ok=False, error=ToolError(str(exc), "path outside workspace", "use a workspace-relative path"))
        rel = self._rel(p)
        if not p.is_file():
            return EditOutcome(
                ok=False,
                error=ToolError(
                    f"file not found: {rel}",
                    "edit_file only edits existing files",
                    "use fs_write to create a new file (or check the path)",
                ),
            )
        data = p.read_bytes()
        before, enc, had_bom = decode_bytes(data)
        fmt = FileFormat(encoding=enc, has_bom=had_bom)
        crlf = before.count("\r\n")
        lf_only = before.count("\n") - crlf
        fmt.eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
        fmt.crlf_count, fmt.lf_count = crlf, lf_only

        stale = self._check_fresh(session_id, rel, sha256_text(before))
        if stale:
            return EditOutcome(ok=False, error=stale)

        if old_string == new_string:
            return EditOutcome(
                ok=False,
                error=ToolError("old_string and new_string are identical", "the edit would change nothing", "provide different text"),
            )

        spans, used_fallback, fallback_note = self._locate(before, old_string)
        if not spans:
            closest = self._closest_snippet(before, old_string)
            hint = f"closest similar snippet:\n{closest}" if closest else "no similar snippet found"
            return EditOutcome(
                ok=False,
                error=ToolError(
                    f"old_string not found in {rel}",
                    f"the exact text does not appear in the file (searched {len(before)} characters); {hint}",
                    "call fs_read to see the current content, then copy old_string exactly (or use hashline anchors)",
                ),
            )
        if len(spans) > 1 and not replace_all:
            occ_lines = [before[:s].count("\n") + 1 for s, _ in spans]
            return EditOutcome(
                ok=False,
                error=ToolError(
                    f"old_string found {len(spans)} times in {rel}",
                    f"it must appear exactly once; occurrences start on lines {occ_lines}",
                    "include more surrounding context to make old_string unique, or pass replace_all=true",
                ),
            )
        if replace_all and used_fallback and not self._find_exact(before, old_string) and not self._find_exact(before, self._eol_variant(before, old_string)):
            return EditOutcome(
                ok=False,
                error=ToolError(
                    "replace_all requires exact matches",
                    "whitespace-tolerant matching is ambiguous with replace_all",
                    "fix the whitespace in old_string to match the file exactly",
                ),
            )

        if replace_all:
            target_spans = spans
        else:
            target_spans = spans[:1]

        after = before
        centers: list[int] = []
        for start, end in reversed(target_spans):
            replacement = self._adapt_replacement(before, start, end, new_string) if used_fallback else new_string
            after = after[:start] + replacement + after[end:]
            centers.append(start)
        centers = [min(c, len(after)) for c in centers]

        language = self._language_of(p)
        report = self._validate(before, after, old_string, new_string, language, rel,
                               requested_removal=(new_string.strip() == ""))
        # Auto-fix unambiguous mojibake in the new content.
        fixed, changed = fix_mojibake(after)
        if changed and fix_mojibake(before)[1] is False:
            after = fixed
        return self._finish_edit(p, rel, session_id, before, after, fmt,
                                 f"edit_file {rel}", centers, report, used_fallback, fallback_note)

    def multi_edit(self, path: str, edits: list[dict[str, Any]], session_id: str = "") -> EditOutcome:
        """Apply several edits atomically: validated in sequence, written once."""
        try:
            p = self._resolve(path)
        except PermissionError as exc:
            return EditOutcome(ok=False, error=ToolError(str(exc), "path outside workspace", "use a workspace-relative path"))
        rel = self._rel(p)
        if not p.is_file():
            return EditOutcome(ok=False, error=ToolError(f"file not found: {rel}", "multi_edit only edits existing files", "use fs_write first"))
        data = p.read_bytes()
        before, enc, had_bom = decode_bytes(data)
        fmt = FileFormat(encoding=enc, has_bom=had_bom)
        crlf = before.count("\r\n")
        lf_only = before.count("\n") - crlf
        fmt.eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
        fmt.crlf_count, fmt.lf_count = crlf, lf_only

        stale = self._check_fresh(session_id, rel, sha256_text(before))
        if stale:
            return EditOutcome(ok=False, error=stale)

        current = before
        centers: list[int] = []
        notes: list[str] = []
        language = self._language_of(p)
        for i, ed in enumerate(edits):
            old_s = ed.get("old_string", "")
            new_s = ed.get("new_string", ed.get("new_text", ""))
            replace_all = bool(ed.get("replace_all", False))
            spans, used_fallback, fb_note = self._locate(current, old_s)
            if not spans:
                closest = self._closest_snippet(current, old_s)
                return EditOutcome(
                    ok=False,
                    error=ToolError(
                        f"multi_edit step {i + 1}/{len(edits)} failed: old_string not found",
                        f"the file is unchanged (atomic); closest snippet:\n{truncate(closest, 800)}",
                        "fix this step's old_string to match the file exactly and re-run all steps",
                    ),
                )
            if len(spans) > 1 and not replace_all:
                occ_lines = [current[:s].count("\n") + 1 for s, _ in spans]
                return EditOutcome(
                    ok=False,
                    error=ToolError(
                        f"multi_edit step {i + 1}/{len(edits)} failed: old_string found {len(spans)} times",
                        f"occurrences on lines {occ_lines}; nothing was written (atomic)",
                        "add more context to make it unique or set replace_all on that step",
                    ),
                )
            for start, end in reversed(spans if replace_all else spans[:1]):
                replacement = self._adapt_replacement(current, start, end, new_s) if used_fallback else new_s
                current = current[:start] + replacement + current[end:]
                centers.append(start)
            if used_fallback:
                notes.append(f"step {i + 1}: {fb_note}")
            report = self._validate(before, current, old_s, new_s, language, rel)
            if not report.ok:
                return EditOutcome(
                    ok=False,
                    error=ToolError(
                        f"multi_edit step {i + 1}/{len(edits)} failed validation - nothing was written",
                        "\n\n".join(x.render() for x in report.issues),
                        "fix the step and re-run the whole multi_edit",
                    ),
                )
        centers = [min(c, len(current)) for c in centers]
        outcome = self._finish_edit(p, rel, session_id, before, current, fmt,
                                    f"multi_edit {rel} ({len(edits)} steps)", centers, report,
                                    used_fallback=bool(notes), fallback_note="; ".join(notes))
        return outcome

    def write_file(self, path: str, content: str, session_id: str = "", justification: str = "",
                   force: bool = False) -> EditOutcome:
        """Create a new file, or rewrite an existing one only when justified."""
        try:
            p = self._resolve(path)
        except PermissionError as exc:
            return EditOutcome(ok=False, error=ToolError(str(exc), "path outside workspace", "use a workspace-relative path"))
        rel = self._rel(p)
        existed = p.is_file()
        before = ""
        fmt = FileFormat(encoding=self.new_file_encoding, has_bom=False, eol=self.new_file_eol)
        if existed:
            data = p.read_bytes()
            before, enc, had_bom = decode_bytes(data)
            fmt = FileFormat(encoding=enc, has_bom=had_bom)
            crlf = before.count("\r\n")
            lf_only = before.count("\n") - crlf
            fmt.eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
            fmt.crlf_count, fmt.lf_count = crlf, lf_only
            stale = self._check_fresh(session_id, rel, sha256_text(before))
            if stale:
                return EditOutcome(ok=False, error=stale)
            before_lines = before.count("\n") + 1
            if before_lines >= self.rewrite_threshold_lines and not (justification.strip() or force):
                # Compare with line endings normalized: an eol-only difference (e.g. a
                # CRLF file rewritten with LF content) must not mask a small text edit.
                sim = difflib.SequenceMatcher(
                    None, before.replace("\r\n", "\n"), content.replace("\r\n", "\n")
                ).ratio()
                if sim > 0.3:  # a small change on a big file: refuse full rewrite
                    return EditOutcome(
                        ok=False,
                        error=ToolError(
                            f"refusing to rewrite large existing file {rel} ({before_lines} lines)",
                            "full rewrites of large files waste tokens and introduce regressions; the change appears small",
                            "use fs_edit (old_string/new_string) for targeted changes, or pass justification='rewrite' if a rewrite is truly intended",
                        ),
                    )
        language = self._language_of(p)
        report = self._validate(before, content, old_string=before, new_string=content,
                                language=language, path=rel, requested_removal=existed)
        fixed, changed = fix_mojibake(content)
        if changed and not fix_mojibake(before)[1]:
            content = fixed
        center = 0
        return self._finish_edit(
            p, rel, session_id, before, content, fmt,
            f"write_file {rel}", [center], report,
            used_fallback=False, fallback_note="",
        )

    # -- hashline -------------------------------------------------------------
    def format_hashline(self, path: str, session_id: str = "", offset: int = 1, limit: int = 200) -> EditOutcome:
        r = self.read(path, session_id=session_id, offset=offset, limit=limit, hashline=True)
        if not r.ok:
            return EditOutcome(ok=False, error=r.error or ToolError("read failed", "", ""))
        return EditOutcome(
            ok=True,
            message=f"{path}: {r.total_lines} lines (hashline format, window {offset}..{offset + limit - 1})",
            snippet=r.content,
            data={"total_lines": r.total_lines, "format": "hashline"},
        )

    def hashline_edit(self, path: str, ops: list[dict[str, Any]], session_id: str = "") -> EditOutcome:
        """Edits anchored at ``line:hash`` instead of copying text.

        op = {"op": "replace", "start": "12:ab12cd34", "end": "15:9988eeff", "new_text": "..."}
        op = {"op": "insert", "after": "12:ab12cd34", "new_text": "..."}
        op = {"op": "delete", "start": "12:ab12cd34", "end": "13:ffee0011"}
        """
        try:
            p = self._resolve(path)
        except PermissionError as exc:
            return EditOutcome(ok=False, error=ToolError(str(exc), "path outside workspace", "use a workspace-relative path"))
        rel = self._rel(p)
        if not p.is_file():
            return EditOutcome(ok=False, error=ToolError(f"file not found: {rel}", "hashline_edit edits existing files", "use fs_write first"))
        data = p.read_bytes()
        before, enc, had_bom = decode_bytes(data)
        fmt = FileFormat(encoding=enc, has_bom=had_bom)
        crlf = before.count("\r\n")
        lf_only = before.count("\n") - crlf
        fmt.eol = "mixed" if crlf and lf_only else ("crlf" if crlf else "lf")
        stale = self._check_fresh(session_id, rel, sha256_text(before))
        if stale:
            return EditOutcome(ok=False, error=stale)

        # Preserve per-line EOL style for mixed files.
        raw_lines = before.splitlines(keepends=True)
        plain = [l.rstrip("\r\n") for l in raw_lines]
        eols = [l[len(l.rstrip("\r\n")):] for l in raw_lines]

        def parse_anchor(anchor: str, opname: str) -> tuple[int, str]:
            try:
                num_s, h = anchor.split(":", 1)
                num = int(num_s)
            except ValueError:
                raise ToolError(f"invalid anchor {anchor!r}", "expected 'line:hash' e.g. '12:ab12cd34'", "use the anchors from a hashline read")
            if num < 1 or num > len(plain):
                raise ToolError(f"anchor {anchor!r} is out of range (file has {len(plain)} lines)", "the file changed or the anchor is wrong", "re-read the file in hashline format")
            actual = line_hash(plain[num - 1])
            if actual != h:
                raise ToolError(
                    f"hash mismatch at line {num}: expected {h}, found {actual}",
                    "the file changed since the last read - the hash does not match, edit rejected",
                    "re-read the file and retry with the fresh anchors",
                )
            return num, actual

        current_lines = list(plain)
        current_eols = list(eols)
        centers: list[int] = []
        for i, op in enumerate(ops):
            kind = op.get("op", "replace")
            try:
                if kind == "insert":
                    num, _ = parse_anchor(op.get("after", ""), "insert")
                    new_text = op.get("new_text", "")
                    nl = new_text.split("\n") if new_text != "" else []
                    eol = "\r\n" if fmt.eol == "crlf" else "\n"
                    current_lines[num:num] = nl
                    current_eols[num:num] = [eol] * len(nl)
                elif kind == "delete":
                    s_num, _ = parse_anchor(op.get("start", ""), "delete")
                    e_num, _ = parse_anchor(op.get("end", op.get("start", "")), "delete")
                    if e_num < s_num:
                        raise ToolError(f"op {i + 1}: end line {e_num} before start line {s_num}", "range inverted", "swap start and end")
                    del current_lines[s_num - 1 : e_num]
                    del current_eols[s_num - 1 : e_num]
                elif kind == "replace":
                    s_num, _ = parse_anchor(op.get("start", ""), "replace")
                    end_anchor = op.get("end", op.get("start", ""))
                    e_num, _ = parse_anchor(end_anchor, "replace")
                    if e_num < s_num:
                        raise ToolError(f"op {i + 1}: end line {e_num} before start line {s_num}", "range inverted", "swap start and end")
                    new_text = op.get("new_text", "")
                    eol = current_eols[s_num - 1] if current_eols else ("\r\n" if fmt.eol == "crlf" else "\n")
                    nl = new_text.split("\n") if new_text != "" else []
                    current_lines[s_num - 1 : e_num] = nl
                    current_eols[s_num - 1 : e_num] = [eol] * len(nl)
                    centers.append(sum(len(x) for x in current_lines[: max(0, s_num - 2)]))
                else:
                    raise ToolError(f"unknown hashline op {kind!r}", "valid ops: replace, insert, delete", "use one of: replace | insert | delete")
            except ToolError as exc:
                return EditOutcome(ok=False, error=exc)

        after = ""
        for idx, line in enumerate(current_lines):
            eol = current_eols[idx] if idx < len(current_eols) else ""
            after += line + eol
        language = self._language_of(p)
        report = self._validate(before, after, "", "", language, rel)
        if not centers:
            centers = [0]
        return self._finish_edit(p, rel, session_id, before, after, fmt,
                                 f"hashline_edit {rel}", centers, report,
                                 used_fallback=False, fallback_note="")

    # -- apply_patch ----------------------------------------------------------
    def apply_patch(self, patch_text: str, session_id: str = "") -> EditOutcome:
        """Apply a multi-file patch in the OpenAI/claude ``*** Begin Patch`` style.

        All files are validated first and written together (atomic per file set
        as far as possible: validation failures leave everything unchanged).
        """
        if "*** Begin Patch" not in patch_text:
            return EditOutcome(
                ok=False,
                error=ToolError(
                    "patch is missing '*** Begin Patch'",
                    "apply_patch expects the marker style used by OpenAI codex",
                    "start the patch with '*** Begin Patch' and end with '*** End Patch'",
                ),
            )
        body = patch_text.split("*** Begin Patch", 1)[1].split("*** End Patch", 1)[0]
        sections = re.split(r"^\*\*\* (Update|Add|Delete) File:\s*(.+?)\s*$", body, flags=re.MULTILINE)
        # sections: [preamble, kind, path, content, kind, path, content...]
        parsed: list[tuple[str, str, str]] = []
        i = 1
        while i + 2 < len(sections) + 1 and i + 2 <= len(sections):
            kind, path, content = sections[i], sections[i + 1], sections[i + 2]
            parsed.append((kind, path, content))
            i += 3
        if not parsed:
            return EditOutcome(ok=False, error=ToolError("patch has no file sections", "no '*** Update/Add/Delete File:' lines found", "add at least one file section"))

        staged: list[dict[str, Any]] = []
        for kind, rel_path, content in parsed:
            content = content.lstrip("\n")
            try:
                p = self._resolve(rel_path)
            except PermissionError as exc:
                return EditOutcome(ok=False, error=ToolError(str(exc), "path outside workspace", "use a workspace-relative path"))
            if kind == "Delete":
                staged.append({"kind": "delete", "path": p})
                continue
            if kind == "Add":
                staged.append({"kind": "add", "path": p, "content": "".join(line[1:] if line.startswith("+") else line for line in content.splitlines(keepends=True))})
                continue
            # Update: parse @@ hunks with ' ' context, '-' removal, '+' addition.
            if not p.is_file():
                return EditOutcome(ok=False, error=ToolError(f"update target missing: {self._rel(p)}", "cannot update a file that does not exist", "use '*** Add File:' instead"))
            data = p.read_bytes()
            text, enc, had_bom = decode_bytes(data)
            current = text
            hunks = re.split(r"^@@.*$", content, flags=re.MULTILINE)[1:]
            if not hunks:
                return EditOutcome(ok=False, error=ToolError(f"no @@ hunks for {rel_path}", "update sections need @@ markers before each change block", "add '@@' line(s) with context"))
            for hunk in hunks:
                lines = hunk.splitlines(keepends=True)
                old_lines = [l[1:] for l in lines if l[:1] in (" ", "-")]
                new_lines = [l[1:] for l in lines if l[:1] in (" ", "+")]
                old_block = "".join(old_lines)
                new_block = "".join(new_lines)
                spans, used_fb, fb_note = self._locate(current, old_block)
                if len(spans) != 1:
                    return EditOutcome(
                        ok=False,
                        error=ToolError(
                            f"patch hunk for {rel_path} matched {len(spans)} times",
                            f"context must match exactly once (searched for {len(old_block)} chars){'; ' + fb_note if fb_note else ''}",
                            "make the hunk context unique (add surrounding unchanged lines)",
                        ),
                    )
                start, end = spans[0]
                current = current[:start] + new_block + current[end:]
            staged.append({"kind": "update", "path": p, "content": current, "before": text, "fmt": FileFormat(encoding=enc, has_bom=had_bom)})

        # Validate all updates before writing anything (all-or-nothing).
        validated: list[EditOutcome] = []
        for item in staged:
            if item["kind"] == "update":
                report = self._validate(item["before"], item["content"], "", "", self._language_of(item["path"]), self._rel(item["path"]))
                if not report.ok:
                    return EditOutcome(
                        ok=False,
                        error=ToolError(
                            f"patch section for {self._rel(item['path'])} failed validation - nothing written",
                            "\n\n".join(x.render() for x in report.issues),
                            "fix the patch content and re-run apply_patch",
                        ),
                    )
        outcomes: list[str] = []
        total_cp = ""
        for item in staged:
            rel = self._rel(item["path"])
            if item["kind"] == "delete":
                if item["path"].is_file():
                    before_bytes = item["path"].read_bytes()
                    item["path"].unlink()
                    if self.checkpoints is not None:
                        self.step_counter += 1
                        total_cp = self.checkpoints.snapshot(session_id or "adhoc", self.step_counter, rel, before_bytes, b"", label=f"apply_patch delete {rel}")
                outcomes.append(f"deleted {rel}")
            elif item["kind"] == "add":
                before = ""
                fmt = FileFormat(encoding=self.new_file_encoding, eol=self.new_file_eol)
                out = self._finish_edit(item["path"], rel, session_id, "", item["content"], fmt,
                                       f"apply_patch add {rel}", [0], ValidationReport(), False, "")
                outcomes.append(f"added {rel}" if out.ok else f"FAILED add {rel}: {out.message}")
                total_cp = total_cp or out.checkpoint_id
            else:
                fmt = item.get("fmt") or FileFormat()
                crlf = item["before"].count("\r\n")
                fmt.crlf_count = crlf
                fmt.lf_count = item["before"].count("\n") - crlf
                fmt.eol = "mixed" if fmt.crlf_count and fmt.lf_count else ("crlf" if crlf else "lf")
                out = self._finish_edit(item["path"], rel, session_id, item["before"], item["content"], fmt,
                                       f"apply_patch update {rel}", [0], ValidationReport(), False, "")
                outcomes.append(f"updated {rel}" if out.ok else f"FAILED update {rel}: {out.message}")
                total_cp = total_cp or out.checkpoint_id
        return EditOutcome(
            ok=True,
            message="; ".join(outcomes),
            checkpoint_id=total_cp,
            snippet=truncate("\n".join(outcomes), 2000),
        )
