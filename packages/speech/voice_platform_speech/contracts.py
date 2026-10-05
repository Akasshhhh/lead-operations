"""Bounded speech contracts; PCM bytes stay in memory, outside JSON persistence."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _safe_text(value: str) -> str:
    if not value.strip() or "\x00" in value:
        raise ValueError("speech text must be nonblank and NUL-free")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ValueError("speech text must be valid UTF-8") from None
    return value


class Contract(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, allow_inf_nan=False, revalidate_instances="always"
    )


class AudioFormat(Contract):
    encoding: Literal["pcm_s16le"] = "pcm_s16le"
    sample_rate: Literal[8000, 16000, 22050, 24000, 48000] = 16000
    channels: Literal[1] = 1

    @field_validator("sample_rate", "channels", mode="before")
    @classmethod
    def integer_format(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("audio rate/channels must be integers")
        return value

    @property
    def bytes_per_sample(self) -> int:
        return 2 * self.channels


class AudioChunk(Contract):
    sequence: int = Field(ge=0, strict=True)
    data: bytes = Field(min_length=2, max_length=65536, strict=True, repr=False)

    @model_validator(mode="after")
    def aligned(self) -> Self:
        if len(self.data) % 2:
            raise ValueError("PCM chunk must contain complete signed 16-bit samples")
        return self


class SpeechRequest(Contract):
    request_id: UUID
    conversation_id: UUID
    call_id: UUID
    utterance_id: UUID
    audio_format: AudioFormat = Field(default_factory=AudioFormat)
    timeout_seconds: float = Field(default=10, gt=0, le=300)


class TranscriptionRequest(SpeechRequest):
    # A language hint, not automatic detection or multilingual routing.
    language: str = Field(default="en", min_length=2, max_length=35, pattern=r"^[a-zA-Z-]+$")


class SynthesisRequest(SpeechRequest):
    language: str = Field(default="en", min_length=2, max_length=35, pattern=r"^[a-zA-Z-]+$")
    text: str = Field(min_length=1, max_length=20000)
    voice: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )

    _validate_text = field_validator("text")(_safe_text)


class TranscriptEvent(Contract):
    kind: Literal["transcript"] = "transcript"
    segment_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_-]+$")
    segment_index: int = Field(ge=0, strict=True)
    text: str = Field(min_length=1, max_length=20000)
    is_final: bool = Field(strict=True)
    # Sample offsets relative to this utterance, not wall clock or DB sequence.
    start_sample: int = Field(ge=0, strict=True)
    end_sample: int = Field(ge=0, strict=True)
    confidence: float | None = Field(default=None, ge=0, le=1)

    _validate_text = field_validator("text")(_safe_text)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_sample < self.start_sample:
            raise ValueError("transcript sample offsets are reversed")
        return self


class TranscriptionCompleted(Contract):
    kind: Literal["transcription_completed"] = "transcription_completed"
    final_segments: int = Field(ge=0, le=200, strict=True)
    input_samples: int = Field(gt=0, strict=True)


class AudioEvent(Contract):
    kind: Literal["audio"] = "audio"
    chunk: AudioChunk


class SynthesisCompleted(Contract):
    kind: Literal["synthesis_completed"] = "synthesis_completed"
    output_samples: int = Field(gt=0, strict=True)


TranscriptionEvent = Annotated[
    TranscriptEvent | TranscriptionCompleted, Field(discriminator="kind")
]
SynthesisEvent = Annotated[AudioEvent | SynthesisCompleted, Field(discriminator="kind")]
