"""LLM provider adapters."""

from .base import (
    Attachment,
    ChatResult,
    LLMProvider,
    Message,
    ModelInfo,
    StreamEvent,
    ToolCall,
    ToolSpec,
    Usage,
)
from .errors import LLMAuthError, LLMContractError, LLMError, LLMRateLimitError, LLMTimeoutError
from .generic_http import GenericHTTPProvider, extract_path
from .registry import ProviderRegistry
from .text_tools import parse_tool_calls, serialize_tool_call, validate_arguments

__all__ = [
    "Attachment",
    "ChatResult",
    "LLMProvider",
    "Message",
    "ModelInfo",
    "StreamEvent",
    "ToolCall",
    "ToolSpec",
    "Usage",
    "LLMAuthError",
    "LLMContractError",
    "LLMError",
    "LLMRateLimitError",
    "LLMTimeoutError",
    "GenericHTTPProvider",
    "extract_path",
    "ProviderRegistry",
    "parse_tool_calls",
    "serialize_tool_call",
    "validate_arguments",
]
