from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from voice_platform_speech import (
    AudioChunk,
    AudioFormat,
    SynthesisRequest,
    TranscriptEvent,
    TranscriptionRequest,
    fixture_pcm,
)


def identity() -> dict[str, Any]:
    return {key: uuid4() for key in ("request_id", "conversation_id", "call_id", "utterance_id")}


@pytest.mark.parametrize(
    "values",
    [
        {"encoding": "mp3"},
        {"sample_rate": 44100},
        {"channels": 2},
        {"channels": True},
        {"sample_rate": 16000.0},
        {"unknown": 1},
    ],
)
def test_invalid_audio_format(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AudioFormat.model_validate(values)


@pytest.mark.parametrize(
    "values",
    [
        {"sequence": -1, "data": b"aa"},
        {"sequence": True, "data": b"aa"},
        {"sequence": 0, "data": b""},
        {"sequence": 0, "data": b"a"},
        {"sequence": 0, "data": b"a" * 65538},
        {"sequence": 0, "data": "aa"},
    ],
)
def test_invalid_audio_chunk(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AudioChunk.model_validate(values)


@pytest.mark.parametrize("text", ["", "  ", "secret\x00", "\ud800", "a" * 20001])
def test_invalid_text(text: str) -> None:
    with pytest.raises(ValidationError):
        SynthesisRequest(**identity(), text=text)
    with pytest.raises(ValidationError):
        TranscriptEvent(
            segment_id="s", segment_index=0, text=text, is_final=True, start_sample=0, end_sample=1
        )


@pytest.mark.parametrize("timeout", [0, -1, 301, float("nan"), float("inf")])
def test_invalid_deadline(timeout: float) -> None:
    with pytest.raises(ValidationError):
        TranscriptionRequest(**identity(), timeout_seconds=timeout)


def test_contract_identity_unicode_roundtrip_and_frozen_fields() -> None:
    request = SynthesisRequest(**identity(), text="नमस्ते", language="hi-IN")
    assert SynthesisRequest.model_validate_json(request.model_dump_json()) == request
    with pytest.raises(ValidationError):
        request.text = "changed"
    chunk = AudioChunk(sequence=0, data=b"private audio!")
    assert "private audio" not in repr(chunk)
    assert chunk.data == b"private audio!"


@pytest.mark.parametrize("rate", [8000, 16000, 22050, 24000, 48000])
def test_fixture_explicit_rate_and_determinism(rate: Any) -> None:
    audio_format = AudioFormat(sample_rate=rate)
    data = fixture_pcm(audio_format)
    assert data == fixture_pcm(audio_format)
    assert len(data) == rate // 50 * 2
    assert len(data) % audio_format.bytes_per_sample == 0
    assert any(data)


@pytest.mark.parametrize(
    "values", [{"confidence": 2}, {"is_final": "true"}, {"start_sample": 2, "end_sample": 1}]
)
def test_invalid_transcript_metadata(values: dict[str, Any]) -> None:
    event = {
        "segment_id": "s",
        "segment_index": 0,
        "text": "hello",
        "is_final": True,
        "start_sample": 0,
        "end_sample": 1,
    } | values
    with pytest.raises(ValidationError):
        TranscriptEvent.model_validate(event)
