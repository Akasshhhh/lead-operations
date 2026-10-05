"""Stateless validation boundary; no retries, playback, persistence, or VAD."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import aclosing, asynccontextmanager

from pydantic import TypeAdapter, ValidationError

from .contracts import (
    AudioChunk,
    AudioEvent,
    SpeechRequest,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptEvent,
    TranscriptionEvent,
    TranscriptionRequest,
)
from .provider import SpeechError, SpeechStream, STTProvider, TTSProvider

_STT = TypeAdapter[TranscriptionEvent](TranscriptionEvent)
_TTS = TypeAdapter[SynthesisEvent](SynthesisEvent)
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 120


@asynccontextmanager
async def _errors(request: SpeechRequest, provider: str) -> AsyncIterator[None]:
    try:
        yield
    except TimeoutError:
        raise SpeechError("timeout", request_id=request.request_id, provider=provider) from None
    except SpeechError as exc:
        raise SpeechError(exc.code, request_id=request.request_id, provider=provider) from None
    except (ValidationError, ValueError):
        raise SpeechError(
            "invalid_output", request_id=request.request_id, provider=provider
        ) from None
    except Exception:
        raise SpeechError(
            "provider_error", request_id=request.request_id, provider=provider
        ) from None
    # Cancellation and GeneratorExit propagate, after closing owned streams.


class _Input:
    def __init__(self, request: TranscriptionRequest, provider: str, deadline: float) -> None:
        self.request = request
        self.provider = provider
        self.deadline = deadline
        self.samples = 0
        self.chunks = 0
        self.exhausted = False

    async def stream(self, audio: SpeechStream[AudioChunk]) -> AsyncGenerator[AudioChunk, None]:
        try:
            while True:
                async with asyncio.timeout_at(self.deadline):
                    try:
                        raw = await anext(audio)
                    except StopAsyncIteration:
                        break
                    chunk = AudioChunk.model_validate(raw)
                    if chunk.sequence != self.chunks:
                        raise ValueError("input sequence is not contiguous")
                    self.chunks += 1
                    if self.chunks > 10000:
                        raise ValueError("too many input chunks")
                    self.samples += len(chunk.data) // 2
                    _audio_bound(self.request, self.samples)
                yield chunk
            if not self.samples:
                raise ValueError("empty input stream")
            self.exhausted = True
        except (ValidationError, ValueError):
            raise SpeechError(
                "invalid_input", request_id=self.request.request_id, provider=self.provider
            ) from None


def _audio_bound(request: SpeechRequest, samples: int) -> None:
    if (
        samples * 2 > MAX_AUDIO_BYTES
        or samples > MAX_AUDIO_SECONDS * request.audio_format.sample_rate
    ):
        raise ValueError("utterance audio exceeds limit")


class SpeechToTextRuntime:
    def __init__(self, provider: STTProvider) -> None:
        self.provider = provider

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        request = TranscriptionRequest.model_validate(request)
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        state = _Input(request, self.provider.name, deadline)
        finals = 0
        text_length = 0
        last_end = 0
        pending: TranscriptEvent | None = None
        ids: set[str] = set()
        events = 0
        async with _errors(request, self.provider.name), aclosing(audio):
            async with aclosing(state.stream(audio)) as validated:
                async with aclosing(self.provider.transcribe(request, validated)) as source:
                    while True:
                        async with asyncio.timeout_at(deadline):
                            try:
                                raw = await anext(source)
                            except StopAsyncIteration:
                                raise ValueError("missing transcription completion") from None
                        event = _STT.validate_python(raw)
                        events += 1
                        if events > 1000:
                            raise ValueError("too many transcription events")
                        if isinstance(event, TranscriptEvent):
                            if finals >= 200 or event.segment_index != finals:
                                raise ValueError("segment index is not contiguous")
                            if pending is None:
                                if event.segment_id in ids or event.start_sample < last_end:
                                    raise ValueError("duplicate or overlapping segment")
                                ids.add(event.segment_id)
                            elif (
                                event.segment_id != pending.segment_id
                                or event.start_sample != pending.start_sample
                            ):
                                raise ValueError("interim revision changed segment identity")
                            if text_length + len(event.text) > 20000:
                                raise ValueError("transcription text exceeds limit")
                            if event.end_sample > state.samples:
                                raise ValueError("segment exceeds consumed input")
                            pending = event
                            if event.is_final:
                                finals += 1
                                text_length += len(event.text)
                                last_end = event.end_sample
                                pending = None
                        else:
                            if (
                                pending is not None
                                or not state.exhausted
                                or event.final_segments != finals
                                or event.input_samples != state.samples
                            ):
                                raise ValueError("incomplete or inconsistent transcription")
                            async with asyncio.timeout_at(deadline):
                                try:
                                    await anext(source)
                                except StopAsyncIteration:
                                    pass
                                else:
                                    raise ValueError("event after transcription completion")
                            await source.aclose()
                            await validated.aclose()
                            await audio.aclose()
                            yield event
                            return
                        yield event


class TextToSpeechRuntime:
    def __init__(self, provider: TTSProvider) -> None:
        self.provider = provider

    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        request = SynthesisRequest.model_validate(request)
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        samples = 0
        chunks = 0
        async with _errors(request, self.provider.name):
            async with aclosing(self.provider.synthesize(request)) as source:
                while True:
                    async with asyncio.timeout_at(deadline):
                        try:
                            raw = await anext(source)
                        except StopAsyncIteration:
                            raise ValueError("missing synthesis completion") from None
                    event = _TTS.validate_python(raw)
                    if isinstance(event, AudioEvent):
                        if event.chunk.sequence != chunks:
                            raise ValueError("output sequence is not contiguous")
                        chunks += 1
                        samples += len(event.chunk.data) // 2
                        _audio_bound(request, samples)
                        if chunks > 10000:
                            raise ValueError("too many synthesis chunks")
                    else:
                        if event.output_samples != samples:
                            raise ValueError("inconsistent synthesis completion")
                        async with asyncio.timeout_at(deadline):
                            try:
                                await anext(source)
                            except StopAsyncIteration:
                                pass
                            else:
                                raise ValueError("event after synthesis completion")
                        await source.aclose()
                        yield event
                        return
                    yield event
