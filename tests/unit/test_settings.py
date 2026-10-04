from __future__ import annotations

import pytest
from sqlalchemy.engine import make_url
from voice_platform_config import ConfigurationError, Settings
from voice_platform_config.settings import database_url_from_env

VALID_CONFIGURATION = {
    "APP_ENV": "test",
    "LOG_LEVEL": "debug",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "5432",
    "POSTGRES_DB": "voice_ai_test",
    "POSTGRES_USER": "voice_ai",
    "POSTGRES_PASSWORD": "test-password",
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
}


def test_settings_loads_and_normalizes_values() -> None:
    settings = Settings.from_mapping(VALID_CONFIGURATION)

    assert settings.app_env == "test"
    assert settings.log_level == "DEBUG"
    assert settings.postgres_port == 5432
    assert settings.redis_port == 6379


def test_settings_rejects_missing_required_values() -> None:
    configuration = dict(VALID_CONFIGURATION)
    del configuration["REDIS_HOST"]

    with pytest.raises(ConfigurationError, match="REDIS_HOST"):
        Settings.from_mapping(configuration)


def test_settings_rejects_invalid_environment() -> None:
    configuration = dict(VALID_CONFIGURATION)
    configuration["APP_ENV"] = "staging"

    with pytest.raises(ConfigurationError, match="APP_ENV"):
        Settings.from_mapping(configuration)


def test_settings_rejects_invalid_port() -> None:
    configuration = dict(VALID_CONFIGURATION)
    configuration["REDIS_PORT"] = "70000"

    with pytest.raises(ConfigurationError, match="REDIS_PORT"):
        Settings.from_mapping(configuration)


def test_settings_builds_postgres_dsn_with_escaped_credentials() -> None:
    configuration = dict(VALID_CONFIGURATION)
    configuration["POSTGRES_USER"] = "voice user"
    configuration["POSTGRES_PASSWORD"] = "secret/password"

    settings = Settings.from_mapping(configuration)

    assert settings.postgres_dsn == (
        "postgresql+asyncpg://voice%20user:secret%2Fpassword@localhost:5432/voice_ai_test"
    )


def test_database_only_configuration_preserves_and_escapes_secrets() -> None:
    values = {k: v for k, v in VALID_CONFIGURATION.items() if k.startswith("POSTGRES_")}
    password = " secret/@:$+% password "
    values.update(POSTGRES_PASSWORD=password, POSTGRES_HOST="::1")
    url = make_url(database_url_from_env(values))
    assert url.password == password
    assert url.host == "::1"
    assert password not in repr(Settings.from_mapping(VALID_CONFIGURATION | values))


def test_invalid_log_level_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="LOG_LEVEL"):
        Settings.from_mapping(VALID_CONFIGURATION | {"LOG_LEVEL": "not-a-level"})
