"""OpenAI-compatible request/response models.

Switchboard speaks the OpenAI Chat Completions wire format on its public API so
that any existing OpenAI SDK can point at it by changing ``base_url``. Provider
adapters translate to and from their native formats.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool", "developer"]
FinishReason = Literal["stop", "length", "tool_calls", "content_filter", "error"]


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Role
    content: str | list[dict[str, Any]] | None = None
    name: str | None = None

    def text(self) -> str:
        """Flatten content into plain text (multi-part content keeps only text parts)."""
        if self.content is None:
            return ""
        if isinstance(self.content, str):
            return self.content
        return "".join(p.get("text", "") for p in self.content if p.get("type") == "text")


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    stop: str | list[str] | None = None
    stream: bool = False
    user: str | None = None

    def stop_list(self) -> list[str] | None:
        if self.stop is None:
            return None
        return [self.stop] if isinstance(self.stop, str) else self.stop


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    @classmethod
    def of(cls, prompt: int, completion: int) -> Usage:
        return cls(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        )


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str | None = None


class Choice(BaseModel):
    index: int = 0
    message: AssistantMessage
    finish_reason: FinishReason | None = "stop"


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


class ChatCompletionResponse(BaseModel):
    id: str = Field(default_factory=new_completion_id)
    object: Literal["chat.completion"] = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[Choice]
    usage: Usage = Field(default_factory=Usage)

    def text(self) -> str:
        if not self.choices:
            return ""
        return self.choices[0].message.content or ""


class Delta(BaseModel):
    role: Literal["assistant"] | None = None
    content: str | None = None


class ChunkChoice(BaseModel):
    index: int = 0
    delta: Delta
    finish_reason: FinishReason | None = None


class ChatCompletionChunk(BaseModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: list[ChunkChoice] = Field(default_factory=list)
    usage: Usage | None = None


class ErrorBody(BaseModel):
    message: str
    type: str
    code: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
