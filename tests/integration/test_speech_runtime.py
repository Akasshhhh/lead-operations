import asyncio
from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import TypeVar, cast
from uuid import uuid4

import pytest
from voice_platform_speech import (
    AudioChunk,
    AudioEvent,
    AudioFormat,
    MockSTTProvider,
    MockSTTScript,
    MockTTSProvider,
    MockTTSScript,
    SpeechError,
    SpeechStream,
    SpeechToTextRuntime,
    SynthesisCompleted,
    SynthesisEvent,
    SynthesisRequest,
    TextToSpeechRuntime,
    TranscriptEvent,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
    fixture_audio,
)

T = TypeVar("T")


class TrackedStream[T]:
    def __init__(
        self, events: list[T], *, delay: float = 0, error: Exception | None = None
    ) -> None:
        self.events = events
        self.delay = delay
        self.error = error
        self.index = 0
        self.closed = False

    def __aiter__(self) -> "TrackedStream[T]":
        return self

    async def __anext__(self) -> T:
        await asyncio.sleep(self.delay)
        if self.index == len(self.events):
            if self.error:
                raise self.error
            raise StopAsyncIteration
        event = self.events[self.index]
        self.index += 1
        return event

    async def aclose(self) -> None:
        self.closed = True


def stt_request(timeout: float = 1) -> TranscriptionRequest:
    return TranscriptionRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        call_id=uuid4(),
        utterance_id=uuid4(),
        timeout_seconds=timeout,
    )


def tts_request(timeout: float = 1) -> SynthesisRequest:
    return SynthesisRequest(**stt_request(timeout).model_dump(exclude={"language"}), text="Hello")


def transcript(**changes: object) -> TranscriptEvent:
    return TranscriptEvent.model_validate(
        {
            "segment_id": "segment",
            "segment_index": 0,
            "text": "Hello",
            "is_final": True,
            "start_sample": 0,
            "end_sample": 1,
        }
        | changes
    )


class ScriptedSTT:
    name = "test-stt"
    model = "fixture"

    def __init__(self, events: list[TranscriptionEvent], *, consume: bool = True) -> None:
        self.output = TrackedStream(events)
        self.consume = consume

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        try:
            if self.consume:
                async for _ in audio:
                    pass
            async for event in self.output:
                yield event
        finally:
            await self.output.aclose()


class ScriptedTTS:
    name = "test-tts"
    model = "fixture"

    def __init__(self, events: list[SynthesisEvent]) -> None:
        self.output = TrackedStream(events)

    def synthesize(self, request: SynthesisRequest) -> TrackedStream[SynthesisEvent]:
        return self.output


@pytest.mark.asyncio
async def test_mock_stt_revisions_stable_identity_restart_and_concurrency() -> None:
    request = stt_request()
    provider = MockSTTProvider()

    async def run() -> list[TranscriptionEvent]:
        return [
            event
            async for event in SpeechToTextRuntime(provider).transcribe(request, fixture_audio())
        ]

    runs = await asyncio.gather(run(), run(), run())
    assert runs[0] == runs[1] == runs[2]
    assert isinstance(runs[0][0], TranscriptEvent) and not runs[0][0].is_final
    assert isinstance(runs[0][1], TranscriptEvent) and runs[0][1].is_final
    assert runs[0][0].segment_id == runs[0][1].segment_id
    assert runs[0][-1] == TranscriptionCompleted(final_segments=1, input_samples=960)
    restarted = [
        event
        async for event in SpeechToTextRuntime(MockSTTProvider()).transcribe(
            request, fixture_audio()
        )
    ]
    assert restarted == runs[0]


@pytest.mark.asyncio
async def test_silence_is_valid_completion_without_fabricated_text() -> None:
    events = [
        event
        async for event in SpeechToTextRuntime(MockSTTProvider(MockSTTScript(text=""))).transcribe(
            stt_request(), fixture_audio()
        )
    ]
    assert events == [TranscriptionCompleted(final_segments=0, input_samples=960)]


