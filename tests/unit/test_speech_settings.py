import httpx
import pytest
from voice_platform_speech import SpeechConfigurationError, SpeechSettings


@pytest.mark.asyncio
async def test_mock_configuration_needs_no_keys_or_live_connections() -> None:
    settings = SpeechSettings.from_mapping({})
    assert settings.mode == "mock"
    assert settings.build_stt().health()[0].provider == "mock-stt"
    async with httpx.AsyncClient() as client:
        assert settings.build_tts(client).health()[0].provider == "mock-tts"


@pytest.mark.parametrize(
    "values",
    [
        {"SPEECH_MODE": "unknown"},
        {"SPEECH_MODE": "real"},
        {"STT_PROVIDERS": "rumik"},
        {"TTS_PROVIDERS": ""},
        {"TTS_PROVIDERS": "sarvam,sarvam"},
        {"TTS_PROVIDERS": "openai"},
        {"RUMIK_TTS_MODEL": "invalid"},
        {"SARVAM_TTS_VOICE": "bad voice"},
        {"SPEECH_MAX_ATTEMPTS": "4"},
        {"SPEECH_ATTEMPT_TIMEOUT_SECONDS": "nan"},
        {"SPEECH_COOLDOWN_SECONDS": "0"},
        {"SPEECH_MODE": "real", "SARVAM_API_KEY": "private\nkey", "RUMIK_API_KEY": "private"},
    ],
)
def test_invalid_configuration_is_content_free(values: dict[str, str]) -> None:
    with pytest.raises(SpeechConfigurationError) as failure:
        SpeechSettings.from_mapping(values)
    assert "private" not in str(failure.value)


@pytest.mark.asyncio
async def test_real_order_capabilities_and_secret_redaction() -> None:
    settings = SpeechSettings.from_mapping(
        {
            "SPEECH_MODE": "real",
            "SARVAM_API_KEY": "private-sarvam",
            "RUMIK_API_KEY": "private-rumik",
            "TTS_PROVIDERS": "rumik,sarvam",
        }
    )
    assert "private" not in repr(settings)
    async with httpx.AsyncClient() as client:
        router = settings.build_tts(client)
        assert [health.provider for health in router.health()] == ["rumik-tts", "sarvam-tts"]
        assert [slot.capabilities.max_text_characters for slot in router.slots] == [2000, 3500]
        assert settings.build_stt().slots[0].capabilities.sample_rates == {8000, 16000}


def test_disabled_rumik_key_is_not_required() -> None:
    settings = SpeechSettings.from_mapping(
        {"SPEECH_MODE": "real", "SARVAM_API_KEY": "key", "TTS_PROVIDERS": "sarvam"}
    )
    assert settings.rumik_key is None
