"""Closable pull streams permit backpressure and explicit interruption cleanup."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol, TypeVar
from uuid import UUID

from .contracts import (
    AudioChunk,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptionEvent,
    TranscriptionRequest,
)

T_co = TypeVar("T_co", covariant=True)
ErrorCode = Literal[
    "timeout", "unavailable", "rate_limited", "invalid_input", "invalid_output", "provider_error"
]


class SpeechError(RuntimeError):
    def __init__(self, code: ErrorCode, *, request_id: UUID, provider: str) -> None:
        super().__init__(f"Speech operation failed: {code}")
        self.code = code
        self.request_id = request_id
        self.provider = provider
        # This is guidance only. Replaying exposed audio/transcripts is not safe.
        self.retryable = code in {"timeout", "unavailable", "rate_limited"}


class SpeechStream(Protocol[T_co]):
    def __aiter__(self) -> AsyncIterator[T_co]: ...
    async def __anext__(self) -> T_co: ...
    async def aclose(self) -> None: ...


class STTProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> SpeechStream[TranscriptionEvent]: ...


class TTSProvider(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def model(self) -> str: ...
    def synthesize(self, request: SynthesisRequest) -> SpeechStream[SynthesisEvent]: ...
