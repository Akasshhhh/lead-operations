"""Vendor-independent, JSON-serializable generation and prompt contracts."""

from __future__ import annotations

import json
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]*$")]
FinishReason = Literal["stop", "tool_calls", "length"]


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )

    @model_validator(mode="after")
    def safe_json(self) -> Self:
        try:
            encoded = json.dumps(self.model_dump(mode="json"), allow_nan=False, ensure_ascii=False)
            encoded.encode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("invalid JSON content") from exc

        def has_nul(value: object) -> bool:
            if isinstance(value, str):
                return "\x00" in value
            if isinstance(value, dict):
                return any(has_nul(key) or has_nul(item) for key, item in value.items())
            if isinstance(value, (tuple, list)):
                return any(has_nul(item) for item in value)
            return False

        if has_nul(self.model_dump(mode="json")):
            raise ValueError("NUL is not supported")
        if len(encoded.encode("utf-8")) > 262_144:
            raise ValueError("LLM contract exceeds 256 KiB")
        return self


class ToolDefinition(Contract):
    name: Name
    description: str = Field(min_length=1, max_length=4000)
    parameters: dict[str, JsonValue]

    @model_validator(mode="after")
    def object_parameters(self) -> Self:
        if self.parameters.get("type") != "object":
            raise ValueError("tool parameters must describe an object")
        return self


class ToolCall(Contract):
    id: str = Field(min_length=1, max_length=160)
    name: Name
    arguments: dict[str, JsonValue]


class ContextMessage(Contract):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(default="", max_length=20_000)
    tool_calls: tuple[ToolCall, ...] = Field(default=(), max_length=20)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def role_payload(self) -> Self:
        if self.role != "assistant" and self.tool_calls:
            raise ValueError("only assistant messages may propose tools")
        if (self.role == "tool") != (self.tool_call_id is not None):
            raise ValueError("tool results require a tool_call_id; other roles forbid it")
        if not self.content.strip() and not self.tool_calls:
            raise ValueError("message requires content or tool calls")
        return self


class PromptContext(Contract):
    # Trusted instructions are separate from untrusted history/tool output.
    system_instruction: str = Field(min_length=1, max_length=20_000)
    messages: tuple[ContextMessage, ...] = Field(default=(), max_length=200)

    @model_validator(mode="after")
    def tool_history(self) -> Self:
        pending: set[str] = set()
        seen: set[str] = set()
        for message in self.messages:
            if message.role == "tool":
                if message.tool_call_id not in pending:
                    raise ValueError("tool result must match an unanswered tool call")
                pending.remove(message.tool_call_id)
            else:
                if pending:
                    raise ValueError("tool calls require results before the next message")
                for call in message.tool_calls:
                    if call.id in seen:
                        raise ValueError("duplicate tool call ID in context")
                    pending.add(call.id)
                    seen.add(call.id)
        if pending:
            raise ValueError("context contains unanswered tool calls")
        return self


class GenerationRequest(Contract):
    request_id: UUID
    conversation_id: UUID
    turn_id: UUID
    context: PromptContext
    tools: tuple[ToolDefinition, ...] = Field(default=(), max_length=20)
    max_output_tokens: int = Field(default=512, ge=1, le=32_768, strict=True)
    temperature: float = Field(default=0, ge=0, le=2)
    timeout_seconds: float = Field(default=10, gt=0, le=300)

    @model_validator(mode="after")
    def unique_tools(self) -> Self:
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError("duplicate tool definition")
        return self


class Usage(Contract):
    input_tokens: int = Field(ge=0, strict=True)
    output_tokens: int = Field(ge=0, strict=True)


class TextDelta(Contract):
    type: Literal["text_delta"] = "text_delta"
    text: str = Field(min_length=1, max_length=20_000)


class ToolCallEvent(Contract):
    # Adapters buffer vendor argument fragments and emit only complete JSON objects.
    type: Literal["tool_call"] = "tool_call"
    call: ToolCall


class CompletionEvent(Contract):
    type: Literal["completed"] = "completed"
    finish_reason: FinishReason
    usage: Usage | None = None


StreamEvent = Annotated[TextDelta | ToolCallEvent | CompletionEvent, Field(discriminator="type")]


class GenerationResponse(Contract):
    request_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=20_000)
    tool_calls: tuple[ToolCall, ...] = Field(default=(), max_length=20)
    finish_reason: FinishReason
    usage: Usage | None = None

    @model_validator(mode="after")
    def completion_payload(self) -> Self:
        ids = [call.id for call in self.tool_calls]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate tool call ID")
        if bool(self.tool_calls) != (self.finish_reason == "tool_calls"):
            raise ValueError("finish reason must match tool calls")
        if not self.text and not self.tool_calls and self.finish_reason != "length":
            raise ValueError("empty completed response")
        return self
