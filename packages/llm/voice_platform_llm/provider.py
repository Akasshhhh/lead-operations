"""Provider lifecycle and controlled, content-free failure contracts."""

from collections.abc import AsyncIterator
from typing import Literal, Protocol
from uuid import UUID

from .contracts import GenerationRequest, StreamEvent

ErrorCode = Literal["timeout", "unavailable", "rate_limited", "invalid_output", "provider_error"]


class LLMError(RuntimeError):
    def __init__(self, code: ErrorCode, *, request_id: UUID, provider: str) -> None:
        super().__init__(f"LLM generation failed: {code}")
        self.code = code
        self.request_id = request_id
        self.provider = provider
        # Guidance only: the caller decides whether a retry is safe after partial output.
        self.retryable = code in {"timeout", "unavailable", "rate_limited"}


class LLMStream(Protocol):
    def __aiter__(self) -> AsyncIterator[StreamEvent]: ...

    async def __anext__(self) -> StreamEvent: ...

    async def aclose(self) -> None: ...


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, request: GenerationRequest) -> LLMStream: ...
