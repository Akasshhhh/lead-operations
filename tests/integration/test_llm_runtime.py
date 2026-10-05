"""Credential-free mock/provider/runtime contract integration and failure paths."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import uuid4

import pytest
from voice_platform_llm import (
    CompletionEvent,
    ContextMessage,
    ConversationLLMRuntime,
    GenerationRequest,
    LLMError,
    MockLLMProvider,
    MockScript,
    PromptContext,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolDefinition,
)


def request(**kwargs: object) -> GenerationRequest:
    return GenerationRequest.model_validate(
        {
            "request_id": uuid4(),
            "conversation_id": uuid4(),
            "turn_id": uuid4(),
            "context": PromptContext(
                system_instruction="Use authoritative backend results",
                messages=(ContextMessage(role="user", content="Hello"),),
            ),
            **kwargs,
        }
    )


class FixtureProvider:
    name = "fixture"
    model = "local-test"

    def __init__(self, events: tuple[StreamEvent, ...], *, wait: bool = False) -> None:
        self.events = events
        self.wait = wait
        self.started = asyncio.Event()
        self.closed = False

    async def stream(self, data: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        try:
            for event in self.events:
                yield event
            self.started.set()
            if self.wait:
                await asyncio.Event().wait()
        finally:
            self.closed = True


@pytest.mark.asyncio
async def test_mock_stream_generate_replay_and_concurrency() -> None:
    runtime = ConversationLLMRuntime(MockLLMProvider(MockScript(text="Hello world", chunk_size=3)))
    data = request()
    events = [event async for event in runtime.stream(data)]
    assert "".join(event.text for event in events if isinstance(event, TextDelta)) == "Hello world"
    assert isinstance(events[-1], CompletionEvent)
    results = await asyncio.gather(*(runtime.generate(data) for _ in range(6)))
    assert all(result == results[0] for result in results)
    assert results[0].request_id == data.request_id
    assert results[0].provider == "mock"
    assert results[0].usage is None
    # Restart with the same fixture reproduces the result without a transcript store.
    restarted = ConversationLLMRuntime(
        MockLLMProvider(MockScript(text="Hello world", chunk_size=3))
    )
    assert await restarted.generate(data) == results[0]


@pytest.mark.asyncio
async def test_complete_tools_are_proposals_and_results_remain_context() -> None:
    tool = ToolDefinition(
        name="get_qualification",
        description="Read backend qualification",
        parameters={"type": "object"},
    )
    call = ToolCall(id="call-1", name=tool.name, arguments={"confirmed": False, "years": 0})
    runtime = ConversationLLMRuntime(MockLLMProvider(MockScript(text="", tool_calls=(call,))))
    data = request(tools=(tool,))
    result = await runtime.generate(data)
    assert result.tool_calls == (call,)
    assert result.finish_reason == "tool_calls"
    next_context = PromptContext(
        system_instruction=data.context.system_instruction,
        messages=(
            *data.context.messages,
            ContextMessage(role="assistant", tool_calls=result.tool_calls),
            ContextMessage(
                role="tool",
                tool_call_id=call.id,
                content='{"score": 0, "rule_version": "baseline-v1"}',
            ),
        ),
    )
    next_request = data.model_copy(update={"context": next_context})
    answer = await ConversationLLMRuntime(MockLLMProvider()).generate(next_request)
    assert answer.text == "How can I help you?"
    assert next_context.messages[-1].content == '{"score": 0, "rule_version": "baseline-v1"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["unavailable", "rate_limited", "provider_error"])
async def test_mock_failure_after_partial_output_has_no_completed_response(code: str) -> None:
    runtime = ConversationLLMRuntime(
        MockLLMProvider(
            MockScript.model_validate(
                {"text": "abcdef", "chunk_size": 3, "fail_after_events": 1, "error_code": code}
            )
        )
    )
    data = request()
    async with aclosing(runtime.stream(data)) as stream:
        assert await anext(stream) == TextDelta(text="abc")
        with pytest.raises(LLMError) as caught:
            await anext(stream)
    assert caught.value.code == code
    assert caught.value.request_id == data.request_id
    with pytest.raises(LLMError):
        await runtime.generate(data)
    assert (await ConversationLLMRuntime(MockLLMProvider()).generate(data)).text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        (),
        (TextDelta(text="partial"),),
        (TextDelta(text="x"), CompletionEvent(finish_reason="tool_calls")),
        (CompletionEvent(finish_reason="stop"),),
        (TextDelta(text="x"), CompletionEvent(finish_reason="stop"), TextDelta(text="extra")),
        (ToolCallEvent(call=ToolCall(id="1", name="unknown", arguments={})),),
    ],
)
async def test_invalid_streams_close_without_terminal_success(
    events: tuple[StreamEvent, ...],
) -> None:
    provider = FixtureProvider(events)
    with pytest.raises(LLMError, match="invalid_output"):
        await ConversationLLMRuntime(provider).generate(request())
    assert provider.closed


@pytest.mark.asyncio
async def test_duplicate_tools_and_oversized_text_fail() -> None:
    tool = ToolDefinition(name="lookup", description="Read", parameters={"type": "object"})
    event = ToolCallEvent(call=ToolCall(id="1", name=tool.name, arguments={}))
    fixtures: list[tuple[StreamEvent, ...]] = [
        (event, event),
        (TextDelta(text="x" * 20_000), TextDelta(text="y")),
    ]
    for events in fixtures:
        with pytest.raises(LLMError, match="invalid_output"):
            await ConversationLLMRuntime(FixtureProvider(events)).generate(request(tools=(tool,)))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        (),
        (TextDelta(text="partial"),),
        (TextDelta(text="x"), CompletionEvent(finish_reason="stop")),
    ],
)
async def test_total_timeout_closes_before_during_and_after_output(
    events: tuple[StreamEvent, ...],
) -> None:
    provider = FixtureProvider(events, wait=True)
    with pytest.raises(LLMError, match="timeout") as caught:
        await ConversationLLMRuntime(provider).generate(request(timeout_seconds=0.02))
    assert caught.value.retryable
    assert provider.closed


@pytest.mark.asyncio
async def test_task_cancellation_closes_provider_and_propagates() -> None:
    provider = FixtureProvider((TextDelta(text="partial"),), wait=True)
    task = asyncio.create_task(ConversationLLMRuntime(provider).generate(request()))
    await provider.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert provider.closed


@pytest.mark.asyncio
async def test_barge_in_early_close_releases_stream() -> None:
    provider = FixtureProvider((TextDelta(text="partial"), TextDelta(text="more")))
    stream = ConversationLLMRuntime(provider).stream(request())
    assert await anext(stream) == TextDelta(text="partial")
    await stream.aclose()
    assert provider.closed


@pytest.mark.asyncio
async def test_vendor_exception_is_redacted() -> None:
    class BrokenProvider(FixtureProvider):
        async def stream(self, data: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
            raise RuntimeError("SECRET provider credential and prompt")
            yield TextDelta(text="unreachable")

    with pytest.raises(LLMError) as caught:
        await ConversationLLMRuntime(BrokenProvider(())).generate(request())
    assert caught.value.code == "provider_error"
    assert "SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_caller_tool_argument_mutation_cannot_change_validated_completion() -> None:
    tool = ToolDefinition(name="lookup", description="Read", parameters={"type": "object"})
    provider = MockLLMProvider(
        MockScript(text="", tool_calls=(ToolCall(id="1", name="lookup", arguments={"x": 0}),))
    )
    async with aclosing(ConversationLLMRuntime(provider).stream(request(tools=(tool,)))) as stream:
        event = await anext(stream)
        assert isinstance(event, ToolCallEvent)
        event.call.arguments["x"] = float("nan")
        assert isinstance(await anext(stream), CompletionEvent)
