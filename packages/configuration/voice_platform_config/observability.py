"""Bounded process-local measurements. Never record bodies, URLs, headers or exceptions."""

import json
import logging
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from voice_platform_contracts.http import normalize_request_id

logger = logging.getLogger("voice_platform.operations")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
request_context: ContextVar[str | None] = ContextVar("operation_request_id", default=None)


class Telemetry:
    """No awaits in updates; use within one event loop. Reset only on process restart."""

    def __init__(self, service: str) -> None:
        self.service = service
        self.started = time.monotonic()
        self.metrics: dict[str, dict[str, Any]] = {}
        self.recent: deque[dict[str, Any]] = deque(maxlen=32)

    @contextmanager
    def span(
        self, operation: str, *, request_id: str | None = None, **identifiers: str
    ) -> Iterator[dict[str, Any]]:
        # Call sites supply fixed operation names and typed UUID identifiers only.
        result: dict[str, Any] = {"outcome": "ok"}
        start = time.monotonic()
        try:
            yield result
        except BaseException:
            if result["outcome"] == "ok":
                result["outcome"] = "interrupted"
            raise
        finally:
            self.record(
                operation,
                str(result["outcome"]),
                (time.monotonic() - start) * 1000,
                request_id=request_id or request_context.get(),
                **identifiers,
            )

    def record(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        request_id: str | None = None,
        **identifiers: str,
    ) -> None:
        if operation not in self.metrics and len(self.metrics) >= 127:
            operation = "other"
        metric = self.metrics.setdefault(
            operation,
            {"operation": operation, "count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0},
        )
        metric["count"] += 1
        metric["errors"] += outcome not in {"ok", "cancelled"}
        metric["total_ms"] += duration_ms
        metric["max_ms"] = max(metric["max_ms"], duration_ms)
        entry = {
            "service": self.service,
            "operation": operation,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 2),
            "request_id": request_id,
            **identifiers,
        }
        self.recent.append(entry)
        logger.info(json.dumps(entry, sort_keys=True))

    def snapshot(self) -> dict[str, Any]:
        return {
            "scope": "process_local",
            "service": self.service,
            "uptime_seconds": round(time.monotonic() - self.started, 2),
            "metrics": [
                {**m, "total_ms": round(m["total_ms"], 2), "max_ms": round(m["max_ms"], 2)}
                for m in self.metrics.values()
            ],
            "recent": list(self.recent),
        }


class RequestTelemetry:
    def __init__(self, app: ASGIApp, telemetry: Telemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = list(scope.get("headers", []))
        incoming = next((v.decode("latin-1") for k, v in headers if k == b"x-request-id"), None)
        rid = normalize_request_id(incoming)
        # Normalize once, including for mounted apps and invalid caller IDs.
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in headers if k != b"x-request-id"] + [
            (b"x-request-id", rid.encode())
        ]
        token = request_context.set(rid)
        start = time.monotonic()
        status = 500

        async def measured_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, measured_send)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            method = (
                scope["method"]
                if scope["method"] in {"GET", "POST", "PATCH", "DELETE"}
                else "OTHER"
            )
            self.telemetry.record(
                f"{method} {route}",
                "ok" if status < 400 else ("client_error" if status < 500 else "server_error"),
                (time.monotonic() - start) * 1000,
                request_id=rid,
                status=str(status),
            )
            request_context.reset(token)
