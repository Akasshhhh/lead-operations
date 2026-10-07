"""HTTPX adapter integration with fragmented OpenAI/OpenRouter wire fixtures."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from uuid import uuid4

import httpx
import pytest
from voice_platform_llm import (
    ContextMessage,
    ConversationLLMRuntime,
    GenerationRequest,
    LLMError,
    LLMRouter,
    OpenAILLMProvider,
    OpenRouterLLMProvider,
    PromptContext,
    ProviderSlot,
    RouterPolicy,
    ToolCall,
    ToolDefinition,
)


def request(*, tools: bool = False, timeout: float = 1) -> GenerationRequest:
    return GenerationRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=uuid4(),
        context=PromptContext(
            system_instruction="Use backend facts",
            messages=(ContextMessage(role="user", content="Hello"),),
        ),
        tools=(
            ToolDefinition(
                name="lookup", description="Read backend", parameters={"type": "object"}
            ),
        )
        if tools
        else (),
        timeout_seconds=timeout,
    )


def frame(value: object) -> bytes:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return f"data: {text}\r\n\r\n".encode()


def delta(value: object, reason: str | None = None) -> dict[str, object]:
    return {"choices": [{"index": 0, "delta": value, "finish_reason": reason}]}


def usage_frame(vendor: str, reason: str = "stop") -> dict[str, object]:
    chunk = (
        delta({"content": "", "role": "assistant"}, reason)
        if vendor == "openrouter"
        else {"choices": []}
    )
    return chunk | {"usage": {"prompt_tokens": 10, "completion_tokens": 4}}


def body_for(vendor: str, text: str = "Hello 世界") -> bytes:
    return (
        b": OPENROUTER PROCESSING\r\n\r\n"
        + frame(delta({"role": "assistant", "content": ""}))
        + frame(delta({"content": text}))
        + frame(delta({}, "stop"))
        + frame(usage_frame(vendor))
        + frame("[DONE]")
    )


def adapter(vendor: str, client: httpx.AsyncClient, key: str = "test") -> OpenAILLMProvider:
    return (
        OpenAILLMProvider(client, api_key=key)
        if vendor == "openai"
        else OpenRouterLLMProvider(client, api_key=key)
    )


class Body(httpx.AsyncByteStream):
    def __init__(self, content: bytes, *, delay: bool = False, error: bool = False) -> None:
        self.content = content
        self.delay = delay
        self.error = error
        self.closed = False
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        if self.delay:
            await asyncio.Event().wait()
        for index in range(0, len(self.content), 7):
            yield self.content[index : index + 7]
        if self.error:
            raise httpx.ReadError("SECRET connection failed")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
async def test_vendor_text_fragmentation_usage_identity_and_cleanup(vendor: str) -> None:
    body = Body(body_for(vendor))
    received: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        received.append(incoming)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream; charset=utf-8"}, stream=body
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = adapter(vendor, client, "test-secret")
        data = request()
        result = await ConversationLLMRuntime(provider).generate(data)
        assert (
            result.text == "Hello 世界"
            and result.provider == vendor
            and result.request_id == data.request_id
        )
        assert (
            result.usage is not None
            and result.usage.input_tokens == 10
            and result.usage.output_tokens == 4
        )
        payload = json.loads(received[0].content)
        assert payload["messages"][0] == {
            "role": "system",
            "content": data.context.system_instruction,
        }
        assert (
            payload["max_completion_tokens" if vendor == "openai" else "max_tokens"]
            == data.max_output_tokens
        )
        assert received[0].headers["authorization"] == "Bearer test-secret"
        assert received[0].headers["x-request-id"] == str(data.request_id)
        assert body.closed and not client.is_closed
        assert "test-secret" not in repr(provider)
        if vendor == "openrouter":
            assert payload["model"] == "meta-llama/llama-3.3-70b-instruct"
            assert payload["provider"] == {"require_parameters": True, "allow_fallbacks": False}
            assert received[0].url == "https://openrouter.ai/api/v1/chat/completions"
            assert "store" not in payload and "max_completion_tokens" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-5-mini", "gpt-5-mini-2025-08-07"])
async def test_gpt5_mini_uses_supported_parameters_and_keeps_tool_stream_contract(
    model: str,
) -> None:
    data = request(tools=True)
    body = Body(
        frame(
            delta(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "lookup-1",
                            "type": "function",
                            "function": {"name": "lookup", "arguments": "{}"},
                        }
                    ]
                }
            )
        )
        + frame(delta({}, "tool_calls"))
        + frame(usage_frame("openai", "tool_calls"))
        + frame("[DONE]")
    )

    def handler(incoming: httpx.Request) -> httpx.Response:
        payload = json.loads(incoming.content)
        # Model the real API rejection that left a user turn pending.
        if "temperature" in payload:
            return httpx.Response(400, json={"error": {"param": "temperature"}})
        assert payload["model"] == model
        assert payload["reasoning_effort"] == "minimal"
        assert payload["max_completion_tokens"] == data.max_output_tokens
        assert payload["tools"][0]["function"]["name"] == "lookup"
        assert payload["messages"][1]["content"] == "Hello"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await ConversationLLMRuntime(
            OpenAILLMProvider(client, api_key="test", model=model)
        ).generate(data)
    assert len(result.tool_calls) == 1 and result.tool_calls[0].name == "lookup"
    assert result.tool_calls[0].id == "lookup-1" and result.finish_reason == "tool_calls"
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vendor,model",
    [
        ("openai", "gpt-4.1-mini-2025-04-14"),
        ("openrouter", "meta-llama/llama-3.3-70b-instruct"),
        ("openrouter", "openai/gpt-5-mini"),
    ],
)
async def test_other_configured_models_keep_their_existing_sampling_contract(
    vendor: str, model: str
) -> None:
    async with httpx.AsyncClient() as client:
        provider = (OpenAILLMProvider if vendor == "openai" else OpenRouterLLMProvider)(
            client, api_key="test", model=model
        )
        payload = provider.payload(request())
    assert payload["temperature"] == 0
    assert "reasoning_effort" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
async def test_fragmented_parallel_tool_calls_and_history(vendor: str) -> None:
    calls = (
        ToolCall(id="call-1", name="lookup", arguments={"confirmed": False, "years": 0}),
        ToolCall(id="call-2", name="lookup", arguments={"x": "é"}),
    )
    values = [
        delta(
            {
                "tool_calls": [
                    {
                        "index": index,
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": "{"},
                    }
                    for index, call in enumerate(calls)
                ]
            }
        ),
        delta(
            {
                "tool_calls": [
                    {"index": index, "function": {"arguments": json.dumps(call.arguments)[1:]}}
                    for index, call in enumerate(calls)
                ]
            }
        ),
        delta({}, "tool_calls"),
        usage_frame(vendor, "tool_calls"),
        "[DONE]",
    ]
    body = Body(b"".join(frame(value) for value in values))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=body
            )
        )
    ) as client:
        provider = adapter(vendor, client)
        data = request(tools=True)
        result = await ConversationLLMRuntime(provider).generate(data)
        assert result.tool_calls == calls and result.finish_reason == "tool_calls"
        context = PromptContext(
            system_instruction=data.context.system_instruction,
            messages=(
                *data.context.messages,
                ContextMessage(role="assistant", tool_calls=calls),
                ContextMessage(role="tool", tool_call_id="call-1", content="one"),
                ContextMessage(role="tool", tool_call_id="call-2", content="two"),
            ),
        )
        payload = provider.payload(data.model_copy(update={"context": context}))
        assert payload["messages"][-1]["tool_call_id"] == "call-2"
        assert (
            json.loads(payload["messages"][-3]["tool_calls"][0]["function"]["arguments"])
            == calls[0].arguments
        )
        assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (400, "provider_error"),
        (401, "provider_error"),
        (402, "provider_error"),
        (403, "provider_error"),
        (429, "rate_limited"),
        (408, "timeout"),
        (500, "unavailable"),
        (503, "unavailable"),
        (302, "provider_error"),
    ],
)
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
async def test_http_errors_redact_vendor_content(status: int, code: str, vendor: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                status,
                content=b"SECRET key and prompt",
                headers={"location": "https://evil.invalid"},
            )
        )
    ) as client:
        with pytest.raises(LLMError) as caught:
            await ConversationLLMRuntime(adapter(vendor, client)).generate(request())
        assert caught.value.code == code and "SECRET" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
@pytest.mark.parametrize(
    "body",
    [b"data: invalid\n\n", b"data: {}\n", b"data: \xff\n\n", b"data: " + b"x" * 262_145 + b"\n\n"],
)
async def test_malformed_frames_are_controlled(vendor: str, body: bytes) -> None:
    stream = Body(body)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=stream
            )
        )
    ) as client:
        with pytest.raises(LLMError, match="invalid_output"):
            await ConversationLLMRuntime(adapter(vendor, client)).generate(request())
        assert stream.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_and_cancellation_close_http_stream(vendor: str, cancel: bool) -> None:
    body = Body(b"", delay=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, stream=body
            )
        )
    ) as client:
        task = asyncio.create_task(
            ConversationLLMRuntime(adapter(vendor, client)).generate(request(timeout=0.05))
        )
        await body.started.wait()
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(LLMError, match="timeout"):
                await task
        assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        ("server_error", "unavailable"),
        (503, "unavailable"),
        (429, "rate_limited"),
        (408, "timeout"),
        (402, "provider_error"),
    ],
)
async def test_openrouter_in_stream_errors_are_content_free(status: object, code: str) -> None:
    content = frame(
        {
            "error": {"code": status, "message": "SECRET"},
            "choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "error"}],
        }
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
    ) as client:
        with pytest.raises(LLMError) as caught:
            await ConversationLLMRuntime(OpenRouterLLMProvider(client, api_key="test")).generate(
                request()
            )
        assert caught.value.code == code and "SECRET" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
async def test_truncated_tool_json_never_becomes_a_proposal(vendor: str) -> None:
    content = (
        frame(
            delta(
                {
                    "tool_calls": [
                        {"index": 0, "id": "1", "function": {"name": "lookup", "arguments": "{"}}
                    ]
                }
            )
        )
        + frame(delta({}, "tool_calls"))
        + frame("[DONE]")
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
    ) as client:
        with pytest.raises(LLMError, match="invalid_output"):
            await ConversationLLMRuntime(adapter(vendor, client)).generate(request(tools=True))


@pytest.mark.asyncio
async def test_actual_adapters_failover_preserves_context_and_one_response() -> None:
    received: list[httpx.Request] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        received.append(incoming)
        if incoming.url.host == "api.openai.com":
            return httpx.Response(503, content=b"failure")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Body(body_for("openrouter", "Fallback")),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = LLMRouter(
            (
                ProviderSlot(OpenAILLMProvider(client, api_key="test")),
                ProviderSlot(OpenRouterLLMProvider(client, api_key="test")),
            ),
            policy=RouterPolicy(max_attempts_per_provider=1),
        )
        data = request()
        original = data.model_dump_json()
        result = await router.generate(data)
        assert result.text == "Fallback" and result.provider == "openrouter"
        assert result.request_id == data.request_id and data.model_dump_json() == original
        assert len(received) == 2
        assert all(
            incoming.headers["x-request-id"] == str(data.request_id) for incoming in received
        )
        assert (
            json.loads(received[0].content)["messages"]
            == json.loads(received[1].content)["messages"]
        )
        assert router.health()[0].failover_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("buffered", [False, True])
async def test_partial_disconnect_recovery_depends_on_output_exposure(buffered: bool) -> None:
    primary = Body(frame(delta({"content": "partial"})), error=True)
    hosts: list[str | None] = []

    def handler(incoming: httpx.Request) -> httpx.Response:
        hosts.append(incoming.url.host)
        stream = (
            primary
            if incoming.url.host == "api.openai.com"
            else Body(body_for("openrouter", "backup"))
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        routed = LLMRouter(
            (
                ProviderSlot(OpenAILLMProvider(client, api_key="test")),
                ProviderSlot(OpenRouterLLMProvider(client, api_key="test")),
            ),
            policy=RouterPolicy(max_attempts_per_provider=1),
        )
        if buffered:
            result = await routed.generate(request())
            assert result.text == "backup" and result.provider == "openrouter" and len(hosts) == 2
        else:
            async with aclosing(routed.stream(request())) as events:
                first = await anext(events)
                assert first.type == "text_delta" and first.text == "partial"
                with pytest.raises(LLMError, match="unavailable"):
                    await anext(events)
            assert len(hosts) == 1
        assert primary.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("vendor", ["openai", "openrouter"])
async def test_completion_requires_done_and_no_trailing_data(vendor: str) -> None:
    content = body_for(vendor)
    for malformed in (
        content[: -len(frame("[DONE]"))],
        content + frame(delta({"content": "extra"})),
    ):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _, data=malformed: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, content=data
                )
            )
        ) as client:
            with pytest.raises(LLMError, match="invalid_output"):
                await ConversationLLMRuntime(adapter(vendor, client)).generate(request())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        delta({"content": "extra"}, "stop"),
        delta({"content": ""}, "length"),
        delta({"tool_calls": []}, "stop"),
    ],
)
async def test_openrouter_accounting_exception_does_not_hide_content(
    change: dict[str, object],
) -> None:
    content = (
        frame(delta({"content": "text"}))
        + frame(delta({}, "stop"))
        + frame(change | {"usage": {"prompt_tokens": 10, "completion_tokens": 4}})
        + frame("[DONE]")
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=content
            )
        )
    ) as client:
        with pytest.raises(LLMError, match="invalid_output"):
            await ConversationLLMRuntime(OpenRouterLLMProvider(client, api_key="test")).generate(
                request()
            )


@pytest.mark.asyncio
async def test_openai_keeps_strict_single_completion() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=body_for("openrouter")
            )
        )
    ) as client:
        with pytest.raises(LLMError, match="invalid_output"):
            await ConversationLLMRuntime(OpenAILLMProvider(client, api_key="test")).generate(
                request()
            )
