import httpx
import pytest
from voice_platform_config import ConfigurationError
from voice_platform_llm import LLMSettings


@pytest.mark.asyncio
async def test_default_mock_needs_no_credentials_or_database() -> None:
    settings = LLMSettings.from_mapping({})
    async with httpx.AsyncClient() as client:
        assert settings.build_router(client).health()[0].provider == "mock"


@pytest.mark.parametrize(
    "values",
    [
        {"LLM_MODE": "bad"},
        {"LLM_MODE": "real"},
        {"LLM_PROVIDERS": "openai,openai"},
        {"LLM_PROVIDERS": ""},
        {"LLM_PROVIDERS": "unknown"},
        {"OPENAI_MODEL": ""},
        {"OPENROUTER_MODEL": "bad\nmodel"},
        {"LLM_MAX_ATTEMPTS": "0"},
        {"LLM_MAX_ATTEMPTS": "bad"},
        {"LLM_ATTEMPT_TIMEOUT_SECONDS": "nan"},
        {"LLM_COOLDOWN_SECONDS": "inf"},
        {"LLM_MODE": "real", "OPENAI_API_KEY": "SECRET\nvalue", "OPENROUTER_API_KEY": "test"},
    ],
)
def test_configuration_failures_are_redacted(values: dict[str, str]) -> None:
    with pytest.raises(ConfigurationError) as caught:
        LLMSettings.from_mapping(values)
    assert "SECRET" not in str(caught.value)


@pytest.mark.asyncio
async def test_real_order_credentials_and_model_configuration() -> None:
    settings = LLMSettings.from_mapping(
        {
            "LLM_MODE": "real",
            "LLM_PROVIDERS": "openrouter,openai",
            "OPENAI_API_KEY": "secret-a",
            "OPENROUTER_API_KEY": "secret-b",
            "OPENAI_MODEL": "custom-compatible-model",
        }
    )
    assert "secret-a" not in repr(settings) and "secret-b" not in repr(settings)
    async with httpx.AsyncClient() as client:
        router = settings.build_router(client)
        assert [item.provider for item in router.health()] == ["openrouter", "openai"]
        assert router.health()[1].model == "custom-compatible-model"
        assert router.slots[0].capabilities.max_output_tokens == 16_384
