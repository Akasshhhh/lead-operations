"""Stateless runtime boundary: validate streams without executing business effects."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    CompletionEvent,
    GenerationRequest,
    GenerationResponse,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from .provider import ErrorCode, LLMError, LLMProvider

_EVENT = TypeAdapter[StreamEvent](StreamEvent)


class ConversationLLMRuntime:
    """Local adapter for one provider; no retries, routing, memory, or persistence."""

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        # Snapshot nested mutable JSON so adapters cannot mutate caller-owned context.
        request = GenerationRequest.model_validate_json(request.model_dump_json())
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        text = ""
        calls: list[ToolCall] = []
        completed = False
        names = {tool.name for tool in request.tools}
        try:
            async with aclosing(self.provider.stream(request)) as source:
                while True:
                    try:
                        # Deadline applies across the whole generation, including consumer pauses.
                        async with asyncio.timeout_at(deadline):
                            raw = await anext(source)
                    except StopAsyncIteration:
                        if not completed:
                            raise ValueError("stream ended without completion") from None
                        break
                    event = _EVENT.validate_python(raw)
                    if completed:
                        raise ValueError("event after completion")
                    if isinstance(event, TextDelta):
                        text += event.text
                        if len(text) > 20_000:
                            raise ValueError("response text exceeds limit")
                    elif isinstance(event, ToolCallEvent):
                        if event.call.name not in names:
                            raise ValueError("undeclared tool")
                        if event.call.id in {call.id for call in calls} or len(calls) >= 20:
                            raise ValueError("duplicate or excessive tool calls")
                        calls.append(ToolCall.model_validate_json(event.call.model_dump_json()))
                    elif isinstance(event, CompletionEvent):
                        self._response(request, text, calls, event)
                        completed = True
                        # Validate EOF before exposing terminal success.
                        async with asyncio.timeout_at(deadline):
                            try:
                                await anext(source)
                            except StopAsyncIteration:
                                pass
                            else:
                                raise ValueError("event after completion")
                        await source.aclose()
                        yield event
                        break
                    yield event
        except TimeoutError:
            raise self._error(request, "timeout") from None
        except (ValidationError, ValueError):
            raise self._error(request, "invalid_output") from None
        except LLMError as exc:
            # Keep codes, but normalize correlation and never leak vendor messages.
            raise self._error(request, exc.code) from None
        except Exception:
            raise self._error(request, "provider_error") from None
        # CancelledError/GeneratorExit propagate; aclosing releases provider resources.

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        request = GenerationRequest.model_validate_json(request.model_dump_json())
        text = ""
        calls: list[ToolCall] = []
        async with aclosing(self.stream(request)) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    text += event.text
                elif isinstance(event, ToolCallEvent):
                    calls.append(event.call)
                elif isinstance(event, CompletionEvent):
                    return self._response(request, text, calls, event)
        raise self._error(request, "invalid_output")

    def _response(
        self, request: GenerationRequest, text: str, calls: list[ToolCall], event: CompletionEvent
    ) -> GenerationResponse:
        return GenerationResponse(
            request_id=request.request_id,
            provider=self.provider.name,
            model=self.provider.model,
            text=text,
            tool_calls=tuple(calls),
            finish_reason=event.finish_reason,
            usage=event.usage,
        )

    def _error(self, request: GenerationRequest, code: ErrorCode) -> LLMError:
        return LLMError(code, request_id=request.request_id, provider=self.provider.name)
