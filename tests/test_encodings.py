"""Encoding / line-ending preservation: CRLF, BOM, cp1252 (Windows-critical)."""

from __future__ import annotations

from wotan.editing.encodings import (
    FileFormat,
    decode_bytes,
    detect_format,
    encode_text,
    new_file_bytes,
)


def test_detect_utf8_lf():
    fmt = detect_format("hello\nworld\n".encode())
    assert fmt.encoding == "utf-8"
    assert fmt.eol == "lf"
    assert not fmt.has_bom


def test_detect_utf8_bom():
    data = b"\xef\xbb\xbfhello\r\nworld\r\n"
    fmt = detect_format(data)
    assert fmt.has_bom
    assert fmt.encoding == "utf-8-sig"
    assert fmt.eol == "crlf"


def test_detect_crlf():
    fmt = detect_format(b"a\r\nb\r\n")
    assert fmt.eol == "crlf"
    assert fmt.preferred_eol == "\r\n"


def test_detect_mixed_eol():
    fmt = detect_format(b"a\r\nb\nc\r\n")
    assert fmt.eol == "mixed"


def test_detect_cp1252():
    data = "ação ção é".encode("cp1252")
    text, enc, bom = decode_bytes(data)
    assert enc == "cp1252"
    assert not bom
    assert "ação" in text
    assert "é" in text


def test_roundtrip_crlf_preserved():
    original = "line1\r\nline2\r\nline3\r\n".encode()
    fmt = detect_format(original)
    text, enc, _ = decode_bytes(original)
    text = text.replace("line2", "LINE2")
    out = encode_text(text, fmt, new_eol="preserve")
    assert out == b"line1\r\nLINE2\r\nline3\r\n"


def test_roundtrip_bom_preserved():
    original = "café résumé\n".encode("utf-8-sig")
    fmt = detect_format(original)
    text, enc, _ = decode_bytes(original)
    out = encode_text(text + "x", fmt, new_eol="preserve")
    assert out.startswith(b"\xef\xbb\xbf")
    assert out.decode("utf-8-sig").endswith("x")


def test_roundtrip_cp1252_preserved():
    original = "coração\n".encode("cp1252")
    fmt = detect_format(original)
    text, enc, _ = decode_bytes(original)
    out = encode_text(text.replace("coração", "CORAÇÃO"), fmt, new_eol="preserve")
    assert out == "CORAÇÃO\n".encode("cp1252")


def test_mixed_eol_preserved_line_by_line():
    original = b"a\r\nb\nc\r\n"
    fmt = detect_format(original)
    text, _, _ = decode_bytes(original)
    out = encode_text(text, fmt, new_eol="preserve")
    assert out == original


def test_new_file_defaults():
    data = new_file_bytes("olá\n")
    assert data == "olá\n".encode("utf-8")
    data = new_file_bytes("x\n", eol="crlf")
    assert data == b"x\r\n"


def test_encode_normalizes_when_asked():
    fmt = FileFormat(encoding="utf-8", eol="lf")
    out = encode_text("a\nb\n", fmt, new_eol="crlf")
    assert out == b"a\r\nb\r\n"
