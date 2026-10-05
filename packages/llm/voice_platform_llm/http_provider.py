"""Shared bounded SSE transport; HTTP clients are owned by the runtime caller."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing
from typing import Any, cast

import httpx
from pydantic import SecretStr, ValidationError

from .contracts import GenerationRequest, StreamEvent
from .provider import ErrorCode, LLMError


class ProviderStreamError(RuntimeError):
    """Content-free wire error, normalized with request identity by the transport."""

    def __init__(self, code: ErrorCode) -> None:
        super().__init__("provider stream failed")
        self.code = code


def object_value(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return cast(dict[str, Any], value)


def integer(value: object) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("expected nonnegative integer")
    return value


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("expected string")
    return value


def json_value(value: str) -> Any:
    def invalid_constant(_: str) -> None:
        raise ValueError("nonfinite JSON")

    return json.loads(value, parse_constant=invalid_constant)


async def sse_data(response: httpx.Response) -> AsyncGenerator[str, None]:
    """Decode split UTF-8/CRLF frames, comments and multiline data, with bounds."""
    pending = b""
    data: list[str] = []
    frame_size = total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > 8_388_608:
            raise ValueError("stream exceeds 8 MiB")
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            if line.endswith(b"\r"):
                line = line[:-1]
            frame_size += len(line)
            if frame_size > 262_144:
                raise ValueError("SSE frame exceeds 256 KiB")
            decoded = line.decode("utf-8", errors="strict")
            if not decoded:
                if data:
                    yield "\n".join(data)
                data = []
                frame_size = 0
            elif decoded.startswith("data:"):
                payload = decoded[5:]
                data.append(payload[1:] if payload.startswith(" ") else payload)
        if len(pending) + frame_size > 262_144:
            raise ValueError("SSE frame exceeds 256 KiB")
    if pending or data:
        raise ValueError("incomplete SSE frame")


class HTTPProvider:
    name: str
    endpoint: str

    def __init__(self, client: httpx.AsyncClient, *, api_key: str, model: str) -> None:
        if not api_key or any(ord(char) < 33 or ord(char) > 126 for char in api_key):
            raise ValueError("provider key must be nonblank printable ASCII")
        if not model.strip() or len(model) > 128 or any(ord(char) < 33 for char in model):
            raise ValueError("invalid provider model")
        self.client = client
        self._key = SecretStr(api_key)
        self.model = model

    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    def payload(self, request: GenerationRequest) -> dict[str, Any]:
        raise NotImplementedError

    def parse(self, frames: AsyncIterator[str]) -> AsyncGenerator[StreamEvent, None]:
        raise NotImplementedError

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        request = GenerationRequest.model_validate_json(request.model_dump_json())
        try:
            async with asyncio.timeout(request.timeout_seconds):
                async with self.client.stream(
                    "POST",
                    self.endpoint,
                    headers=self.headers() | {"X-Request-ID": str(request.request_id)},
                    json=self.payload(request),
                    timeout=httpx.Timeout(request.timeout_seconds),
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        code: ErrorCode = "provider_error"
                        if response.status_code == 429:
                            code = "rate_limited"
                        elif response.status_code == 408:
                            code = "timeout"
                        elif response.status_code >= 500:
                            code = "unavailable"
                        raise LLMError(code, request_id=request.request_id, provider=self.name)
                    if (
                        response.headers.get("content-type", "").split(";")[0].strip().lower()
                        != "text/event-stream"
                    ):
                        raise ValueError("expected SSE response")
                    async with aclosing(sse_data(response)) as frames:
                        async with aclosing(self.parse(frames)) as events:
                            terminal: StreamEvent | None = None
                            async for event in events:
                                if event.type == "completed":
                                    terminal = event
                                else:
                                    yield event
                # Release the HTTP response before exposing terminal completion.
                if terminal is None:
                    raise ValueError("missing completion")
                yield terminal
        except ProviderStreamError as exc:
            raise LLMError(exc.code, request_id=request.request_id, provider=self.name) from None
        except (TimeoutError, httpx.TimeoutException):
            raise LLMError("timeout", request_id=request.request_id, provider=self.name) from None
        except httpx.RequestError:
            raise LLMError(
                "unavailable", request_id=request.request_id, provider=self.name
            ) from None
        except (ValueError, KeyError, TypeError, ValidationError, UnicodeError):
            raise LLMError(
                "invalid_output", request_id=request.request_id, provider=self.name
            ) from None
