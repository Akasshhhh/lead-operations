import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from uuid import uuid4

import pytest
from voice_platform_speech import (
    AudioChunk,
    AudioEvent,
    MockSTTProvider,
    MockSTTScript,
    MockTTSProvider,
    MockTTSScript,
    SpeechCapabilities,
    SpeechError,
    SpeechRouterPolicy,
    SpeechSlot,
    STTProvider,
    STTRouter,
    SynthesisCompleted,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
    TTSProvider,
    TTSRouter,
    fixture_audio,
)
from voice_platform_speech.provider import SpeechStream


def stt_request(timeout: float = 1) -> TranscriptionRequest:
    return TranscriptionRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        call_id=uuid4(),
        utterance_id=uuid4(),
        timeout_seconds=timeout,
    )


def tts_request(timeout: float = 1) -> SynthesisRequest:
    return SynthesisRequest(**stt_request(timeout).model_dump(), text="Hello")


class TTS(MockTTSProvider):
    def __init__(self, name: str, script: MockTTSScript | None = None) -> None:
        super().__init__(script)
        self.name = name
        self.requests: list[SynthesisRequest] = []
        self.closed = 0

    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        self.requests.append(request)
        try:
            async with aclosing(super().synthesize(request)) as source:
                async for event in source:
                    yield event
        finally:
            self.closed += 1


class STT(MockSTTProvider):
    def __init__(self, name: str, script: MockSTTScript | None = None) -> None:
        super().__init__(script)
        self.name = name
        self.requests: list[TranscriptionRequest] = []
        self.inputs: list[list[AudioChunk]] = []

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        self.requests.append(request)
        chunks = [chunk async for chunk in audio]
        self.inputs.append(chunks)

        async def replay() -> AsyncGenerator[AudioChunk, None]:
            for chunk in chunks:
                yield chunk

        async with aclosing(super().transcribe(request, replay())) as source:
            async for event in source:
                yield event


def tts_router(a: TTS, b: TTS, **policy: object) -> TTSRouter:
    return TTSRouter(
        (SpeechSlot[TTSProvider](a), SpeechSlot[TTSProvider](b)),
        policy=SpeechRouterPolicy.model_validate({"retry_delay_seconds": 0} | policy),
    )


@pytest.mark.asyncio
async def test_retry_and_fallback_keep_request_identity_and_text() -> None:
    a = TTS("a", MockTTSScript(fail_after_events=0))
    b = TTS("b")
    router = tts_router(a, b)
    request = tts_request()
    result = [event async for event in router.synthesize(request)]
    assert isinstance(result[-1], SynthesisCompleted)
    assert len(a.requests) == a.closed == 2 and len(b.requests) == b.closed == 1
    assert all(
        item.request_id == request.request_id and item.text == request.text
        for item in a.requests + b.requests
    )
    health = router.health()
    assert health[0].failure_count == 2 and health[0].failover_count == 1
    assert health[1].success_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_partial_output_never_replays(kind: str) -> None:
    if kind == "tts":
        a, b = TTS("a", MockTTSScript(fail_after_events=1)), TTS("b")
        source: AsyncGenerator[TranscriptionEvent | SynthesisEvent, None] = tts_router(
            a, b
        ).synthesize(tts_request())
    else:
        first, second = STT("a", MockSTTScript(fail_after_events=1)), STT("b")
        source = STTRouter(
            (SpeechSlot[STTProvider](first), SpeechSlot[STTProvider](second))
        ).transcribe(stt_request(), fixture_audio())
    async with aclosing(source):
        await anext(source)
        with pytest.raises(SpeechError, match="unavailable"):
            await anext(source)
    if kind == "tts":
        assert len(a.requests) == 1 and not b.requests
    else:
        assert len(first.requests) == 1 and not second.requests


