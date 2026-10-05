"""Runtime-local health and recovery; no replay after exposed speech output."""

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from .contracts import (
    AudioChunk,
    Contract,
    SpeechRequest,
    SynthesisCompleted,
    SynthesisEvent,
    SynthesisRequest,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
)
from .provider import ErrorCode, SpeechError, SpeechStream, STTProvider, TTSProvider
from .runtime import (
    MAX_AUDIO_BYTES,
    MAX_AUDIO_SECONDS,
    SpeechToTextRuntime,
    TextToSpeechRuntime,
    _errors,
)


class SpeechRouterPolicy(Contract):
    max_attempts: int = Field(default=2, ge=1, le=3, strict=True)
    attempt_timeout_seconds: float = Field(default=4, gt=0, le=300)
    retry_delay_seconds: float = Field(default=0.1, ge=0, le=5)
    failure_threshold: int = Field(default=3, ge=1, le=20, strict=True)
    cooldown_seconds: float = Field(default=15, gt=0, le=300)


@dataclass(frozen=True)
class SpeechCapabilities:
    sample_rates: frozenset[int] = frozenset({8000, 16000, 22050, 24000, 48000})
    languages: frozenset[str] | None = None
    max_text_characters: int = 20000

    def accepts(self, request: SpeechRequest) -> bool:
        language = (
            request.language
            if isinstance(request, (SynthesisRequest, TranscriptionRequest))
            else ""
        )
        return (
            request.audio_format.sample_rate in self.sample_rates
            and (self.languages is None or language in self.languages)
            and (
                not isinstance(request, SynthesisRequest)
                or len(request.text) <= self.max_text_characters
            )
        )


@dataclass(frozen=True)
class SpeechSlot[P: STTProvider | TTSProvider]:
    provider: P
    capabilities: SpeechCapabilities = SpeechCapabilities()
    enabled: bool = True


@dataclass
class _Circuit:
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"] = "CLOSED"
    epoch: int = 0
    open_until: float = 0
    failures: int = 0
    consecutive: int = 0
    successes: int = 0
    failovers: int = 0
    last_error: ErrorCode | None = None


@dataclass(frozen=True)
class SpeechHealth:
    provider: str
    model: str
    enabled: bool
    circuit_state: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    success_count: int
    failure_count: int
    consecutive_failures: int
    failover_count: int
    last_error: ErrorCode | None
    retry_after_seconds: float


