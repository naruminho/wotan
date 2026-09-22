"""LLM errors with retry semantics and clear ERROR/WHY/HOW TO FIX messages.

Lives in its own module (not inside providers) so both the auth and provider
layers can use it without circular imports.
"""

from __future__ import annotations

from typing import Any


class LLMError(Exception):
    message: str
    code: str = "llm_error"
    status: int | None = None
    retryable: bool = False
    raw: Any = None
    why: str = ""
    how_to_fix: str = ""

    def __init__(self, message: str, code: str = "llm_error", status: int | None = None,
                 retryable: bool = False, raw: Any = None, why: str = "", how_to_fix: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retryable = retryable
        self.raw = raw
        self.why = why
        self.how_to_fix = how_to_fix

    def __str__(self) -> str:  # pragma: no cover - trivial
        parts = [f"ERROR: {self.message}"]
        if self.why:
            parts.append(f"WHY: {self.why}")
        if self.how_to_fix:
            parts.append(f"HOW TO FIX: {self.how_to_fix}")
        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "status": self.status,
            "retryable": self.retryable,
            "why": self.why,
            "how_to_fix": self.how_to_fix,
        }


class LLMAuthError(LLMError):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(message, code="auth_error", retryable=False, **kw)


class LLMRateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None, **kw: Any) -> None:
        super().__init__(message, code="rate_limit", retryable=True, **kw)
        self.retry_after = retry_after


class LLMTimeoutError(LLMError):
    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(message, code="timeout", retryable=True, **kw)


class LLMContractError(LLMError):
    """The gateway answered, but the configured extraction paths came back empty."""

    def __init__(self, message: str, **kw: Any) -> None:
        super().__init__(message, code="contract_error", retryable=False, **kw)
