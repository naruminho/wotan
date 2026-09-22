"""Shared HTTP retry helpers for the provider adapters.

Connection resets, DNS blips and rate limits are NORMAL when talking to a
corporate gateway through VPN/proxies - the adapters must ride through them
instead of killing the agent turn. Centralized here so openai_compat,
anthropic and generic_http behave the same:

- :func:`parse_retry_after` - accepts seconds, HTTP-date or garbage (never
  raises; a ValueError from ``float(Retry-After: <HTTP-date>)`` used to crash
  the retry loop itself),
- :func:`backoff_seconds` - exponential backoff with capped ceiling and jitter
  (jitter avoids synchronized retry storms across many agents),
- :func:`is_retryable_exception` - which httpx/network errors deserve a retry.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any


def parse_retry_after(value: Any, fallback: float) -> float:
    """'12' -> 12.0; 'Wed, 21 Oct 2026 07:28:00 GMT' -> seconds from now;
    garbage/None -> fallback. Always returns a finite, capped (>=0) float."""
    if value is None:
        return max(0.0, fallback)
    try:
        text = str(value).strip()
        if not text:
            return max(0.0, fallback)
        try:
            return max(0.0, min(float(text), 300.0))
        except ValueError:
            pass
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return max(0.0, fallback)
        if when is None:
            return max(0.0, fallback)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, min(delta, 300.0))
    except Exception:
        return max(0.0, fallback)


def backoff_seconds(base: float, attempt: int, cap: float = 30.0, jitter: float = 0.25) -> float:
    """Exponential backoff with capped ceiling and proportional (downward-only
    around the cap) jitter so the result never exceeds ``cap``."""
    raw = min(max(0.0, base) * (2 ** max(0, attempt)), cap)
    # downward-only proportional jitter: never exceeds the cap, still breaks
    # synchronization between many concurrent agents
    return max(0.0, raw * (1.0 - jitter * random.random()))


async def sleep_backoff(base: float, attempt: int, cap: float = 30.0) -> float:
    """Sleep the backoff and return the duration actually slept (for logs)."""
    seconds = backoff_seconds(base, attempt, cap)
    if seconds > 0:
        await asyncio.sleep(seconds)
    return seconds


def is_retryable_exception(exc: BaseException) -> bool:
    """True for network-level errors that a retry can plausibly fix."""
    try:
        import httpx

        if isinstance(exc, httpx.TransportError):  # ConnectError, ReadError, RemoteProtocolError, DNS...
            return True
        if isinstance(exc, httpx.TimeoutException):
            return True
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        pass
    return False
