"""Conversation Service configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from voice_platform_config.service_auth import validate_service_auth


class ConversationConfigurationError(ValueError):
    """Raised when Conversation Service configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class ConversationSettings:
    environment: str
    lead_service_url: str
    service_auth_token: str | None = field(repr=False)
    request_timeout_seconds: float

    def __post_init__(self) -> None:
        try:
            token = validate_service_auth(self.environment, self.service_auth_token)
            parsed = urlsplit(self.lead_service_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("LEAD_SERVICE_URL must be an HTTP(S) origin")
            if not 0.1 <= self.request_timeout_seconds <= 30:
                raise ValueError("CONVERSATION_SERVICE_TIMEOUT_SECONDS must be between 0.1 and 30")
        except ValueError as exc:
            raise ConversationConfigurationError(str(exc)) from exc
        object.__setattr__(self, "service_auth_token", token)

    @classmethod
    def from_env(cls) -> ConversationSettings:
        environment = os.getenv("APP_ENV", "local").strip().lower()
        raw_timeout = os.getenv("CONVERSATION_SERVICE_TIMEOUT_SECONDS", "5").strip()
        try:
            timeout = float(raw_timeout)
        except ValueError as exc:
            raise ConversationConfigurationError(
                "CONVERSATION_SERVICE_TIMEOUT_SECONDS must be a number"
            ) from exc
        return cls(
            environment=environment,
            lead_service_url=os.getenv("LEAD_SERVICE_URL", "http://lead-service:8001")
            .strip()
            .rstrip("/"),
            service_auth_token=os.getenv("LEAD_SERVICE_AUTH_TOKEN", "").strip() or None,
            request_timeout_seconds=timeout,
        )
