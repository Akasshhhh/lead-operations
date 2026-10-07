"""Native volume gating rejects quiet disturbances without disabling real barge-in."""

import pytest
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADState
from pydantic import ValidationError
from voice_platform_runtime.app import configured_vad_params


def test_volume_setting_preserves_native_speech_confidence_and_endpointing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VOICE_VAD_MIN_VOLUME", raising=False)
    params = configured_vad_params()
    assert params.min_volume == 0.65
    assert (params.confidence, params.start_secs, params.stop_secs) == (0.7, 0.2, 0.4)
    monkeypatch.setenv("VOICE_VAD_MIN_VOLUME", "0.75")
    assert configured_vad_params().min_volume == 0.75


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf", "quiet"])
def test_invalid_volume_setting_is_rejected(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOICE_VAD_MIN_VOLUME", value)
    with pytest.raises(ValidationError):
        configured_vad_params()


@pytest.mark.asyncio
async def test_native_vad_requires_volume_confidence_and_sustained_onset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VOICE_VAD_MIN_VOLUME", "0.75")
    analyzer = SileroVADAnalyzer(params=configured_vad_params())
    analyzer.set_sample_rate(16000)
    confidence, volume = 1.0, 0.70
    monkeypatch.setattr(analyzer, "voice_confidence", lambda audio: confidence)
    monkeypatch.setattr(analyzer, "_get_smoothed_volume", lambda audio: volume)
    audio = b"\0\0" * 512  # 32 ms native Silero frame

    async def samples(count: int) -> list[VADState]:
        return [await analyzer.analyze_audio(audio) for _ in range(count)]

    try:
        assert set(await samples(20)) == {VADState.QUIET}  # Confident but too quiet.
        volume, confidence = 0.80, 0.60
        assert set(await samples(20)) == {VADState.QUIET}  # Loud but not speech.
        confidence = 1.0
        assert VADState.SPEAKING not in await samples(3)  # A brief disturbance.
        confidence = 0.0
        assert (await samples(1))[-1] == VADState.QUIET
        confidence = 1.0
        assert (await samples(10))[-1] == VADState.SPEAKING
        volume = 0.70
        assert (await samples(15))[-1] == VADState.QUIET  # Existing silence endpoint.
    finally:
        await analyzer.cleanup()  # type: ignore[no-untyped-call]
