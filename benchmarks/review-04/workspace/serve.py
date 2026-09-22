from pathlib import Path

ROOT = Path("files")


def read_file(name):
    return (ROOT / name).read_text(encoding="utf-8")
