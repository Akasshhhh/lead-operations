"""Explicit mock/real speech setup; validate keys without exposing environment data."""

import os
from collections.abc import Mapping
from typing import Literal, Self

import httpx
from pydantic import Field, SecretStr, ValidationError, model_validator

from .contracts import Contract
from .http_tts import LANGUAGES, RumikTTSProvider, SarvamTTSProvider
from .mock import MockSTTProvider, MockTTSProvider
from .provider import STTProvider, TTSProvider
from .router import SpeechCapabilities, SpeechRouterPolicy, SpeechSlot, STTRouter, TTSRouter
from .sarvam_stt import Connector, SarvamSTTProvider, websocket_connector


class SpeechConfigurationError(ValueError):
    pass


class SpeechSettings(Contract):
    mode: Literal["mock", "real"] = "mock"
    stt_providers: tuple[Literal["sarvam"], ...] = ("sarvam",)
    tts_providers: tuple[Literal["sarvam", "rumik"], ...] = ("sarvam", "rumik")
    sarvam_key: SecretStr | None = Field(default=None, repr=False)
    rumik_key: SecretStr | None = Field(default=None, repr=False)
    sarvam_voice: str = Field(
        default="shubh", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    rumik_voice: str = Field(
        default="siya", min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    rumik_model: Literal["mulberry", "muga"] = "mulberry"
    policy: SpeechRouterPolicy = Field(default_factory=SpeechRouterPolicy)

    @model_validator(mode="after")
    def configured(self) -> Self:
        for names in (self.stt_providers, self.tts_providers):
            if not names or len(names) != len(set(names)):
                raise ValueError("provider order must be nonempty and unique")
        if self.mode == "real":
            enabled = set(self.stt_providers) | set(self.tts_providers)
            for name, key in (("sarvam", self.sarvam_key), ("rumik", self.rumik_key)):
                if name in enabled and (
                    key is None
                    or not key.get_secret_value()
                    or any(ord(char) < 33 or ord(char) > 126 for char in key.get_secret_value())
                ):
                    raise ValueError("enabled speech providers require printable ASCII keys")
        return self

    @classmethod
    def from_env(cls) -> "SpeechSettings":
        return cls.from_mapping(os.environ)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "SpeechSettings":
        try:
            return cls(
                mode=values.get("SPEECH_MODE", "mock"),  # type: ignore[arg-type]
                stt_providers=tuple(values.get("STT_PROVIDERS", "sarvam").split(",")),  # type: ignore[arg-type]
                tts_providers=tuple(values.get("TTS_PROVIDERS", "sarvam,rumik").split(",")),  # type: ignore[arg-type]
                sarvam_key=SecretStr(values["SARVAM_API_KEY"])
                if values.get("SARVAM_API_KEY")
                else None,
                rumik_key=SecretStr(values["RUMIK_API_KEY"])
                if values.get("RUMIK_API_KEY")
                else None,
                sarvam_voice=values.get("SARVAM_TTS_VOICE", "shubh"),
                rumik_voice=values.get("RUMIK_TTS_VOICE", "siya"),
                rumik_model=values.get("RUMIK_TTS_MODEL", "mulberry"),  # type: ignore[arg-type]
                policy=SpeechRouterPolicy(
                    max_attempts=int(values.get("SPEECH_MAX_ATTEMPTS", "2")),
                    attempt_timeout_seconds=float(
                        values.get("SPEECH_ATTEMPT_TIMEOUT_SECONDS", "4")
                    ),
                    retry_delay_seconds=float(values.get("SPEECH_RETRY_DELAY_SECONDS", ".1")),
                    failure_threshold=int(values.get("SPEECH_FAILURE_THRESHOLD", "3")),
                    cooldown_seconds=float(values.get("SPEECH_COOLDOWN_SECONDS", "15")),
                ),
            )
        except (ValueError, ValidationError):
            raise SpeechConfigurationError("invalid speech configuration") from None

    def build_stt(self, *, connector: Connector = websocket_connector) -> STTRouter:
        if self.mode == "mock":
            return STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),), policy=self.policy)
        assert self.sarvam_key is not None
        return STTRouter(
            (
                SpeechSlot[STTProvider](
                    SarvamSTTProvider(self.sarvam_key, connector=connector),
                    SpeechCapabilities(sample_rates=frozenset({8000, 16000}), languages=LANGUAGES),
                ),
            ),
            policy=self.policy,
        )

    def build_tts(self, client: httpx.AsyncClient) -> TTSRouter:
        if self.mode == "mock":
            return TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),), policy=self.policy)
        slots: list[SpeechSlot[TTSProvider]] = []
        for name in self.tts_providers:
            if name == "sarvam":
                assert self.sarvam_key is not None
                slots.append(
                    SpeechSlot[TTSProvider](
                        SarvamTTSProvider(client, self.sarvam_key, voice=self.sarvam_voice),
                        SpeechCapabilities(
                            sample_rates=frozenset({24000}),
                            languages=LANGUAGES,
                            max_text_characters=3500,
                        ),
                    )
                )
            else:
                assert self.rumik_key is not None
                slots.append(
                    SpeechSlot[TTSProvider](
                        RumikTTSProvider(
                            client, self.rumik_key, model=self.rumik_model, voice=self.rumik_voice
                        ),
                        SpeechCapabilities(
                            sample_rates=frozenset({24000}),
                            languages=LANGUAGES,
                            max_text_characters=2000,
                        ),
                    )
                )
        return TTSRouter(tuple(slots), policy=self.policy)
