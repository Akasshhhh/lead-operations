"""Stable jitter makes retry deadlines consistent across recovering workers."""

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_seconds: float = 1
    max_seconds: float = 30

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        if not 0 < self.base_seconds <= self.max_seconds <= 3600:
            raise ValueError("retry delays must satisfy 0 < base <= maximum <= 3600")

    def delay(self, attempt: int, key: str) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        ceiling = min(self.max_seconds, self.base_seconds * (1 << min(attempt - 1, 20)))
        fraction = int.from_bytes(sha256(f"{key}:{attempt}".encode()).digest()[:4]) / (2**32)
        return ceiling * (0.5 + 0.5 * fraction)
