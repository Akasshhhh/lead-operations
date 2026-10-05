"""Capability-scoped, expiring demo faults at provider/HTTP boundaries, before effects."""

import asyncio
import os
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from voice_platform_config.observability import Telemetry
from voice_platform_llm import GenerationRequest, LLMError, StreamEvent
from voice_platform_llm.provider import LLMProvider
from voice_platform_speech import (
    AudioChunk,
    SpeechError,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptionEvent,
    TranscriptionRequest,
)
from voice_platform_speech.provider import SpeechStream, STTProvider, TTSProvider

from .backend import Backend, DependencyError


class FaultCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: UUID
    target: Literal["llm", "stt", "tts", "dependency"]
    mode: Literal["unavailable", "latency", "timeout"]
    attempts: int = Field(default=2, ge=1, le=10, strict=True)
    duration_seconds: int = Field(default=60, ge=1, le=60, strict=True)


def faults_enabled() -> bool:
    raw = os.getenv("DEMO_FAULTS_ENABLED", "0")
    if raw not in {"0", "1"}:
        raise ValueError("DEMO_FAULTS_ENABLED must be 0 or 1")
    enabled = raw == "1"
    if enabled and os.getenv("APP_ENV", "local") not in {"local", "test"}:
        raise ValueError("Demo faults require APP_ENV=local or test")
    return enabled


@dataclass
class ArmedFault:
    data: FaultCreate
    expires: float
    remaining: int


class Faults:
    def __init__(self, telemetry: Telemetry) -> None:
        self.telemetry = telemetry
        self.active: ArmedFault | None = None
        self.receipts: dict[UUID, FaultCreate] = {}

    def arm(self, data: FaultCreate) -> None:
        previous = self.receipts.get(data.operation_id)
        if previous is not None:
            if previous != data:
                raise FaultConflict("fault identity changed")
            return  # Replay never extends TTL/replenishes attempts, even after reset/expiry.
        if len(self.receipts) >= 32:
            raise FaultConflict("session fault control capacity reached")
        if (data.target == "dependency") != (data.mode == "timeout"):
            raise ValueError("Dependency supports timeout; providers support unavailable/latency")
        # One fault per session; replacing one is explicit and bounded.
        self.active = ArmedFault(data, time.monotonic() + data.duration_seconds, data.attempts)
        self.receipts[data.operation_id] = data
        self.telemetry.record("fault.arm", "ok", 0)

    def reset(self) -> None:
        self.active = None
        self.telemetry.record("fault.reset", "ok", 0)

    def snapshot(self) -> dict[str, object] | None:
        active = self.active
        if active is None:
            return None
        if time.monotonic() >= active.expires or active.remaining == 0:
            self.active = None
            return None
        return {
            **active.data.model_dump(mode="json"),
            "remaining_attempts": active.remaining,
            "expires_in_seconds": round(max(0, active.expires - time.monotonic()), 2),
        }

    async def hit(self, target: str, *, timeout: float) -> str | None:
        if self.snapshot() is None or self.active is None or self.active.data.target != target:
            return None
        active = self.active
        active.remaining -= 1  # Atomic reservation before await; reset cannot replay an attempt.
        mode = active.data.mode
        self.telemetry.record(f"fault.{target}", mode, 0)
        if mode == "latency":
            await asyncio.sleep(1)
        elif mode == "timeout":
            # Runs under the existing boundary timeout; never dispatches a mutation.
            await asyncio.sleep(timeout + 0.1)
        return mode


class FaultConflict(RuntimeError):
    """Changed or exhausted control identities must not rearm a fault."""


class ObservedBackend(Backend):
    def __init__(self, backend: Backend, telemetry: Telemetry, faults: Faults) -> None:
        super().__init__(backend.client, backend.token, backend.timeout)
        self.backend, self.telemetry, self.faults = backend, telemetry, faults

    async def request[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        request_id: UUID,
        payload: BaseModel | None = None,
    ) -> T:
        operation = "conversation.apply" if path.endswith("/apply") else "conversation.http"
        with self.telemetry.span(operation, request_id=str(request_id)) as result:
            try:
                async with asyncio.timeout(self.timeout):
                    if operation == "conversation.apply":
                        await self.faults.hit("dependency", timeout=self.timeout)
                    return await self.backend.request(method, path, model, request_id, payload)
            except TimeoutError:
                result["outcome"] = "timeout"
                raise DependencyError() from None
            except DependencyError as exc:
                result["outcome"] = "dependency_error"
                raise exc