@pytest.mark.asyncio
async def test_tts_mock_ordered_pcm_replay_and_concurrent_requests() -> None:
    request = tts_request().model_copy(update={"audio_format": AudioFormat(sample_rate=24000)})
    runtime = TextToSpeechRuntime(MockTTSProvider())

    async def run() -> list[SynthesisEvent]:
        return [event async for event in runtime.synthesize(request)]

    first, second = await asyncio.gather(run(), run())
    assert first == second
    chunks = [event.chunk for event in first if isinstance(event, AudioEvent)]
    assert [chunk.sequence for chunk in chunks] == [0, 1, 2]
    assert first[-1] == SynthesisCompleted(
        output_samples=sum(len(chunk.data) // 2 for chunk in chunks)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [],
        [transcript()],
        [transcript(is_final=False), TranscriptionCompleted(final_segments=0, input_samples=1)],
        [transcript(segment_index=1)],
        [transcript(end_sample=2)],
        [transcript(), transcript()],
        [transcript(is_final=False), transcript(segment_id="different")],
        [transcript(), TranscriptionCompleted(final_segments=0, input_samples=1)],
        [transcript(), TranscriptionCompleted(final_segments=1, input_samples=2)],
        [transcript(), TranscriptionCompleted(final_segments=1, input_samples=1), transcript()],
        [cast(TranscriptionEvent, {"kind": "unknown", "text": "private"})],
    ],
)
async def test_invalid_stt_stream_is_controlled_and_closed(
    events: list[TranscriptionEvent],
) -> None:
    provider = ScriptedSTT(events)
    audio = TrackedStream([AudioChunk(sequence=0, data=b"\0\0")])
    request = stt_request()
    with pytest.raises(SpeechError) as failure:
        _ = [event async for event in SpeechToTextRuntime(provider).transcribe(request, audio)]
    assert failure.value.code == "invalid_output"
    assert failure.value.request_id == request.request_id
    assert audio.closed and provider.output.closed
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
async def test_stt_requires_input_eof_before_completion() -> None:
    provider = ScriptedSTT(
        [TranscriptionCompleted(final_segments=0, input_samples=1)], consume=False
    )
    audio = TrackedStream([AudioChunk(sequence=0, data=b"\0\0")])
    with pytest.raises(SpeechError, match="invalid_output"):
        _ = [
            event async for event in SpeechToTextRuntime(provider).transcribe(stt_request(), audio)
        ]
    assert audio.index == 0 and audio.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        [],
        [AudioChunk(sequence=1, data=b"aa")],
        [AudioChunk(sequence=0, data=b"aa"), AudioChunk(sequence=0, data=b"aa")],
        [cast(AudioChunk, {"sequence": 0, "data": b"a"})],
    ],
)
async def test_invalid_input(chunks: list[AudioChunk]) -> None:
    audio = TrackedStream(chunks)
    with pytest.raises(SpeechError, match="invalid_input"):
        _ = [
            event
            async for event in SpeechToTextRuntime(MockSTTProvider()).transcribe(
                stt_request(), audio
            )
        ]
    assert audio.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "events",
    [
        [],
        [AudioEvent(chunk=AudioChunk(sequence=1, data=b"aa"))],
        [
            AudioEvent(chunk=AudioChunk(sequence=0, data=b"aa")),
            SynthesisCompleted(output_samples=2),
        ],
        [SynthesisCompleted(output_samples=1)],
        [
            AudioEvent(chunk=AudioChunk(sequence=0, data=b"aa")),
            SynthesisCompleted(output_samples=1),
            SynthesisCompleted(output_samples=1),
        ],
        [cast(SynthesisEvent, {"kind": "audio", "chunk": {"sequence": 0, "data": b"a"}})],
    ],
)
async def test_invalid_tts_stream(events: list[SynthesisEvent]) -> None:
    provider = ScriptedTTS(events)
    with pytest.raises(SpeechError, match="invalid_output"):
        _ = [event async for event in TextToSpeechRuntime(provider).synthesize(tts_request())]
    assert provider.output.closed


