"""Frankenstein-proof editing engine."""

from .checkpoints import CheckpointStore
from .engine import EditEngine, EditOutcome, ReadResult, line_hash, sha256_text
from .encodings import FileFormat, decode_bytes, encode_text, new_file_bytes

__all__ = [
    "CheckpointStore",
    "EditEngine",
    "EditOutcome",
    "ReadResult",
    "line_hash",
    "sha256_text",
    "FileFormat",
    "decode_bytes",
    "encode_text",
    "new_file_bytes",
]