@pytest.mark.asyncio
async def test_stt_safe_replay_preserves_every_input_chunk_once_per_attempt() -> None:
    a, b = STT("a", MockSTTScript(fail_after_events=0)), STT("b")
    router = STTRouter(
        (SpeechSlot[STTProvider](a), SpeechSlot[STTProvider](b)),
        policy=SpeechRouterPolicy(max_attempts=1),
    )
    result = [event async for event in router.transcribe(stt_request(), fixture_audio())]
    assert result[-1] == TranscriptionCompleted(final_segments=1, input_samples=960)
    assert a.inputs[0] == b.inputs[0]
    assert [chunk.sequence for chunk in b.inputs[0]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_capability_and_disabled_selection_skip_without_health_failure() -> None:
    a, b = TTS("a"), TTS("b")
    router = TTSRouter(
        (
            SpeechSlot[TTSProvider](a, SpeechCapabilities(max_text_characters=1)),
            SpeechSlot[TTSProvider](b),
        )
    )
    _ = [event async for event in router.synthesize(tts_request())]
    assert not a.requests and len(b.requests) == 1 and router.health()[0].failure_count == 0
    disabled = TTSRouter((SpeechSlot[TTSProvider](a, enabled=False),))
    with pytest.raises(SpeechError, match="unavailable"):
        _ = [event async for event in disabled.synthesize(tts_request())]
    assert not a.requests


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["provider_error", "invalid_output", "rate_limited", "timeout"])
async def test_error_retry_policy_and_all_failed_outcome(code: str) -> None:
    script = MockTTSScript.model_validate({"fail_after_events": 0, "error_code": code})
    a, b = TTS("a", script), TTS("b", script)
    with pytest.raises(SpeechError) as error:
        _ = [event async for event in tts_router(a, b).synthesize(tts_request())]
    assert error.value.code == code
    expected = 2 if code in {"rate_limited", "timeout"} else 1
    assert len(a.requests) == len(b.requests) == expected


@pytest.mark.asyncio
async def test_circuit_open_skip_half_open_and_recovery() -> None:
    now = [0.0]
    a, b = TTS("a", MockTTSScript(fail_after_events=0)), TTS("b")
    router = TTSRouter(
        (SpeechSlot[TTSProvider](a), SpeechSlot[TTSProvider](b)),
        policy=SpeechRouterPolicy(max_attempts=1, failure_threshold=1, cooldown_seconds=1),
        clock=lambda: now[0],
    )
    _ = [event async for event in router.synthesize(tts_request())]
    assert router.health()[0].circuit_state == "OPEN"
    _ = [event async for event in router.synthesize(tts_request())]
    assert len(a.requests) == 1
    now[0] = 2
    a.script = MockTTSScript()
    _ = [event async for event in router.synthesize(tts_request())]
    assert router.health()[0].circuit_state == "CLOSED" and len(a.requests) == 2


@pytest.mark.asyncio
async def test_exclusive_probe_and_cancelled_probe_release() -> None:
    now = [0.0]
    a, b = TTS("a", MockTTSScript(fail_after_events=0)), TTS("b")
    router = TTSRouter(
        (SpeechSlot[TTSProvider](a), SpeechSlot[TTSProvider](b)),
        policy=SpeechRouterPolicy(max_attempts=1, failure_threshold=1, cooldown_seconds=1),
        clock=lambda: now[0],
    )
    _ = [event async for event in router.synthesize(tts_request())]
    now[0] = 2
    a.script = MockTTSScript(delay_seconds=0.1)
    source = router.synthesize(tts_request())
    task = asyncio.create_task(anext(source))
    await asyncio.sleep(0.01)
    assert router.health()[0].circuit_state == "HALF_OPEN"
    _ = [event async for event in router.synthesize(tts_request())]
    assert len(a.requests) == 2  # Concurrent request used B, not a second probe.
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert router.health()[0].circuit_state == "OPEN" and router.health()[0].failure_count == 1


@pytest.mark.asyncio
async def test_attempt_timeout_fallback_and_total_budget() -> None:
    a, b = TTS("a", MockTTSScript(delay_seconds=0.1)), TTS("b")
    router = tts_router(a, b, max_attempts=1, attempt_timeout_seconds=0.01)
    _ = [event async for event in router.synthesize(tts_request())]
    assert router.health()[0].last_error == "timeout"
    b.script = MockTTSScript(delay_seconds=0.1)
    with pytest.raises(SpeechError, match="timeout"):
        _ = [event async for event in router.synthesize(tts_request(0.015))]


@pytest.mark.asyncio
async def test_invalid_audio_does_not_change_provider_health_or_fallback() -> None:
    a, b = STT("a"), STT("b")
    router = STTRouter((SpeechSlot[STTProvider](a), SpeechSlot[STTProvider](b)))

    async def invalid() -> AsyncGenerator[AudioChunk, None]:
        yield AudioChunk(sequence=1, data=b"aa")

    with pytest.raises(SpeechError, match="invalid_input"):
        _ = [event async for event in router.transcribe(stt_request(), invalid())]
    assert router.health()[0].failure_count == 0 and not b.requests


@pytest.mark.asyncio
async def test_cancelled_input_read_cannot_retry_truncated_audio() -> None:
    closed = False

    async def slow() -> AsyncGenerator[AudioChunk, None]:
        nonlocal closed
        try:
            yield AudioChunk(sequence=0, data=b"aa")
            await asyncio.sleep(1)
            yield AudioChunk(sequence=1, data=b"bb")
        finally:
            closed = True

    a, b = STT("a"), STT("b")
    router = STTRouter(
        (SpeechSlot[STTProvider](a), SpeechSlot[STTProvider](b)),
        policy=SpeechRouterPolicy(attempt_timeout_seconds=0.01),
    )
    with pytest.raises(SpeechError, match="timeout"):
        _ = [event async for event in router.transcribe(stt_request(), slow())]
    assert closed and len(a.requests) == 1 and not b.requests


