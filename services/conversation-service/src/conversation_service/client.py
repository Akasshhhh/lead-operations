"""Internal Lead Service client used by live conversation turns."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import UUID

import httpx
from pydantic import ValidationError
from voice_platform_config import SERVICE_TOKEN_HEADER
from voice_platform_contracts.lead import QualificationResponse, QualificationUpdate


class LeadServiceUnavailableError(RuntimeError):
    """Raised when qualification cannot be synchronously obtained."""


class LeadServiceResponseError(RuntimeError):
    """Raised for a controlled Lead Service response."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class LeadServiceClient:
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
        self, method: str, path: str, *, request_id: str, json: object | None = None
    ) -> Any:
        headers = {"X-Request-ID": request_id}
        if self.service_auth_token:
            headers[SERVICE_TOKEN_HEADER] = self.service_auth_token
        try:
            async with asyncio.timeout(self.timeout_seconds):
                response = await self.client.request(method, path, headers=headers, json=json)
        except (httpx.RequestError, TimeoutError) as exc:
            raise LeadServiceUnavailableError("lead service unavailable") from exc
        if response.is_error:
            messages = {
                404: "lead not found",
                409: "qualification conflict",
                422: "invalid qualification update",
            }
            if response.status_code in messages:
                raise LeadServiceResponseError(response.status_code, messages[response.status_code])
            raise LeadServiceUnavailableError("lead service unavailable")
        if response.status_code != 200:
            raise LeadServiceUnavailableError("unexpected lead service response")
        try:
            return response.json()
        except ValueError as exc:
            raise LeadServiceUnavailableError("invalid lead service response") from exc

    async def get_qualification(self, *, lead_id: UUID, request_id: str) -> QualificationResponse:
        payload = await self.request(
            "GET", f"/v1/leads/{lead_id}/qualification", request_id=request_id
        )
        return self._qualification_response(payload, lead_id)

    async def update_qualification(
        self, *, lead_id: UUID, data: QualificationUpdate, request_id: str
    ) -> QualificationResponse:
        payload = await self.request(
            "POST",
            f"/v1/leads/{lead_id}/qualification/updates",
            request_id=request_id,
            json=data.model_dump(mode="json"),
        )
        result = self._qualification_response(payload, lead_id)
        if result.score is None:
            raise LeadServiceUnavailableError(
                "missing authoritative score after qualification update"
            )
        return result

    @staticmethod
    def _qualification_response(payload: object, lead_id: UUID) -> QualificationResponse:
        try:
            result = QualificationResponse.model_validate(payload)
        except ValidationError as exc:
            raise LeadServiceUnavailableError("invalid lead service response") from exc
        if result.lead_id != lead_id:
            raise LeadServiceUnavailableError("unexpected lead in qualification response")
        return result

    async def get_lead(self, *, lead_id: UUID, request_id: str) -> dict[str, Any]:
        payload = await self.request("GET", f"/v1/leads/{lead_id}", request_id=request_id)
        return cast(dict[str, Any], payload)
