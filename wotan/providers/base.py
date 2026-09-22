"""Provider abstraction: one internal interface for all LLM gateways.

Messages, tools, tool calls, streaming, token usage and errors are normalized
here. Concrete adapters (OpenAI-compatible, Anthropic, GenericHTTPProvider and
Python plugins) implement :class:`LLMProvider`.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable

from .errors import LLMError  # re-export

Role = str  # system | user | assistant | tool


@dataclass
class Attachment:
    """Multimodal input: image or document (PDF...)."""

    kind: str  # image | document
    mime: str
    data_b64: str = ""
    path: str = ""
    url: str = ""
    name: str = ""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # for role='tool': which call this answers
    name: str = ""  # tool name for role='tool'
    attachments: list[Attachment] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        if self.attachments:
            d["attachments"] = [
                {"kind": a.kind, "mime": a.mime, "name": a.name} for a in self.attachments
            ]
        return d


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)  # JSON Schema (object)

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }

    def to_anthropic(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters or {"type": "object", "properties": {}},
        }


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class StreamEvent:
    """Normalized streaming event."""

    type: str  # text_delta | tool_call | usage | stop | error | raw
    text: str = ""
    tool_call: ToolCall | None = None
    usage: Usage | None = None
    stop_reason: str = ""
    raw: Any = None


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str = ""  # stop | tool_calls | length | error | content_filter
    usage: Usage = field(default_factory=Usage)
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ModelInfo:
    id: str
    name: str = ""
    context_window: int = 0
    tools: bool = True
    streaming: bool = True
    multimodal: bool = False


OnEvent = Callable[[StreamEvent], Awaitable[None] | None]


class LLMProvider(abc.ABC):
    """Single internal interface every adapter implements."""

    id: str = ""
    name: str = ""

    @abc.abstractmethod
    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        stream: bool | None = None,
        on_event: OnEvent | None = None,
    ) -> ChatResult:
        """Run one chat turn. With ``on_event`` the result is streamed."""

    async def list_models(self) -> list[ModelInfo]:
        return []

    def capabilities(self) -> dict[str, Any]:
        return {"native_tools": True, "streaming": True, "multimodal": False}

    async def aclose(self) -> None:
        return None
