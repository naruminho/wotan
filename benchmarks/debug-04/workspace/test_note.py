from pathlib import Path
from save_note import append_note


def test_crlf_preserved(tmp_path):
    f = tmp_path / "note.txt"
    f.write_bytes(b"line one\r\nline two\r\n")
    append_note("line three", path=str(f))
    data = f.read_bytes()
    assert data.count(b"\r\n") == 3
    assert b"\n" not in data.replace(b"\r\n", b"")