class _Router[P: STTProvider | TTSProvider]:
    def __init__(
        self,
        slots: tuple[SpeechSlot[P], ...],
        *,
        policy: SpeechRouterPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        names = [slot.provider.name for slot in slots]
        if not slots or len(slots) > 4 or len(set(names)) != len(names):
            raise ValueError("speech router requires 1–4 unique providers")
        self.slots = slots
        self.policy = policy or SpeechRouterPolicy()
        self.clock = clock
        self.circuits = {name: _Circuit() for name in names}

    def health(self) -> tuple[SpeechHealth, ...]:
        return tuple(
            SpeechHealth(
                slot.provider.name,
                slot.provider.model,
                slot.enabled,
                (c := self.circuits[slot.provider.name]).state,
                c.successes,
                c.failures,
                c.consecutive,
                c.failovers,
                c.last_error,
                max(0, c.open_until - self.clock()),
            )
            for slot in self.slots
        )

    async def _events[R: SpeechRequest, E](
        self,
        request: R,
        operation: Callable[[P, R], SpeechStream[E]],
        *,
        can_retry: Callable[[], bool] = lambda: True,
    ) -> AsyncGenerator[E, None]:
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        error = SpeechError("unavailable", request_id=request.request_id, provider="speech-router")
        previous: _Circuit | None = None
        for slot in self.slots:
            if not slot.enabled or not slot.capabilities.accepts(request):
                continue
            accepts = getattr(slot.provider, "accepts", None)
            if callable(accepts) and not accepts(request):
                continue
            circuit = self.circuits[slot.provider.name]
            for attempt in range(self.policy.max_attempts):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise SpeechError(
                        "timeout", request_id=request.request_id, provider="speech-router"
                    )
                if circuit.state == "HALF_OPEN" or (
                    circuit.state == "OPEN" and self.clock() < circuit.open_until
                ):
                    break
                probe = circuit.state == "OPEN"
                if probe:
                    circuit.state = "HALF_OPEN"
                epoch = circuit.epoch
                if previous is not None and previous is not circuit:
                    previous.failovers += 1
                previous = circuit
                exposed = finished = False
                data = request.model_copy(
                    update={"timeout_seconds": min(remaining, self.policy.attempt_timeout_seconds)}
                )
                try:
                    async with aclosing(operation(slot.provider, data)) as events:
                        async for event in events:
                            if isinstance(event, (SynthesisCompleted, TranscriptionCompleted)):
                                finished = True
                                circuit.successes += 1
                                if circuit.epoch == epoch:
                                    circuit.state = "CLOSED"
                                    circuit.consecutive = 0
                                    circuit.last_error = None
                                    circuit.open_until = 0
                            exposed = True
                            yield event
                    return
                except SpeechError as exc:
                    error = exc
                    if exc.code == "invalid_input":
                        raise  # Bad caller audio is not provider health failure.
                    circuit.failures += 1
                    if epoch == circuit.epoch:
                        circuit.consecutive += 1
                        circuit.last_error = exc.code
                        if probe or circuit.consecutive >= self.policy.failure_threshold:
                            circuit.state = "OPEN"
                            circuit.epoch += 1
                            circuit.open_until = self.clock() + self.policy.cooldown_seconds
                    if exposed or not can_retry():
                        raise
                    if (
                        not exc.retryable
                        or probe
                        or attempt + 1 == self.policy.max_attempts
                        or circuit.state != "CLOSED"
                    ):
                        break
                    delay = self.policy.retry_delay_seconds * 2**attempt
                    if deadline - asyncio.get_running_loop().time() <= delay:
                        break  # Leave budget for a faster fallback.
                    await asyncio.sleep(delay)
                finally:
                    if (
                        probe
                        and not finished
                        and circuit.epoch == epoch
                        and circuit.state == "HALF_OPEN"
                    ):
                        circuit.state = "OPEN"
        raise error


class TTSRouter(_Router[TTSProvider]):
    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        request = SynthesisRequest.model_validate(request)
        async with aclosing(
            self._events(request, lambda p, r: TextToSpeechRuntime(p).synthesize(r))
        ) as events:
            async for event in events:
                yield event


class _Replay:
    def __init__(self, audio: SpeechStream[AudioChunk], request: TranscriptionRequest) -> None:
        self.audio = audio
        self.request = request
        self.saved: list[AudioChunk] = []
        self.samples = 0
        self.eof = False
        self.safe = True

    async def view(self) -> AsyncGenerator[AudioChunk, None]:
        index = 0
        while True:
            if index < len(self.saved):
                chunk = self.saved[index]
            elif self.eof:
                return
            else:
                try:
                    raw = await anext(self.audio)
                except StopAsyncIteration:
                    self.eof = True
                    return
                except BaseException:
                    # Cancellation during a read may close a generator or lose a frame.
                    self.safe = False
                    raise
                try:
                    chunk = AudioChunk.model_validate(raw)
                    if chunk.sequence != len(self.saved):
                        raise ValueError("invalid input sequence")
                    self.samples += len(chunk.data) // 2
                    if (
                        len(self.saved) >= 10000
                        or self.samples * 2 > MAX_AUDIO_BYTES
                        or self.samples > MAX_AUDIO_SECONDS * self.request.audio_format.sample_rate
                    ):
                        raise ValueError("input exceeds replay budget")
                except ValueError:
                    self.safe = False
                    raise SpeechError(
                        "invalid_input",
                        request_id=self.request.request_id,
                        provider="speech-router",
                    ) from None
                self.saved.append(chunk)
            index += 1
            yield chunk


class STTRouter(_Router[STTProvider]):
    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        request = TranscriptionRequest.model_validate(request)
        replay = _Replay(audio, request)
        async with _errors(request, "speech-router"), aclosing(audio):
            async with aclosing(
                self._events(
                    request,
                    lambda p, r: SpeechToTextRuntime(p).transcribe(r, replay.view()),
                    can_retry=lambda: replay.safe,
                )
            ) as events:
                async for event in events:
                    yield event
