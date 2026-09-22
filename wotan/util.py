"""Small shared utilities: secret masking, JSON helpers, error formatting."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any


def new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:12]
    return f"{prefix}{short}" if prefix else short


def now() -> float:
    return time.time()


def mask_secret(value: str | None, keep: int = 4) -> str:
    """Mask a secret for logs/UI: never print credentials in the clear."""
    if value is None or value == "":
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(4, len(value) - keep)


def mask_mapping(data: dict[str, Any], extra_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    """Deep-copy a mapping with secret-looking values masked."""
    secret_keys = {
        "authorization",
        "api_key",
        "apikey",
        "api-key",
        "x-api-key",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "cookie",
        "set-cookie",
    } | set(extra_keys)
    out: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            out[k] = mask_mapping(v, extra_keys)
        elif isinstance(v, list):
            out[k] = [mask_mapping(i, extra_keys) if isinstance(i, dict) else (mask_secret(i) if isinstance(i, str) and k.lower() in secret_keys else i) for i in v]
        elif isinstance(v, str) and k.lower() in secret_keys:
            out[k] = mask_secret(v)
        else:
            out[k] = v
    return out


def mask_text(text: str, secrets: list[str] | None = None) -> str:
    """Mask known secret strings and common credential shapes inside free text."""
    if not text:
        return text
    for s in secrets or []:
        if s and len(s) >= 6:
            text = text.replace(s, mask_secret(s))
    # Bearer tokens / api keys in free text.
    text = re.sub(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-]{8,}", lambda m: m.group(1) + mask_secret(m.group(2)), text)
    text = re.sub(r"(?i)\b(sk-[A-Za-z0-9_\-]{6,})", lambda m: mask_secret(m.group(1)), text)
    return text


def dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, default=str)


def try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


@dataclass
class ToolError:
    """ERROR / WHY / HOW TO FIX message - weak models self-correct best with these."""

    error: str
    why: str
    how_to_fix: str

    def to_dict(self) -> dict[str, str]:
        return {"status": "error", "error": self.error, "why": self.why, "how_to_fix": self.how_to_fix}

    def render(self) -> str:
        return (
            f"ERROR: {self.error}\n"
            f"WHY: {self.why}\n"
            f"HOW TO FIX: {self.how_to_fix}"
        )


def truncate(text: str, limit: int = 4000, head_ratio: float = 0.6) -> str:
    """Smart truncation keeping head and tail (both are informative)."""
    if len(text) <= limit:
        return text
    head = int(limit * head_ratio)
    tail = limit - head - 80
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [wotan: {omitted} characters omitted] ...\n{text[-tail:]}"


def utc_iso(ts: float | None = None) -> str:
    import datetime as _dt

    t = ts if ts is not None else time.time()
    return _dt.datetime.fromtimestamp(t, _dt.timezone.utc).isoformat(timespec="milliseconds")
