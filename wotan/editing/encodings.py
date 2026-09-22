"""Encoding / line-ending detection and preservation (Windows-critical).

Files keep their original encoding (UTF-8, UTF-8 BOM, cp1252/latin-1) and line
endings (CRLF/LF, mixed preserved) across edits. New files are UTF-8 without
BOM with configurable line endings.
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

BOMS: list[tuple[bytes, str]] = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
]


@dataclass
class FileFormat:
    encoding: str = "utf-8"  # codec name used for decode/encode
    has_bom: bool = False
    eol: str = "lf"  # lf | crlf | mixed
    crlf_count: int = 0
    lf_count: int = 0

    @property
    def preferred_eol(self) -> str:
        return "\r\n" if self.eol == "crlf" or (self.eol == "mixed" and self.crlf_count >= self.lf_count) else "\n"


def detect_format(data: bytes) -> FileFormat:
    fmt = FileFormat()
    for bom, enc in BOMS:
        if data.startswith(bom):
            fmt.has_bom = True
            fmt.encoding = enc
            break
    # Decode tolerantly for analysis (records the codec actually used).
    text, enc_used, had_bom = decode_bytes(data, fmt.encoding if fmt.has_bom else None)
    fmt.has_bom = fmt.has_bom or had_bom
    fmt.encoding = enc_used
    crlf = text.count("\r\n")
    lf_total = text.count("\n")
    lf_only = lf_total - crlf
    fmt.crlf_count = crlf
    fmt.lf_count = lf_only
    if crlf and lf_only:
        fmt.eol = "mixed"
    elif crlf:
        fmt.eol = "crlf"
    else:
        fmt.eol = "lf"
    return fmt


def decode_bytes(data: bytes, encoding: str | None = None) -> tuple[str, str, bool]:
    """Decode tolerantly: returns (text, encoding_used, had_bom)."""
    if encoding:
        enc = encoding
    else:
        enc = "utf-8"
        for bom, benc in BOMS:
            if data.startswith(bom):
                enc = benc
                break
    had_bom = any(data.startswith(bom) for bom, _ in BOMS)
    try:
        return data.decode(enc), enc, had_bom
    except (UnicodeDecodeError, LookupError):
        pass
    for fallback in ("cp1252", "latin-1"):
        try:
            return data.decode(fallback), fallback, had_bom
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8", had_bom


def encode_text(text: str, fmt: FileFormat, new_eol: str = "preserve") -> bytes:
    """Encode back with the original BOM/codec; normalize EOL per policy."""
    eol = fmt.preferred_eol if new_eol == "preserve" else ("\r\n" if new_eol == "crlf" else "\n")
    # Normalize to LF first, then to target EOL - avoids mixed endings after edit
    # unless the file was mixed and policy says preserve (then keep per-line).
    if new_eol == "preserve" and fmt.eol == "mixed":
        body = text  # keep exactly what the engine produced (line-level preserved)
    else:
        normalized = text.replace("\r\n", "\n")
        body = normalized.replace("\n", eol)
    codec = fmt.encoding
    try:
        data = body.encode(codec, errors="strict")
    except UnicodeEncodeError:
        data = body.encode("utf-8")
        codec = "utf-8"
    if fmt.has_bom:
        for bom, enc in BOMS:
            if enc == codec or (enc == "utf-8-sig" and codec in ("utf-8", "utf-8-sig")):
                if not data.startswith(bom):
                    data = bom + data
                break
    return data


def new_file_bytes(content: str, encoding: str = "utf-8", eol: str = "lf") -> bytes:
    fmt = FileFormat(encoding=encoding, has_bom=encoding == "utf-8-sig", eol=eol if eol in ("lf", "crlf") else "lf")
    return encode_text(content, fmt, new_eol=eol if eol in ("lf", "crlf") else "lf")
