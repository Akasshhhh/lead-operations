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
from voice_platform_contracts.conversation import (
    CallCreate,
    CallResponse,
    CallTransition,
    ConversationCreate,
    ConversationLiveState,
    ConversationResponse,
    ConversationTransition,
    ConversationTurn,
    ConversationTurnResponse,
    MessageHistoryResponse,
    TranscriptHistoryResponse,
    TranscriptQuery,
)
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
    ConversationServiceClient,
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
    conversation_client: ConversationServiceClient | None = None,
) -> FastAPI:
    """Create the Gateway with injectable settings and client for tests."""

    gateway_settings = settings or GatewaySettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if client is not None:
            app.state.lead_client = client
            app.state.conversation_client = conversation_client or client
            yield
            return

        timeout = httpx.Timeout(gateway_settings.request_timeout_seconds)
        http_client = httpx.AsyncClient(
            base_url=gateway_settings.lead_service_url,
            timeout=timeout,
        )
        conversation_http_client = httpx.AsyncClient(
            base_url=gateway_settings.conversation_service_url,
            timeout=timeout,
        )
        app.state.http_client = http_client
        app.state.lead_client = LeadServiceClient(
            http_client,
            service_auth_token=gateway_settings.service_auth_token,
            timeout_seconds=gateway_settings.request_timeout_seconds,
        )
        app.state.conversation_client = ConversationServiceClient(
            conversation_http_client,
            service_auth_token=gateway_settings.service_auth_token,
            timeout_seconds=gateway_settings.request_timeout_seconds,
        )
        try:
            yield
        finally:
            await http_client.aclose()
            await conversation_http_client.aclose()

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

    def get_conversation_client(request: Request) -> ConversationServiceClient:
        return cast(ConversationServiceClient, request.app.state.conversation_client)

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

    @app.post("/v1/conversations", response_model=ConversationResponse, status_code=201)
    async def create_conversation(
        request: Request,
        payload: ConversationCreate,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> ConversationResponse:
        result = await conversation_client.create_conversation(
            request_id=request_id(request), payload=payload.model_dump(mode="json")
        )
        return validate_response(ConversationResponse, result)

    @app.get("/v1/conversations/{conversation_id}", response_model=ConversationResponse)
    async def get_conversation(
        request: Request,
        conversation_id: UUID,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> ConversationResponse:
        result = await conversation_client.get_conversation(
            request_id=request_id(request), conversation_id=conversation_id
        )
        return validate_response(ConversationResponse, result)

    @app.post(
        "/v1/conversations/{conversation_id}/transitions", response_model=ConversationResponse
    )
    async def transition_conversation(
        request: Request,
        conversation_id: UUID,
        payload: ConversationTransition,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> ConversationResponse:
        result = await conversation_client.transition_conversation(
            request_id=request_id(request),
            conversation_id=conversation_id,
            payload=payload.model_dump(mode="json"),
        )
        return validate_response(ConversationResponse, result)

    @app.post(
        "/v1/conversations/{conversation_id}/calls", response_model=CallResponse, status_code=201
    )
    async def create_call(
        request: Request,
        conversation_id: UUID,
        payload: CallCreate,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> CallResponse:
        result = await conversation_client.create_call(
            request_id=request_id(request),
            conversation_id=conversation_id,
            payload=payload.model_dump(mode="json"),
        )
        return validate_response(CallResponse, result)

    @app.post(
        "/v1/conversations/{conversation_id}/calls/{call_id}/transitions",
        response_model=CallResponse,
    )
    async def transition_call(
        request: Request,
        conversation_id: UUID,
        call_id: UUID,
        payload: CallTransition,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> CallResponse:
        result = await conversation_client.transition_call(
            request_id=request_id(request),
            conversation_id=conversation_id,
            call_id=call_id,
            payload=payload.model_dump(mode="json"),
        )
        return validate_response(CallResponse, result)

    @app.post("/v1/conversations/{conversation_id}/turns", response_model=ConversationTurnResponse)
    async def ingest_turn(
        request: Request,
        conversation_id: UUID,
        payload: ConversationTurn,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> ConversationTurnResponse:
        result = await conversation_client.ingest_turn(
            request_id=request_id(request),
            conversation_id=conversation_id,
            payload=payload.model_dump(mode="json"),
        )
        return validate_response(ConversationTurnResponse, result)

    @app.get("/v1/conversations/{conversation_id}/live-state", response_model=ConversationLiveState)
    async def live_state(
        request: Request,
        conversation_id: UUID,
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> ConversationLiveState:
        result = await conversation_client.live_state(
            request_id=request_id(request), conversation_id=conversation_id
        )
        return validate_response(ConversationLiveState, result)

    @app.get("/v1/conversations/{conversation_id}/history", response_model=MessageHistoryResponse)
    async def message_history(
        request: Request,
        conversation_id: UUID,
        query: TranscriptQuery = Depends(),
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> MessageHistoryResponse:
        result = await conversation_client.history(
            request_id=request_id(request),
            conversation_id=conversation_id,
            params=query.model_dump(exclude_none=True),
        )
        return validate_response(MessageHistoryResponse, result)

    @app.get(
        "/v1/conversations/{conversation_id}/transcript", response_model=TranscriptHistoryResponse
    )
    async def transcript_history(
        request: Request,
        conversation_id: UUID,
        query: TranscriptQuery = Depends(),
        conversation_client: ConversationServiceClient = Depends(get_conversation_client),
    ) -> TranscriptHistoryResponse:
        result = await conversation_client.transcript(
            request_id=request_id(request),
            conversation_id=conversation_id,
            params=query.model_dump(exclude_none=True),
        )
        return validate_response(TranscriptHistoryResponse, result)

    return app


app = create_app()
