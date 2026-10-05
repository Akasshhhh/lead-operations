"""Deterministic text fixtures and PCM tone; these do not recognize or speak."""

import asyncio
import math
import struct
from collections.abc import AsyncGenerator

from pydantic import Field

from .contracts import (
    AudioChunk,
    AudioEvent,
    AudioFormat,
    Contract,
    SynthesisCompleted,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptEvent,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
)
from .provider import ErrorCode, SpeechError, SpeechStream


class MockSTTScript(Contract):
    text: str = Field(default="Hello", max_length=20000)
    interim: str | None = Field(default="Hel", min_length=1, max_length=20000)
    delay_seconds: float = Field(default=0, ge=0, le=300)
    fail_after_events: int | None = Field(default=None, ge=0, strict=True)
    error_code: ErrorCode = "unavailable"


class MockTTSScript(Contract):
    chunks: int = Field(default=3, ge=1, le=100, strict=True)
    delay_seconds: float = Field(default=0, ge=0, le=300)
    fail_after_events: int | None = Field(default=None, ge=0, strict=True)
    error_code: ErrorCode = "unavailable"


def fixture_pcm(audio_format: AudioFormat | None = None) -> bytes:
    """20 ms of a 400 Hz, low-amplitude tone at the explicitly requested rate."""
    audio_format = audio_format or AudioFormat()
    return b"".join(
        struct.pack(
            "<h", round(1000 * math.sin(2 * math.pi * 400 * index / audio_format.sample_rate))
        )
        for index in range(audio_format.sample_rate // 50)
    )


async def fixture_audio(
    audio_format: AudioFormat | None = None, chunks: int = 3
) -> AsyncGenerator[AudioChunk, None]:
    if not 1 <= chunks <= 100:
        raise ValueError("fixture chunk count must be between 1 and 100")
    data = fixture_pcm(audio_format)
    for index in range(chunks):
        await asyncio.sleep(0)
        yield AudioChunk(sequence=index, data=data)


class MockSTTProvider:
    name = "mock-stt"
    model = "deterministic-v1"

    def __init__(self, script: MockSTTScript | None = None) -> None:
        self.script = script or MockSTTScript()

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        samples = 0
        async for chunk in audio:
            samples += len(chunk.data) // 2
        events: list[TranscriptionEvent] = []
        if self.script.text:
            segment = f"mock_{request.utterance_id.hex}_0"
            if self.script.interim:
                events.append(
                    TranscriptEvent(
                        segment_id=segment,
                        segment_index=0,
                        text=self.script.interim,
                        is_final=False,
                        start_sample=0,
                        end_sample=samples,
                    )
                )
            events.append(
                TranscriptEvent(
                    segment_id=segment,
                    segment_index=0,
                    text=self.script.text,
                    is_final=True,
                    start_sample=0,
                    end_sample=samples,
                )
            )
        events.append(
            TranscriptionCompleted(
                final_segments=int(bool(self.script.text)), input_samples=samples
            )
        )
        for index, event in enumerate(events):
            await asyncio.sleep(self.script.delay_seconds)
            if self.script.fail_after_events is not None and index >= self.script.fail_after_events:
                raise SpeechError(
                    self.script.error_code, request_id=request.request_id, provider=self.name
                )
            yield event


class MockTTSProvider:
    name = "mock-tts"
    model = "deterministic-v1"

    def __init__(self, script: MockTTSScript | None = None) -> None:
        self.script = script or MockTTSScript()

    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        data = fixture_pcm(request.audio_format)
        for index in range(self.script.chunks + 1):
            await asyncio.sleep(self.script.delay_seconds)
            if self.script.fail_after_events is not None and index >= self.script.fail_after_events:
                raise SpeechError(
                    self.script.error_code, request_id=request.request_id, provider=self.name
                )
            if index == self.script.chunks:
                yield SynthesisCompleted(output_samples=self.script.chunks * len(data) // 2)
            else:
                yield AudioEvent(chunk=AudioChunk(sequence=index, data=data))
