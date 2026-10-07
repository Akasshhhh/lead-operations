"""OpenAI Chat Completions text/function streaming adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx

from .contracts import (
    CompletionEvent,
    GenerationRequest,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
)
from .http_provider import HTTPProvider, integer, json_value, object_value, string


class OpenAILLMProvider(HTTPProvider):
    name = "openai"
    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self, client: httpx.AsyncClient, *, api_key: str, model: str = "gpt-4.1-mini-2025-04-14"
    ) -> None:
        super().__init__(client, api_key=api_key, model=model)

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key.get_secret_value()}",
            "Accept": "text/event-stream",
        }

    def payload(self, request: GenerationRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.context.system_instruction}
        ]
        for message in request.context.messages:
            item: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": json.dumps(call.arguments, allow_nan=False),
                        },
                    }
                    for call in message.tool_calls
                ]
            messages.append(item)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_completion_tokens": request.max_output_tokens,
            "temperature": request.temperature,
            "store": False,
        }
        if self.name == "openai" and self.model in {"gpt-5-mini", "gpt-5-mini-2025-08-07"}:
            # GPT-5 mini rejects temperature. Minimal reasoning fits the existing
            # bounded voice-turn budget without changing domain/tool ownership.
            payload.pop("temperature", None)
            payload["reasoning_effort"] = "minimal"
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                    },
                }
                for tool in request.tools
            ]
        return payload

    async def parse(self, frames: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
        calls: dict[int, dict[str, str]] = {}
        reason: str | None = None
        usage: Usage | None = None
        done = False
        buffered = 0
        async for frame in frames:
            if done:
                raise ValueError("frame after DONE")
            if frame == "[DONE]":
                done = True
                continue
            chunk = object_value(json_value(frame))
            if "error" in chunk:
                raise ValueError("unexpected error frame")
            if chunk.get("usage") is not None:
                if usage is not None:
                    raise ValueError("duplicate usage")
                raw_usage = object_value(chunk["usage"])
                usage = Usage(
                    input_tokens=integer(raw_usage["prompt_tokens"]),
                    output_tokens=integer(raw_usage["completion_tokens"]),
                )
            choices = chunk["choices"]
            if not isinstance(choices, list):
                raise ValueError("invalid choices")
            if not choices:
                if reason is None or usage is None:
                    raise ValueError("unexpected empty choices")
                continue
            if reason is not None or len(choices) != 1:
                raise ValueError("extra completion choice")
            choice = object_value(choices[0])
            if integer(choice["index"]) != 0:
                raise ValueError("invalid choice index")
            delta = object_value(choice["delta"])
            if delta.get("refusal"):
                raise ValueError("refusal is not a completed business response")
            if delta.get("content") is not None:
                text = string(delta["content"])
                if text:
                    yield TextDelta(text=text)
            fragments = delta.get("tool_calls") or []
            if not isinstance(fragments, list):
                raise ValueError("invalid tool fragments")
            for raw in fragments:
                fragment = object_value(raw)
                index = integer(fragment["index"])
                if index >= 20:
                    raise ValueError("too many tools")
                call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if fragment.get("type") not in (None, "function"):
                    raise ValueError("unsupported tool type")
                if fragment.get("id"):
                    value = string(fragment["id"])
                    if call["id"] and call["id"] != value:
                        raise ValueError("changed tool ID")
                    call["id"] = value
                function = object_value(fragment.get("function", {}))
                for key in ("name", "arguments"):
                    if function.get(key) is not None:
                        value = string(function[key])
                        call[key] += value
                        buffered += len(value.encode("utf-8"))
                if buffered > 262_144 or len(call["id"]) > 160 or len(call["name"]) > 64:
                    raise ValueError("tool fragments exceed bounds")
            if choice.get("finish_reason") is not None:
                reason = string(choice["finish_reason"])
                if reason not in {"stop", "length", "tool_calls"}:
                    raise ValueError("unsupported finish reason")
        if not done or reason is None:
            raise ValueError("incomplete OpenAI stream")
        if bool(calls) != (reason == "tool_calls"):
            raise ValueError("inconsistent tool finish")
        for index in sorted(calls):
            call = calls[index]
            yield ToolCallEvent(
                call=ToolCall(
                    id=call["id"],
                    name=call["name"],
                    arguments=object_value(json_value(call["arguments"])),
                )
            )
        yield CompletionEvent.model_validate({"finish_reason": reason, "usage": usage})