@pytest.mark.asyncio
async def test_incremental_stt_uses_backpressure_and_replaces_interim() -> None:
    class IncrementalSTT:
        name = "incremental-stt"
        model = "fixture"

        async def transcribe(
            self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
        ) -> AsyncGenerator[TranscriptionEvent, None]:
            await anext(audio)
            yield transcript(text="Hel", is_final=False)
            yield transcript(text="Hello", is_final=False)
            async for _ in audio:
                pass
            yield transcript(end_sample=2)
            yield TranscriptionCompleted(final_segments=1, input_samples=2)

    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa"), AudioChunk(sequence=1, data=b"bb")])
    async with aclosing(
        SpeechToTextRuntime(IncrementalSTT()).transcribe(stt_request(), audio)
    ) as source:
        assert isinstance(await anext(source), TranscriptEvent)
        assert audio.index == 1  # No eager drain/buffering of the utterance by runtime.
        assert isinstance(await anext(source), TranscriptEvent)
        assert audio.index == 1
        assert isinstance(await anext(source), TranscriptEvent)
        assert isinstance(await anext(source), TranscriptionCompleted)
        assert audio.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_audio_duration_bound_without_buffering(kind: str) -> None:
    data = b"\0\0" * 32000
    if kind == "stt":
        audio = TrackedStream([AudioChunk(sequence=index, data=data) for index in range(31)])
        request = stt_request().model_copy(update={"audio_format": AudioFormat(sample_rate=8000)})
        with pytest.raises(SpeechError, match="invalid_input"):
            _ = [
                event
                async for event in SpeechToTextRuntime(MockSTTProvider()).transcribe(request, audio)
            ]
        assert audio.closed
    else:
        provider = ScriptedTTS(
            [AudioEvent(chunk=AudioChunk(sequence=index, data=data)) for index in range(31)]
        )
        synthesis = tts_request().model_copy(update={"audio_format": AudioFormat(sample_rate=8000)})
        with pytest.raises(SpeechError, match="invalid_output"):
            _ = [event async for event in TextToSpeechRuntime(provider).synthesize(synthesis)]
        assert provider.output.closed


@pytest.mark.asyncio
async def test_transcript_total_text_limit_across_final_segments() -> None:
    provider = ScriptedSTT(
        [
            transcript(text="a" * 20000),
            transcript(segment_id="next", segment_index=1, start_sample=1, end_sample=2, text="b"),
        ]
    )
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aaaa")])
    with pytest.raises(SpeechError, match="invalid_output"):
        _ = [
            event async for event in SpeechToTextRuntime(provider).transcribe(stt_request(), audio)
        ]
    assert provider.output.closed and audio.closed


@pytest.mark.asyncio
async def test_interim_event_limit() -> None:
    provider = ScriptedSTT([transcript(is_final=False)] * 1001)
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")])
    with pytest.raises(SpeechError, match="invalid_output"):
        _ = [
            event async for event in SpeechToTextRuntime(provider).transcribe(stt_request(), audio)
        ]
    assert provider.output.closed and audio.closed


@pytest.mark.asyncio
async def test_completion_eof_timeout_is_not_exposed_as_success() -> None:
    provider = ScriptedTTS(
        [AudioEvent(chunk=AudioChunk(sequence=0, data=b"aa")), SynthesisCompleted(output_samples=1)]
    )
    source = TextToSpeechRuntime(provider).synthesize(tts_request(0.01))
    async with aclosing(source):
        await anext(source)
        provider.output.delay = 0.008  # Completion arrives, EOF exceeds remaining budget.
        with pytest.raises(SpeechError, match="timeout"):
            await anext(source)
    assert provider.output.closed


@pytest.mark.asyncio
async def test_cancellation_while_reading_audio_closes_both_streams() -> None:
    provider = ScriptedSTT([])
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")], delay=1)
    source = SpeechToTextRuntime(provider).transcribe(stt_request(), audio)
    task = asyncio.create_task(anext(source))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert audio.closed and provider.output.closed


