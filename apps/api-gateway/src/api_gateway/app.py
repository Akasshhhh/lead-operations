"""Public API Gateway application."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException
from starlette.responses import Response
from voice_platform_contracts.http import normalize_request_id
from voice_platform_contracts.lead import (
    HealthResponse,
    LeadCreate,
    LeadListResponse,
    LeadResponse,
    LeadUpdate,
    QualificationResponse,
)

from .client import (
    LeadServiceClient,
    LeadServiceProtocolError,
    LeadServiceResponseError,
    LeadServiceUnavailableError,
)
from .settings import GatewaySettings


def error_payload(code: str, message: str, request_id: str) -> dict[str, object]:
    return {
        "error": {"code": code, "message": message, "request_id": request_id},
        "request_id": request_id,
    }


def validate_response[T: BaseModel](model: type[T], payload: object) -> T:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise LeadServiceProtocolError("invalid lead service response") from exc


def create_app(
    *,
    settings: GatewaySettings | None = None,
    client: LeadServiceClient | None = None,
) -> FastAPI:
    """Create the Gateway with injectable settings and client for tests."""

    gateway_settings = settings or GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if client is not None:
            app.state.lead_client = client
            yield
            return

        timeout = httpx.Timeout(gateway_settings.request_timeout_seconds)
        http_client = httpx.AsyncClient(
            base_url=gateway_settings.lead_service_url,
            timeout=timeout,
        )
        app.state.http_client = http_client
        app.state.lead_client = LeadServiceClient(
            http_client,
            service_auth_token=gateway_settings.service_auth_token,
            timeout_seconds=gateway_settings.request_timeout_seconds,
        )
        try:
            yield
        finally:
            await http_client.aclose()

    app = FastAPI(title="API Gateway", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Response:
        request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        response = cast(Response, await call_next(request))
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(LeadServiceUnavailableError)
    async def handle_unavailable(request: Request, _: LeadServiceUnavailableError) -> JSONResponse:
        request_id = cast(str, request.state.request_id)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_payload(
                "lead_service_unavailable", "lead service unavailable", request_id
            ),
        )

    @app.exception_handler(LeadServiceResponseError)
    async def handle_downstream_error(
        request: Request, exc: LeadServiceResponseError
    ) -> JSONResponse:
        request_id = cast(str, request.state.request_id)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload("lead_service_error", str(exc.detail), request_id),
        )

    @app.exception_handler(LeadServiceProtocolError)
    async def handle_protocol_error(request: Request, _: LeadServiceProtocolError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content=error_payload(
                "lead_service_invalid_response",
                "invalid lead service response",
                request.state.request_id,
            ),
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            headers=exc.headers,
            content=error_payload("http_error", str(exc.detail), request.state.request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        request_id = cast(str, request.state.request_id)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=error_payload("request_validation_error", "invalid request", request_id),
        )

    def get_client(request: Request) -> LeadServiceClient:
        return cast(LeadServiceClient, request.app.state.lead_client)

    def request_id(request: Request) -> str:
        return cast(str, request.state.request_id)

    @app.get("/health")
    async def health(
        request: Request,
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> dict[str, object]:
        downstream = await lead_client.health(request_id=request_id(request))
        health = validate_response(HealthResponse, downstream)
        if health.status != "ok" or health.database != "ok":
            raise LeadServiceUnavailableError("lead service unavailable")
        return {"status": "ok", "lead_service": health.model_dump()}

    @app.get("/v1/leads", response_model=LeadListResponse)
    async def list_leads(
        request: Request,
        status_filter: str | None = Query(
            default=None, alias="status", max_length=32, pattern=r"^[^\x00]*$"
        ),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=9_223_372_036_854_775_807),
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> LeadListResponse:
        payload = await lead_client.list_leads(
            request_id=request_id(request),
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        return validate_response(LeadListResponse, payload)

    @app.post("/v1/leads", response_model=LeadResponse, status_code=status.HTTP_201_CREATED)
    async def create_lead(
        request: Request,
        payload: LeadCreate,
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> LeadResponse:
        result = await lead_client.create_lead(
            request_id=request_id(request),
            payload=payload.model_dump(mode="json"),
        )
        return validate_response(LeadResponse, result)

    @app.get("/v1/leads/{lead_id}", response_model=LeadResponse)
    async def get_lead(
        request: Request,
        lead_id: UUID,
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> LeadResponse:
        result = await lead_client.get_lead(request_id=request_id(request), lead_id=lead_id)
        return validate_response(LeadResponse, result)

    @app.patch("/v1/leads/{lead_id}", response_model=LeadResponse)
    async def update_lead(
        request: Request,
        lead_id: UUID,
        payload: LeadUpdate,
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> LeadResponse:
        result = await lead_client.update_lead(
            request_id=request_id(request),
            lead_id=lead_id,
            payload=payload.model_dump(mode="json", exclude_unset=True),
        )
        return validate_response(LeadResponse, result)

    @app.get("/v1/leads/{lead_id}/qualification", response_model=QualificationResponse)
    async def get_qualification(
        request: Request,
        lead_id: UUID,
        lead_client: LeadServiceClient = Depends(get_client),
    ) -> QualificationResponse:
        result = await lead_client.get_qualification(
            request_id=request_id(request), lead_id=lead_id
        )
        return validate_response(QualificationResponse, result)

    return app


app = create_app()
