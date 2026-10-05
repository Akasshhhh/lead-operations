"""OpenRouter's Chat Completions path, including its terminal accounting frame."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Any

import httpx

from .contracts import GenerationRequest, StreamEvent
from .http_provider import ProviderStreamError, integer, json_value, object_value
from .openai import OpenAILLMProvider
from .provider import ErrorCode


class OpenRouterLLMProvider(OpenAILLMProvider):
    name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str = "meta-llama/llama-3.3-70b-instruct",
    ) -> None:
        super().__init__(client, api_key=api_key, model=model)

    def payload(self, request: GenerationRequest) -> dict[str, Any]:
        payload = super().payload(request)
        payload["max_tokens"] = payload.pop("max_completion_tokens")
        payload.pop("store")
        # Domain/runtime retries stay visible in our router, not a nested vendor fallback.
        payload["provider"] = {"require_parameters": True, "allow_fallbacks": False}
        return payload

    async def _normalized(self, frames: AsyncIterator[str]) -> AsyncGenerator[str, None]:
        reason: str | None = None
        async for frame in frames:
            if frame == "[DONE]":
                yield frame
                continue
            chunk = object_value(json_value(frame))
            if "error" in chunk:
                error = object_value(chunk["error"])
                status = error.get("code")
                code: ErrorCode = "provider_error"
                if status in (429, "429", "rate_limit_exceeded", "rate_limited"):
                    code = "rate_limited"
                elif status in (408, "408", "timeout"):
                    code = "timeout"
                elif status in ("server_error", "service_unavailable") or (
                    type(status) is int and status >= 500
                ):
                    code = "unavailable"
                raise ProviderStreamError(code)
            choices = chunk.get("choices")
            if isinstance(choices, list) and len(choices) == 1:
                choice = object_value(choices[0])
                if reason is not None and chunk.get("usage") is not None:
                    delta = object_value(choice["delta"])
                    if (
                        integer(choice["index"]) != 0
                        or choice.get("finish_reason") != reason
                        or delta.get("content") not in (None, "")
                        or delta.get("role") not in (None, "assistant")
                        or set(delta) - {"role", "content"}
                    ):
                        raise ValueError("invalid terminal accounting frame")
                    # OpenRouter repeats finish_reason on its usage chunk. It is accounting,
                    # not a second completion. Preserve strict OpenAI behavior elsewhere.
                    chunk["choices"] = []
                elif choice.get("finish_reason") is not None:
                    reason = choice["finish_reason"]
            yield json.dumps(chunk, ensure_ascii=False, allow_nan=False)

    async def parse(self, frames: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
        async with aclosing(self._normalized(frames)) as normalized:
            async with aclosing(super().parse(normalized)) as events:
                async for event in events:
                    yield event