@pytest.mark.asyncio
async def test_synchronous_provider_creation_failure_still_closes_input() -> None:
    class BrokenSTT:
        name = "broken"
        model = "fixture"

        def transcribe(
            self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
        ) -> SpeechStream[TranscriptionEvent]:
            raise RuntimeError("private credential")

    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")])
    with pytest.raises(SpeechError, match="provider_error"):
        _ = [
            event
            async for event in SpeechToTextRuntime(BrokenSTT()).transcribe(stt_request(), audio)
        ]
    assert audio.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_partial_failure_is_exposed_without_retry(kind: str) -> None:
    source: AsyncGenerator[TranscriptionEvent | SynthesisEvent, None]
    if kind == "stt":
        source = SpeechToTextRuntime(
            MockSTTProvider(MockSTTScript(fail_after_events=1))
        ).transcribe(stt_request(), fixture_audio())
    else:
        source = TextToSpeechRuntime(
            MockTTSProvider(MockTTSScript(fail_after_events=1))
        ).synthesize(tts_request())
    async with aclosing(source):
        await anext(source)
        with pytest.raises(SpeechError, match="unavailable"):
            await anext(source)


@pytest.mark.asyncio
async def test_input_timeout_closes_audio() -> None:
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")], delay=1)
    with pytest.raises(SpeechError, match="timeout"):
        _ = [
            event
            async for event in SpeechToTextRuntime(MockSTTProvider()).transcribe(
                stt_request(0.01), audio
            )
        ]
    assert audio.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["stt", "tts"])
async def test_provider_timeout_and_total_deadline_after_consumer_pause(kind: str) -> None:
    source: AsyncGenerator[TranscriptionEvent | SynthesisEvent, None]
    if kind == "stt":
        source = SpeechToTextRuntime(MockSTTProvider()).transcribe(
            stt_request(0.01), fixture_audio()
        )
    else:
        source = TextToSpeechRuntime(MockTTSProvider()).synthesize(tts_request(0.01))
    async with aclosing(source):
        await anext(source)
        await asyncio.sleep(0.02)
        with pytest.raises(SpeechError, match="timeout"):
            await anext(source)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_tts_close_and_cancellation_cleanup(cancel: bool) -> None:
    provider = ScriptedTTS(
        [AudioEvent(chunk=AudioChunk(sequence=0, data=b"aa")), SynthesisCompleted(output_samples=1)]
    )
    source = TextToSpeechRuntime(provider).synthesize(tts_request())
    await anext(source)
    if cancel:
        provider.output.delay = 1
        task = asyncio.create_task(anext(source))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await source.aclose()
    assert provider.output.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_stt_close_and_cancellation_cleanup(cancel: bool) -> None:
    provider = ScriptedSTT(
        [
            transcript(is_final=False),
            transcript(),
            TranscriptionCompleted(final_segments=1, input_samples=1),
        ]
    )
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")])
    source = SpeechToTextRuntime(provider).transcribe(stt_request(), audio)
    await anext(source)
    if cancel:
        provider.output.delay = 1
        task = asyncio.create_task(anext(source))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await source.aclose()
    assert provider.output.closed and audio.closed


@pytest.mark.asyncio
async def test_success_closes_input_and_output_before_exposing_terminal_event() -> None:
    provider = ScriptedSTT(
        [transcript(), TranscriptionCompleted(final_segments=1, input_samples=1)]
    )
    audio = TrackedStream([AudioChunk(sequence=0, data=b"aa")])
    async with aclosing(SpeechToTextRuntime(provider).transcribe(stt_request(), audio)) as source:
        await anext(source)
        assert isinstance(await anext(source), TranscriptionCompleted)
        assert provider.output.closed and audio.closed


@pytest.mark.asyncio
async def test_unexpected_vendor_exception_redaction() -> None:
    provider = ScriptedTTS([])
    provider.output.error = RuntimeError("api-secret and private text")
    request = tts_request()
    with pytest.raises(SpeechError) as failure:
        _ = [event async for event in TextToSpeechRuntime(provider).synthesize(request)]
    assert failure.value.code == "provider_error"
    assert failure.value.request_id == request.request_id
    assert "api-secret" not in str(failure.value)
    assert provider.output.closed
