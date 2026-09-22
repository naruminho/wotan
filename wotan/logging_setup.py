"""Structured JSON application logging with correlation ids and a UI ring buffer.

Frontend JS errors are shipped to the backend and land in the same log. Secrets
are always masked before they reach a file or the UI.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import threading
import time
from collections import deque
from typing import Any

from .paths import logs_dir, utf8_console
from .util import mask_mapping, mask_text, new_id

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
component_var: contextvars.ContextVar[str] = contextvars.ContextVar("component", default="core")

MAX_RING = 2000


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "component": getattr(record, "component", component_var.get()),
            "correlation_id": getattr(record, "correlation_id", correlation_id.get()),
            "message": record.getMessage(),
        }
        extra = getattr(record, "data", None)
        if extra:
            payload["data"] = mask_mapping(extra) if isinstance(extra, dict) else extra
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        payload["message"] = mask_text(str(payload["message"]))
        return json.dumps(payload, ensure_ascii=False, default=str)


class RingBufferHandler(logging.Handler):
    """Keeps recent log records in memory for the UI log panel (live tail)."""

    def __init__(self, capacity: int = MAX_RING) -> None:
        super().__init__()
        self.buffer: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._lock = threading.Lock()
        self._subs: list[Any] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = json.loads(JsonFormatter().format(record))
        except Exception:
            return
        with self._lock:
            self.buffer.append(entry)

    def snapshot(self, limit: int = 500, level: str | None = None, query: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self.buffer)
        if level:
            order = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            try:
                min_idx = order.index(level.upper())
                items = [i for i in items if order.index(str(i.get("level", "INFO"))) >= min_idx]
            except ValueError:
                pass
        if query:
            q = query.lower()
            items = [i for i in items if q in json.dumps(i, ensure_ascii=False).lower()]
        return items[-limit:]


_ring: RingBufferHandler | None = None
_configured = False


def ring() -> RingBufferHandler:
    global _ring
    if _ring is None:
        _ring = RingBufferHandler()
    return _ring


def setup_logging(level: str = "INFO", log_dir: Any = None) -> None:
    """Idempotent logging setup: rotating JSON files + stderr + ring buffer."""
    global _configured
    utf8_console()
    root = logging.getLogger()
    if _configured:
        root.setLevel(level.upper())
        return
    root.setLevel(level.upper())
    fmt = JsonFormatter()

    try:
        d = logs_dir() if log_dir is None else log_dir
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            d / "wotan.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except Exception:
        pass  # logging must never crash the app

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)

    rh = ring()
    rh.setFormatter(fmt)
    root.addHandler(rh)
    _configured = True


def get_logger(name: str, component: str | None = None) -> logging.LoggerAdapter:
    base = logging.getLogger(name)

    class _Adapter(logging.LoggerAdapter):
        def process(self, msg: Any, kwargs: Any) -> tuple[Any, Any]:
            extra = kwargs.pop("extra", None) or {}
            if "data" not in extra and extra:
                extra = {"data": extra}
            if component:
                extra.setdefault("component", component)
            extra.setdefault("correlation_id", correlation_id.get())
            kwargs["extra"] = extra
            return msg, kwargs

    return _Adapter(base, {})  # type: ignore[return-value]


def set_correlation(cid: str | None = None) -> str:
    cid = cid or new_id("cid-")
    correlation_id.set(cid)
    return cid


def diagnostic_bundle_files() -> list[Any]:
    """Files that go into the 'Generate diagnostic bundle' zip (no secrets)."""
    try:
        d = logs_dir()
        return sorted(d.glob("wotan.log*"))
    except Exception:
        return []
