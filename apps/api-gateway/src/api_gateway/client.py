"""Internal Lead Service REST client."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
from voice_platform_config import SERVICE_TOKEN_HEADER


class LeadServiceUnavailableError(RuntimeError):
    """Raised when the Lead Service cannot be reached."""


class LeadServiceProtocolError(RuntimeError):
    """Raised for an unexpected status, invalid JSON, or incompatible response."""


class LeadServiceResponseError(RuntimeError):
    """Raised when the Lead Service returns a controlled non-success response."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class LeadServiceClient:
    """Typed-enough transport wrapper that keeps provider/service HTTP out of routes."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        service_auth_token: str | None,
        timeout_seconds: float = 5,
    ) -> None:
        self.client = client
        self.service_auth_token = service_auth_token
        self.timeout_seconds = timeout_seconds

    async def request(
        self,
        method: str,
        path: str,
        *,
        request_id: str,
        params: Mapping[str, str | int | float | bool | None] | None = None,
        json: object | None = None,
    ) -> Any:
        headers = {"X-Request-ID": request_id}
        if self.service_auth_token:
            headers[SERVICE_TOKEN_HEADER] = self.service_auth_token

        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.request(
                    method,
                    path,
                    headers=headers,
                    params=params,
                    json=json,
                )
        except (httpx.RequestError, TimeoutError) as exc:
            raise LeadServiceUnavailableError("lead service unavailable") from exc

        if response.is_error:
            messages = {
                404: "lead not found",
                409: "lead conflict",
                422: "invalid request",
                503: "lead service unavailable",
            }
            if response.status_code not in messages:
                raise LeadServiceProtocolError("lead service request failed")
            raise LeadServiceResponseError(response.status_code, messages[response.status_code])

        if response.status_code != (201 if method == "POST" else 200):
            raise LeadServiceProtocolError("unexpected lead service response status")
        try:
            return response.json()
        except ValueError as exc:
            raise LeadServiceProtocolError("invalid lead service response") from exc

    async def health(self, *, request_id: str) -> Any:
        return await self.request("GET", "/health", request_id=request_id)

    async def list_leads(
        self,
        *,
        request_id: str,
        status: str | None,
        limit: int,
        offset: int,
    ) -> Any:
        params: dict[str, str | int | float | bool | None] = {
            "limit": limit,
            "offset": offset,
        }
        if status is not None:
            params["status"] = status
        return await self.request("GET", "/v1/leads", request_id=request_id, params=params)

    async def create_lead(self, *, request_id: str, payload: object) -> Any:
        return await self.request("POST", "/v1/leads", request_id=request_id, json=payload)

    async def get_lead(self, *, request_id: str, lead_id: UUID) -> Any:
        return await self.request("GET", f"/v1/leads/{lead_id}", request_id=request_id)

    async def update_lead(self, *, request_id: str, lead_id: UUID, payload: object) -> Any:
        return await self.request(
            "PATCH", f"/v1/leads/{lead_id}", request_id=request_id, json=payload
        )

    async def get_qualification(self, *, request_id: str, lead_id: UUID) -> Any:
        return await self.request(
            "GET",
            f"/v1/leads/{lead_id}/qualification",
            request_id=request_id,
        )