class ObservedLLM:
    def __init__(
        self, provider: LLMProvider, telemetry: Telemetry, faults: Faults, primary: bool
    ) -> None:
        self.provider, self.telemetry, self.faults, self.primary = (
            provider,
            telemetry,
            faults,
            primary,
        )
        self.name, self.model = provider.name, provider.model

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        with self.telemetry.span(
            "llm.attempt",
            provider=self.name,
            request_id=str(request.request_id),
            conversation_id=str(request.conversation_id),
            turn_id=str(request.turn_id),
        ) as result:
            try:
                mode = (
                    await self.faults.hit("llm", timeout=request.timeout_seconds)
                    if self.primary
                    else None
                )
                if mode == "unavailable":
                    raise LLMError("unavailable", request_id=request.request_id, provider=self.name)
                async with aclosing(self.provider.stream(request)) as stream:
                    async for event in stream:
                        yield event
            except LLMError as exc:
                result["outcome"] = exc.code
                raise
            except asyncio.CancelledError:
                result["outcome"] = "cancelled"
                raise


class ObservedSTT:
    def __init__(
        self, provider: STTProvider, telemetry: Telemetry, faults: Faults, primary: bool
    ) -> None:
        self.provider, self.telemetry, self.faults, self.primary = (
            provider,
            telemetry,
            faults,
            primary,
        )
        self.name, self.model = provider.name, provider.model

    def accepts(self, request: TranscriptionRequest) -> bool:
        accepts = getattr(self.provider, "accepts", None)
        return bool(accepts(request)) if callable(accepts) else True

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        with self.telemetry.span(
            "stt.attempt",
            provider=self.name,
            request_id=str(request.request_id),
            conversation_id=str(request.conversation_id),
            call_id=str(request.call_id),
            turn_id=str(request.utterance_id),
        ) as result:
            try:
                mode = (
                    await self.faults.hit("stt", timeout=request.timeout_seconds)
                    if self.primary
                    else None
                )
                if mode == "unavailable":
                    raise SpeechError(
                        "unavailable", request_id=request.request_id, provider=self.name
                    )
                async with aclosing(self.provider.transcribe(request, audio)) as stream:
                    async for event in stream:
                        yield event
            except SpeechError as exc:
                result["outcome"] = exc.code
                raise
            except asyncio.CancelledError:
                result["outcome"] = "cancelled"
                raise


class ObservedTTS:
    def __init__(
        self, provider: TTSProvider, telemetry: Telemetry, faults: Faults, primary: bool
    ) -> None:
        self.provider, self.telemetry, self.faults, self.primary = (
            provider,
            telemetry,
            faults,
            primary,
        )
        self.name, self.model = provider.name, provider.model

    def accepts(self, request: SynthesisRequest) -> bool:
        accepts = getattr(self.provider, "accepts", None)
        return bool(accepts(request)) if callable(accepts) else True

    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        with self.telemetry.span(
            "tts.attempt",
            provider=self.name,
            request_id=str(request.request_id),
            conversation_id=str(request.conversation_id),
            call_id=str(request.call_id),
            turn_id=str(request.utterance_id),
        ) as result:
            try:
                mode = (
                    await self.faults.hit("tts", timeout=request.timeout_seconds)
                    if self.primary
                    else None
                )
                if mode == "unavailable":
                    raise SpeechError(
                        "unavailable", request_id=request.request_id, provider=self.name
                    )
                async with aclosing(self.provider.synthesize(request)) as stream:
                    async for event in stream:
                        yield event
            except SpeechError as exc:
                result["outcome"] = exc.code
                raise
            except asyncio.CancelledError:
                result["outcome"] = "cancelled"
                raise