@pytest.mark.asyncio
async def test_early_close_cleans_provider_without_health_success_or_failure() -> None:
    a, b = TTS("a"), TTS("b")
    router = tts_router(a, b)
    source = router.synthesize(tts_request())
    assert isinstance(await anext(source), AudioEvent)
    await source.aclose()
    assert (
        a.closed == 1 and router.health()[0].success_count == router.health()[0].failure_count == 0
    )


@pytest.mark.asyncio
async def test_concurrent_request_state_is_isolated() -> None:
    a, b = TTS("a"), TTS("b")
    router = tts_router(a, b)

    async def run() -> list[SynthesisEvent]:
        return [event async for event in router.synthesize(tts_request())]

    results = await asyncio.gather(*(run() for _ in range(5)))
    assert all(isinstance(result[-1], SynthesisCompleted) for result in results)
    assert (
        router.health()[0].success_count == 5
        and len({request.request_id for request in a.requests}) == 5
    )


def test_duplicate_provider_names_and_policy_bounds() -> None:
    with pytest.raises(ValueError):
        tts_router(TTS("a"), TTS("a"))
    with pytest.raises(ValueError):
        SpeechRouterPolicy(max_attempts=4)


@pytest.mark.asyncio
async def test_stt_replays_consumed_prefix_then_pulls_remaining_input() -> None:
    class PrefixFailure(STT):
        async def transcribe(
            self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
        ) -> AsyncGenerator[TranscriptionEvent, None]:
            self.inputs.append([await anext(audio)])
            raise SpeechError("unavailable", request_id=request.request_id, provider=self.name)
            yield  # pragma: no cover

    a, b = PrefixFailure("a"), STT("b")
    router = STTRouter(
        (SpeechSlot[STTProvider](a), SpeechSlot[STTProvider](b)),
        policy=SpeechRouterPolicy(max_attempts=1),
    )
    events = [event async for event in router.transcribe(stt_request(), fixture_audio())]
    assert isinstance(events[-1], TranscriptionCompleted)
    assert a.inputs[0] == b.inputs[0][:1] and len(b.inputs[0]) == 3


@pytest.mark.asyncio
async def test_old_success_cannot_reset_a_newer_open_circuit() -> None:
    class RacingTTS(TTS):
        def __init__(self) -> None:
            super().__init__("a")
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.count = 0

        async def synthesize(
            self, request: SynthesisRequest
        ) -> AsyncGenerator[SynthesisEvent, None]:
            self.count += 1
            if self.count == 1:
                self.started.set()
                await self.release.wait()
                async for event in super().synthesize(request):
                    yield event
            else:
                raise SpeechError("unavailable", request_id=request.request_id, provider=self.name)

    a, b = RacingTTS(), TTS("b")
    router = tts_router(a, b, max_attempts=1, failure_threshold=1)

    async def run() -> None:
        _ = [event async for event in router.synthesize(tts_request())]

    first = asyncio.create_task(run())
    await a.started.wait()
    await run()
    assert router.health()[0].circuit_state == "OPEN"
    a.release.set()
    await first
    assert router.health()[0].circuit_state == "OPEN" and router.health()[0].success_count == 1


@pytest.mark.asyncio
async def test_retry_backoff_leaves_budget_for_fallback() -> None:
    a, b = TTS("a", MockTTSScript(fail_after_events=0)), TTS("b")
    router = tts_router(a, b, retry_delay_seconds=0.5)
    events = [event async for event in router.synthesize(tts_request(0.1))]
    assert isinstance(events[-1], SynthesisCompleted) and len(a.requests) == 1


@pytest.mark.asyncio
async def test_input_close_error_is_safe_even_when_no_provider_is_selected() -> None:
    class BrokenInput:
        def __aiter__(self) -> "BrokenInput":
            return self

        async def __anext__(self) -> AudioChunk:
            raise StopAsyncIteration

        async def aclose(self) -> None:
            raise RuntimeError("private audio and credential")

    request = stt_request()
    router = STTRouter((SpeechSlot[STTProvider](STT("a"), enabled=False),))
    with pytest.raises(SpeechError) as failure:
        _ = [event async for event in router.transcribe(request, BrokenInput())]
    assert failure.value.request_id == request.request_id
    assert "private" not in str(failure.value) and failure.value.code == "provider_error"
