from pathlib import Path


def append_note(text, path="note.txt"):
    p = Path(path)
    data = p.read_text(encoding="utf-8")
    p.write_text(data + text + "\n", encoding="utf-8")
