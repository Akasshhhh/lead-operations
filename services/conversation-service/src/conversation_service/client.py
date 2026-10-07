"""Internal Lead Service client used by live conversation turns."""

from __future__ import annotations

import asyncio
from typing import Any, cast
from uuid import UUID

import httpx
from pydantic import ValidationError
from voice_platform_config import SERVICE_TOKEN_HEADER
from voice_platform_contracts.lead import QualificationResponse, QualificationUpdate
from voice_platform_contracts.qualification import (
    ProposalRejection,
    QualificationPlan,
    ValidatedFacts,
    ValidateProposals,
)


class LeadServiceUnavailableError(RuntimeError):
    """Raised when qualification cannot be synchronously obtained."""


class LeadServiceResponseError(RuntimeError):
    """Raised for a controlled Lead Service response."""

    def __init__(
        self, status_code: int, detail: str, rejection: ProposalRejection | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.rejection = rejection


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
                rejection = None
                if response.status_code == 422:
                    try:
                        rejection = ProposalRejection.model_validate(response.json()["rejection"])
                    except (ValueError, KeyError, TypeError):
                        pass  # An unclassified rejection must not bypass validation.
                raise LeadServiceResponseError(
                    response.status_code, messages[response.status_code], rejection
                )
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

    async def plan(
        self, lead_id: UUID, request_id: str, call_id: UUID | None = None
    ) -> QualificationPlan:
        path = f"/v1/leads/{lead_id}/qualification/plan"
        if call_id is not None:
            path += f"?call_id={call_id}"
        payload = await self.request("GET", path, request_id=request_id)
        try:
            result = QualificationPlan.model_validate(payload)
        except ValidationError:
            raise LeadServiceUnavailableError("invalid qualification plan") from None
        if result.qualification.lead_id != lead_id:
            raise LeadServiceUnavailableError("invalid qualification plan identity")
        return result

    async def validate(
        self, lead_id: UUID, data: ValidateProposals, request_id: str
    ) -> ValidatedFacts:
        payload = await self.request(
            "POST",
            f"/v1/leads/{lead_id}/qualification/validate",
            request_id=request_id,
            json=data.model_dump(mode="json"),
        )
        try:
            return ValidatedFacts.model_validate(payload)
        except ValidationError:
            raise LeadServiceUnavailableError("invalid fact validation") from None
