"""Retry, partial-output, capability, circuit, and cancellation invariants."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import uuid4

import pytest
from voice_platform_llm import (
    CompletionEvent,
    ContextMessage,
    GenerationRequest,
    LLMError,
    LLMRouter,
    MockLLMProvider,
    MockScript,
    PromptContext,
    ProviderCapabilities,
    ProviderSlot,
    RouterPolicy,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolDefinition,
)


def request(*, timeout: float = 1, tools: bool = False) -> GenerationRequest:
    return GenerationRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=uuid4(),
        context=PromptContext(
            system_instruction="Trusted",
            messages=(ContextMessage(role="user", content="Caller context"),),
        ),
        timeout_seconds=timeout,
        tools=(
            ToolDefinition(
                name="lookup", description="Read backend", parameters={"type": "object"}
            ),
        )
        if tools
        else (),
    )


class TrackingProvider:
    model = "test-model"

    def __init__(self, name: str, *scripts: MockScript) -> None:
        self.name = name
        self.scripts = scripts or (MockScript(),)
        self.requests: list[str] = []
        self.closes = 0

    async def stream(self, data: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        index = len(self.requests)
        self.requests.append(data.model_dump_json())
        script = self.scripts[min(index, len(self.scripts) - 1)]
        try:
            async with aclosing(MockLLMProvider(script).stream(data)) as events:
                async for event in events:
                    yield event
        finally:
            self.closes += 1


def router(a: TrackingProvider, b: TrackingProvider, **kwargs: object) -> LLMRouter:
    policy = RouterPolicy.model_validate(
        {"max_attempts_per_provider": 1, "retry_delay_seconds": 0, **kwargs}
    )
    return LLMRouter((ProviderSlot(a), ProviderSlot(b)), policy=policy)


@pytest.mark.asyncio
async def test_buffered_partial_failure_discards_text_and_tool_effects() -> None:
    call = ToolCall(id="1", name="lookup", arguments={"x": 0})
    a = TrackingProvider(
        "a", MockScript(text="bad", tool_calls=(call,), chunk_size=3, fail_after_events=2)
    )
    b = TrackingProvider("b", MockScript(text="good", tool_calls=(call,)))
    routed = router(a, b)
    data = request(tools=True)
    result = await routed.generate(data)
    assert result.provider == "b" and result.text == "good" and result.tool_calls == (call,)
    assert len(a.requests) == len(b.requests) == a.closes == b.closes == 1
    for raw in (a.requests[0], b.requests[0]):
        sent = GenerationRequest.model_validate_json(raw)
        assert (
            sent.context == data.context
            and sent.turn_id == data.turn_id
            and sent.conversation_id == data.conversation_id
        )
    assert routed.health()[0].failure_count == 1 and routed.health()[0].failover_count == 1


@pytest.mark.asyncio
async def test_streaming_partial_failure_never_replays_exposed_output() -> None:
    a = TrackingProvider("a", MockScript(text="abcdef", chunk_size=3, fail_after_events=1))
    b = TrackingProvider("b")
    routed = router(a, b, max_attempts_per_provider=2)
    async with aclosing(routed.stream(request())) as events:
        assert await anext(events) == TextDelta(text="abc")
        with pytest.raises(LLMError, match="unavailable"):
            await anext(events)
    assert len(a.requests) == 1 and not b.requests and a.closes == 1


@pytest.mark.asyncio
async def test_pre_output_retry_succeeds_and_keeps_correlation() -> None:
    a = TrackingProvider("a", MockScript(fail_after_events=0), MockScript(text="recovered"))
    b = TrackingProvider("b")
    routed = router(a, b, max_attempts_per_provider=2)
    data = request()
    assert (await routed.generate(data)).text == "recovered"
    assert len(a.requests) == 2 and not b.requests
    assert all(
        GenerationRequest.model_validate_json(raw).request_id == data.request_id
        for raw in a.requests
    )
    assert routed.health()[0].status == "HEALTHY"


@pytest.mark.asyncio
async def test_pre_output_live_failover_is_streamed_once() -> None:
    a = TrackingProvider("a", MockScript(fail_after_events=0))
    b = TrackingProvider("b", MockScript(text="fallback"))
    events = [event async for event in router(a, b).stream(request())]
    assert sum(isinstance(event, CompletionEvent) for event in events) == 1
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "fallback"


@pytest.mark.asyncio
async def test_all_unavailable_is_bounded_no_mock_or_score_fallback() -> None:
    a = TrackingProvider("a", MockScript(fail_after_events=0))
    b = TrackingProvider("b", MockScript(fail_after_events=0))
    routed = router(a, b, max_attempts_per_provider=2)
    with pytest.raises(LLMError, match="unavailable"):
        await routed.generate(request())
    assert len(a.requests) == len(b.requests) == 2
    assert a.closes == b.closes == 2


@pytest.mark.asyncio
async def test_nonretryable_output_failure_uses_backup_once() -> None:
    a = TrackingProvider("a", MockScript(text="", finish_reason="stop"))
    b = TrackingProvider("b")
    routed = router(a, b, max_attempts_per_provider=3)
    assert (await routed.generate(request())).provider == "b"
    assert len(a.requests) == 1


@pytest.mark.asyncio
async def test_capabilities_disabled_and_open_circuits_skip_without_attempt() -> None:
    a = TrackingProvider("a")
    b = TrackingProvider("b")
    routed = LLMRouter((ProviderSlot(a, ProviderCapabilities(tools=False)), ProviderSlot(b)))
    assert (await routed.generate(request(tools=True))).provider == "b"
    assert not a.requests
    disabled = LLMRouter((ProviderSlot(a, enabled=False), ProviderSlot(b, enabled=False)))
    with pytest.raises(LLMError, match="unavailable"):
        await disabled.generate(request())
    assert disabled.health()[0].status == "DISABLED"


@pytest.mark.asyncio
async def test_circuit_opens_skips_then_successful_probe_recovers() -> None:
    now = [0.0]
    a = TrackingProvider("a", MockScript(fail_after_events=0), MockScript(text="recovered"))
    b = TrackingProvider("b")
    routed = LLMRouter(
        (ProviderSlot(a), ProviderSlot(b)),
        policy=RouterPolicy(failure_threshold=1, cooldown_seconds=5),
        clock=lambda: now[0],
    )
    assert (await routed.generate(request())).provider == "b"
    assert (
        routed.health()[0].circuit_state == "OPEN" and routed.health()[0].retry_after_seconds == 5
    )
    assert (await routed.generate(request())).provider == "b"
    assert len(a.requests) == 1
    now[0] = 6
    assert (await routed.generate(request())).provider == "a"
    assert routed.health()[0].circuit_state == "CLOSED" and routed.health()[0].status == "HEALTHY"


@pytest.mark.asyncio
async def test_half_open_probe_is_exclusive_and_cancel_releases_it() -> None:
    now = [0.0]
    a = TrackingProvider(
        "a",
        MockScript(fail_after_events=0),
        MockScript(delay_seconds=10),
        MockScript(text="recovered"),
    )
    b = TrackingProvider("b")
    routed = LLMRouter(
        (ProviderSlot(a), ProviderSlot(b)),
        policy=RouterPolicy(failure_threshold=1, cooldown_seconds=1),
        clock=lambda: now[0],
    )
    await routed.generate(request())
    now[0] = 2
    probe = asyncio.create_task(routed.generate(request()))
    while len(a.requests) < 2:
        await asyncio.sleep(0)
    assert routed.health()[0].circuit_state == "HALF_OPEN"
    assert (await routed.generate(request())).provider == "b"
    assert len(a.requests) == 2
    probe.cancel()
    with pytest.raises(asyncio.CancelledError):
        await probe
    assert routed.health()[0].failure_count == 1 and a.closes == 2
    assert (await routed.generate(request())).provider == "a"


@pytest.mark.asyncio
async def test_old_inflight_success_does_not_close_newly_opened_circuit() -> None:
    a = TrackingProvider(
        "a", MockScript(text="late", delay_seconds=0.03), MockScript(fail_after_events=0)
    )
    b = TrackingProvider("b")
    routed = router(a, b, failure_threshold=1)
    first = asyncio.create_task(routed.generate(request()))
    while not a.requests:
        await asyncio.sleep(0)
    assert (await routed.generate(request())).provider == "b"
    assert (await first).provider == "a"
    assert routed.health()[0].circuit_state == "OPEN"


@pytest.mark.asyncio
async def test_attempt_timeout_fails_over_and_total_timeout_is_not_reset() -> None:
    a = TrackingProvider("a", MockScript(delay_seconds=10))
    b = TrackingProvider("b", MockScript(text="fast"))
    routed = router(a, b, attempt_timeout_seconds=0.02)
    assert (await routed.generate(request())).provider == "b"
    assert a.closes == 1
    b.scripts = (MockScript(delay_seconds=10),)
    with pytest.raises(LLMError, match="timeout"):
        await routed.generate(request(timeout=0.03))
    assert a.closes == 2 and b.closes == 2


@pytest.mark.asyncio
async def test_stream_early_close_does_not_fail_provider_or_call_backup() -> None:
    a = TrackingProvider("a")
    b = TrackingProvider("b")
    routed = router(a, b)
    events = routed.stream(request())
    await anext(events)
    await events.aclose()
    assert a.closes == 1 and not b.requests
    assert routed.health()[0].failure_count == 0


@pytest.mark.asyncio
async def test_concurrent_results_have_request_local_provider_attribution() -> None:
    a = TrackingProvider("a", MockScript(fail_after_events=0), MockScript(text="primary"))
    b = TrackingProvider("b", MockScript(text="backup"))
    routed = router(a, b)
    requests = [request() for _ in range(6)]
    results = await asyncio.gather(*(routed.generate(data) for data in requests))
    assert results[0].provider == "b"
    assert all(result.provider == "a" for result in results[1:])
    assert [result.request_id for result in results] == [data.request_id for data in requests]


@pytest.mark.asyncio
async def test_retry_delay_does_not_waste_fallback_budget() -> None:
    a = TrackingProvider("a", MockScript(fail_after_events=0))
    b = TrackingProvider("b", MockScript(text="fast"))
    routed = router(a, b, max_attempts_per_provider=2, retry_delay_seconds=1)
    assert (await routed.generate(request(timeout=0.1))).provider == "b"
    assert len(a.requests) == 1


@pytest.mark.asyncio
async def test_failed_recovery_probe_reopens_circuit() -> None:
    now = [0.0]
    a = TrackingProvider("a", MockScript(fail_after_events=0))
    b = TrackingProvider("b")
    routed = LLMRouter(
        (ProviderSlot(a), ProviderSlot(b)),
        policy=RouterPolicy(failure_threshold=1, cooldown_seconds=1),
        clock=lambda: now[0],
    )
    await routed.generate(request())
    now[0] = 2
    await routed.generate(request())
    assert len(a.requests) == 2 and routed.health()[0].circuit_state == "OPEN"
    assert routed.health()[0].retry_after_seconds == 1


@pytest.mark.asyncio
async def test_capability_checks_include_context_tools_temperature_and_tokens() -> None:
    a = TrackingProvider("a")
    b = TrackingProvider("b")
    routed = LLMRouter(
        (
            ProviderSlot(
                a, ProviderCapabilities(tools=False, max_temperature=1, max_output_tokens=10)
            ),
            ProviderSlot(b),
        )
    )
    data = request()
    for changes in ({"temperature": 2.0}, {"max_output_tokens": 11}):
        assert (await routed.generate(data.model_copy(update=changes))).provider == "b"
    call = ToolCall(id="1", name="lookup", arguments={})
    context = PromptContext(
        system_instruction="Trusted",
        messages=(
            ContextMessage(role="assistant", tool_calls=(call,)),
            ContextMessage(role="tool", tool_call_id="1", content="backend result"),
        ),
    )
    assert (
        await routed.generate(data.model_copy(update={"context": context, "max_output_tokens": 1}))
    ).provider == "b"
    assert not a.requests
