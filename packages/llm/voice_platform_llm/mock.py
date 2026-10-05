"""Replayable, concurrency-safe fixtures; no credentials or inferred business truth."""

import asyncio
from collections.abc import AsyncGenerator

from pydantic import Field

from .contracts import (
    CompletionEvent,
    Contract,
    FinishReason,
    GenerationRequest,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from .provider import ErrorCode, LLMError


class MockScript(Contract):
    text: str = Field(default="How can I help you?", max_length=20_000)
    tool_calls: tuple[ToolCall, ...] = Field(default=(), max_length=20)
    chunk_size: int = Field(default=8, ge=1, le=20_000, strict=True)
    delay_seconds: float = Field(default=0, ge=0, le=300)
    fail_after_events: int | None = Field(default=None, ge=0, strict=True)
    error_code: ErrorCode = "unavailable"
    finish_reason: FinishReason | None = None


class MockLLMProvider:
    name = "mock"
    model = "deterministic-v1"

    def __init__(self, script: MockScript | None = None) -> None:
        self._script = (script or MockScript()).model_dump_json()

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        script = MockScript.model_validate_json(self._script)
        events: list[StreamEvent] = [
            TextDelta(text=script.text[start : start + script.chunk_size])
            for start in range(0, len(script.text), script.chunk_size)
        ]
        events.extend(ToolCallEvent(call=call) for call in script.tool_calls)
        events.append(
            CompletionEvent(
                finish_reason=script.finish_reason
                or ("tool_calls" if script.tool_calls else "stop")
            )
        )
        for index, event in enumerate(events):
            await asyncio.sleep(script.delay_seconds)
            if script.fail_after_events is not None and index >= script.fail_after_events:
                raise LLMError(script.error_code, request_id=request.request_id, provider=self.name)
            yield event
