"""Runtime-local capability selection, bounded retries, and circuit recovery."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from .contracts import (
    CompletionEvent,
    Contract,
    GenerationRequest,
    GenerationResponse,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from .provider import ErrorCode, LLMError, LLMProvider
from .runtime import ConversationLLMRuntime


class RouterPolicy(Contract):
    max_attempts_per_provider: int = Field(default=2, ge=1, le=3, strict=True)
    attempt_timeout_seconds: float = Field(default=4, gt=0, le=300)
    retry_delay_seconds: float = Field(default=0.1, ge=0, le=5)
    failure_threshold: int = Field(default=3, ge=1, le=20, strict=True)
    cooldown_seconds: float = Field(default=15, gt=0, le=300)


@dataclass(frozen=True)
class ProviderCapabilities:
    tools: bool = True
    max_output_tokens: int = 32_768
    max_temperature: float = 2
    requires_messages: bool = False

    def accepts(self, request: GenerationRequest) -> bool:
        needs_tools = bool(request.tools) or any(
            message.tool_calls or message.role == "tool" for message in request.context.messages
        )
        return (
            (self.tools or not needs_tools)
            and request.max_output_tokens <= self.max_output_tokens
            and request.temperature <= self.max_temperature
            and (not self.requires_messages or bool(request.context.messages))
        )


@dataclass(frozen=True)
class ProviderSlot:
    provider: LLMProvider
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    enabled: bool = True


@dataclass
class _Circuit:
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"] = "CLOSED"
    epoch: int = 0
    open_until: float = 0
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    failovers: int = 0
    last_error: ErrorCode | None = None


@dataclass(frozen=True)
class ProviderHealthSnapshot:
    provider: str
    model: str
    enabled: bool
    circuit_state: Literal["CLOSED", "OPEN", "HALF_OPEN"]
    status: Literal["UNKNOWN", "HEALTHY", "DEGRADED", "UNAVAILABLE", "DISABLED"]
    success_count: int
    failure_count: int
    consecutive_failures: int
    failover_count: int
    last_error: ErrorCode | None
    retry_after_seconds: float


class LLMRouter:
    """Use on one event loop. Health is operational memory, never business truth."""

    def __init__(
        self,
        slots: tuple[ProviderSlot, ...],
        *,
        policy: RouterPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        names = [slot.provider.name for slot in slots]
        if not slots or len(slots) > 4 or len(names) != len(set(names)):
            raise ValueError("router requires 1–4 uniquely named providers")
        self.slots = slots
        self.policy = policy or RouterPolicy()
        self._clock = clock
        self._circuits = {name: _Circuit() for name in names}

    def health(self) -> tuple[ProviderHealthSnapshot, ...]:
        result = []
        for slot in self.slots:
            circuit = self._circuits[slot.provider.name]
            status: Literal["UNKNOWN", "HEALTHY", "DEGRADED", "UNAVAILABLE", "DISABLED"] = "UNKNOWN"
            if not slot.enabled:
                status = "DISABLED"
            elif circuit.state != "CLOSED":
                status = "UNAVAILABLE"
            elif circuit.consecutive_failures:
                status = "DEGRADED"
            elif circuit.successes:
                status = "HEALTHY"
            result.append(
                ProviderHealthSnapshot(
                    provider=slot.provider.name,
                    model=slot.provider.model,
                    enabled=slot.enabled,
                    circuit_state=circuit.state,
                    status=status,
                    success_count=circuit.successes,
                    failure_count=circuit.failures,
                    consecutive_failures=circuit.consecutive_failures,
                    failover_count=circuit.failovers,
                    last_error=circuit.last_error,
                    retry_after_seconds=max(0, circuit.open_until - self._clock()),
                )
            )
        return tuple(result)

    def _admit(self, circuit: _Circuit) -> tuple[int, bool] | None:
        # No awaits: admission/state updates are atomic within this event loop.
        if circuit.state == "HALF_OPEN":
            return None
        if circuit.state == "OPEN":
            if self._clock() < circuit.open_until:
                return None
            circuit.state = "HALF_OPEN"
            return circuit.epoch, True
        return circuit.epoch, False

    def _failed(self, circuit: _Circuit, epoch: int, code: ErrorCode) -> None:
        circuit.failures += 1
        if epoch != circuit.epoch:
            return
        circuit.last_error = code
        circuit.consecutive_failures += 1
        if (
            circuit.state == "HALF_OPEN"
            or circuit.consecutive_failures >= self.policy.failure_threshold
        ):
            circuit.state = "OPEN"
            circuit.epoch += 1
            circuit.open_until = self._clock() + self.policy.cooldown_seconds

    def _succeeded(self, circuit: _Circuit, epoch: int) -> None:
        circuit.successes += 1
        if epoch == circuit.epoch:
            circuit.state = "CLOSED"
            circuit.consecutive_failures = 0
            circuit.last_error = None
            circuit.open_until = 0

    async def _events(
        self, request: GenerationRequest, *, buffered: bool
    ) -> AsyncGenerator[tuple[ProviderSlot, StreamEvent], None]:
        request = GenerationRequest.model_validate_json(request.model_dump_json())
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        last_error = LLMError("unavailable", request_id=request.request_id, provider="router")
        previous: _Circuit | None = None
        for slot in self.slots:
            if not slot.enabled or not slot.capabilities.accepts(request):
                continue
            circuit = self._circuits[slot.provider.name]
            for attempt in range(self.policy.max_attempts_per_provider):
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise LLMError("timeout", request_id=request.request_id, provider="router")
                lease = self._admit(circuit)
                if lease is None:
                    break
                if previous is not None and previous is not circuit:
                    previous.failovers += 1
                previous = circuit
                epoch, probe = lease
                exposed = finished = False
                buffered_events: list[StreamEvent] = []
                data = request.model_copy(
                    update={"timeout_seconds": min(remaining, self.policy.attempt_timeout_seconds)}
                )
                try:
                    async with aclosing(
                        ConversationLLMRuntime(slot.provider).stream(data)
                    ) as events:
                        async for event in events:
                            if isinstance(event, CompletionEvent):
                                self._succeeded(circuit, epoch)
                                finished = True
                            if buffered:
                                buffered_events.append(event)
                            else:
                                exposed = True
                                yield slot, event
                    if buffered:
                        for event in buffered_events:
                            yield slot, event
                    return
                except LLMError as exc:
                    last_error = exc
                    self._failed(circuit, epoch, exc.code)
                    if exposed:
                        raise
                    if (
                        not exc.retryable
                        or probe
                        or attempt + 1 >= self.policy.max_attempts_per_provider
                    ):
                        break
                    if circuit.state != "CLOSED":
                        break
                    delay = self.policy.retry_delay_seconds * (2**attempt)
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise LLMError(
                            "timeout", request_id=request.request_id, provider="router"
                        ) from None
                    if remaining <= delay:
                        # Leave the remaining budget available to a faster fallback.
                        break
                    await asyncio.sleep(delay)
                finally:
                    if (
                        probe
                        and not finished
                        and circuit.epoch == epoch
                        and circuit.state == "HALF_OPEN"
                    ):
                        # Cancelled/closed probes release their slot without recording a failure.
                        circuit.state = "OPEN"
        raise last_error

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        """Fail over only before the first exposed event; callers must close early exits."""
        async with aclosing(self._events(request, buffered=False)) as events:
            async for _, event in events:
                yield event

    async def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Buffer attempts; discard failed output before retry/failover."""
        text = ""
        calls: list[ToolCall] = []
        async with aclosing(self._events(request, buffered=True)) as events:
            async for slot, event in events:
                if isinstance(event, TextDelta):
                    text += event.text
                elif isinstance(event, ToolCallEvent):
                    calls.append(event.call)
                elif isinstance(event, CompletionEvent):
                    return GenerationResponse(
                        request_id=request.request_id,
                        provider=slot.provider.name,
                        model=slot.provider.model,
                        text=text,
                        tool_calls=tuple(calls),
                        finish_reason=event.finish_reason,
                        usage=event.usage,
                    )
        raise LLMError("invalid_output", request_id=request.request_id, provider="router")
