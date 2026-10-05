"""Explicit opt-in provider settings; mock mode needs no infrastructure/credentials."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx
from pydantic import SecretStr, ValidationError
from voice_platform_config import ConfigurationError

from .mock import MockLLMProvider
from .openai import OpenAILLMProvider
from .openrouter import OpenRouterLLMProvider
from .router import LLMRouter, ProviderCapabilities, ProviderSlot, RouterPolicy


@dataclass(frozen=True)
class LLMSettings:
    mode: str = "mock"
    providers: tuple[str, ...] = ("openai", "openrouter")
    openai_model: str = "gpt-4.1-mini-2025-04-14"
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct"
    openai_key: SecretStr | None = field(default=None, repr=False)
    openrouter_key: SecretStr | None = field(default=None, repr=False)
    policy: RouterPolicy = field(default_factory=RouterPolicy)

    @classmethod
    def from_env(cls) -> LLMSettings:
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> LLMSettings:
        mode = values.get("LLM_MODE", "mock").strip()
        if mode not in {"mock", "real"}:
            raise ConfigurationError("LLM_MODE must be mock or real")
        providers = tuple(
            item.strip() for item in values.get("LLM_PROVIDERS", "openai,openrouter").split(",")
        )
        if (
            not providers
            or len(providers) != len(set(providers))
            or any(name not in {"openai", "openrouter"} for name in providers)
        ):
            raise ConfigurationError("LLM_PROVIDERS must contain unique openai/openrouter names")
        models = [
            values.get("OPENAI_MODEL", "gpt-4.1-mini-2025-04-14"),
            values.get("OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct"),
        ]
        if any(
            not model
            or len(model) > 128
            or any(ord(char) < 33 or ord(char) > 126 for char in model)
            for model in models
        ):
            raise ConfigurationError("invalid LLM model configuration")
        keys: dict[str, SecretStr | None] = {"openai": None, "openrouter": None}
        if mode == "real":
            for name in providers:
                raw = values.get(f"{name.upper()}_API_KEY", "")
                if not raw or any(ord(char) < 33 or ord(char) > 126 for char in raw):
                    raise ConfigurationError(
                        f"{name.upper()}_API_KEY is required and must be printable ASCII"
                    )
                keys[name] = SecretStr(raw)
        try:
            policy = RouterPolicy(
                max_attempts_per_provider=int(values.get("LLM_MAX_ATTEMPTS", "2")),
                attempt_timeout_seconds=float(values.get("LLM_ATTEMPT_TIMEOUT_SECONDS", "4")),
                retry_delay_seconds=float(values.get("LLM_RETRY_DELAY_SECONDS", "0.1")),
                failure_threshold=int(values.get("LLM_FAILURE_THRESHOLD", "3")),
                cooldown_seconds=float(values.get("LLM_COOLDOWN_SECONDS", "15")),
            )
        except (ValueError, ValidationError):
            raise ConfigurationError("invalid LLM router policy configuration") from None
        return cls(
            mode, providers, models[0], models[1], keys["openai"], keys["openrouter"], policy
        )

    def build_router(self, client: httpx.AsyncClient) -> LLMRouter:
        if self.mode == "mock":
            return LLMRouter((ProviderSlot(MockLLMProvider()),), policy=self.policy)
        slots = []
        for name in self.providers:
            if name == "openai":
                if self.openai_key is None:
                    raise ConfigurationError("OPENAI_API_KEY is required")
                slots.append(
                    ProviderSlot(
                        OpenAILLMProvider(
                            client,
                            api_key=self.openai_key.get_secret_value(),
                            model=self.openai_model,
                        )
                    )
                )
            elif name == "openrouter":
                if self.openrouter_key is None:
                    raise ConfigurationError("OPENROUTER_API_KEY is required")
                slots.append(
                    ProviderSlot(
                        OpenRouterLLMProvider(
                            client,
                            api_key=self.openrouter_key.get_secret_value(),
                            model=self.openrouter_model,
                        ),
                        capabilities=ProviderCapabilities(max_output_tokens=16_384),
                    )
                )
        return LLMRouter(tuple(slots), policy=self.policy)
