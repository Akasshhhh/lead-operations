"""Small, dependency-free environment configuration loader.

Service-specific configuration can build on this module without coupling the
application to a particular settings framework. Validation happens at startup
so malformed infrastructure configuration fails before a service accepts work.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote


class ConfigurationError(ValueError):
    """Raised when required platform configuration is missing or invalid."""


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "")
    if not value.strip():
        raise ConfigurationError(f"Missing required configuration: {name}")
    return value if name == "POSTGRES_PASSWORD" else value.strip()


def _port(values: Mapping[str, str], name: str) -> int:
    try:
        value = int(_required(values, name))
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not 1 <= value <= 65535:
        raise ConfigurationError(f"{name} must be between 1 and 65535")
    return value


def database_url_from_env(values: Mapping[str, str] | None = None) -> str:
    """Resolve database configuration without requiring unrelated Redis settings."""
    values = os.environ if values is None else values
    if url := values.get("DATABASE_URL", "").strip():
        return url
    host = _required(values, "POSTGRES_HOST")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = _port(values, "POSTGRES_PORT")
    user, password, database = (
        quote(_required(values, name), safe="")
        for name in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")
    )
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated configuration shared by local services."""

    app_env: str
    log_level: str
    postgres_host: str
    postgres_port: int
    postgres_db: str
    postgres_user: str
    postgres_password: str = field(repr=False)
    redis_host: str
    redis_port: int

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from the process environment."""

        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> Settings:
        """Load and validate settings from a mapping.

        A mapping is accepted to keep tests deterministic and to avoid loading
        dotenv files implicitly inside application code.
        """

        def required(name: str) -> str:
            return _required(values, name)

        def port(name: str) -> int:
            return _port(values, name)

        app_env = required("APP_ENV")
        if app_env not in {"local", "test", "production"}:
            raise ConfigurationError("APP_ENV must be local, test, or production")

        log_level = values.get("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return cls(
            app_env=app_env,
            log_level=log_level,
            postgres_host=required("POSTGRES_HOST"),
            postgres_port=port("POSTGRES_PORT"),
            postgres_db=required("POSTGRES_DB"),
            postgres_user=required("POSTGRES_USER"),
            postgres_password=required("POSTGRES_PASSWORD"),
            redis_host=required("REDIS_HOST"),
            redis_port=port("REDIS_PORT"),
        )

    @property
    def postgres_dsn(self) -> str:
        """Return an async SQLAlchemy PostgreSQL connection URL."""

        return database_url_from_env(
            {
                "POSTGRES_HOST": self.postgres_host,
                "POSTGRES_PORT": str(self.postgres_port),
                "POSTGRES_DB": self.postgres_db,
                "POSTGRES_USER": self.postgres_user,
                "POSTGRES_PASSWORD": self.postgres_password,
            }
        )
