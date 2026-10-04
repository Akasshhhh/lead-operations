"""API Gateway runtime settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from voice_platform_config.service_auth import validate_service_auth


class GatewayConfigurationError(ValueError):
    """Raised when gateway configuration cannot safely start."""


@dataclass(frozen=True, slots=True)
class GatewaySettings:
    environment: str
    lead_service_url: str
    service_auth_token: str | None = field(repr=False)
    request_timeout_seconds: float

    def __post_init__(self) -> None:
        try:
            token = validate_service_auth(self.environment, self.service_auth_token)
            url = urlsplit(self.lead_service_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
                or (url.port is not None and not 1 <= url.port <= 65535)
            ):
                raise ValueError("LEAD_SERVICE_URL must be an HTTP(S) origin without credentials")
            if not 0.1 <= self.request_timeout_seconds <= 30:
                raise ValueError("LEAD_SERVICE_TIMEOUT_SECONDS must be between 0.1 and 30")
        except ValueError as exc:
            raise GatewayConfigurationError(str(exc)) from exc
        object.__setattr__(self, "service_auth_token", token)

    @classmethod
    def from_env(cls) -> GatewaySettings:
        environment = os.getenv("APP_ENV", "local").strip().lower()
        lead_service_url = os.getenv("LEAD_SERVICE_URL", "http://lead-service:8001").strip()
        token = os.getenv("LEAD_SERVICE_AUTH_TOKEN", "").strip() or None
        raw_timeout = os.getenv("LEAD_SERVICE_TIMEOUT_SECONDS", "5").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise GatewayConfigurationError(
                "LEAD_SERVICE_TIMEOUT_SECONDS must be a number"
            ) from exc
        return cls(
            environment=environment,
            lead_service_url=lead_service_url.rstrip("/"),
            service_auth_token=token,
            request_timeout_seconds=timeout,
        )
