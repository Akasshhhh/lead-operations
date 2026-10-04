"""Conversation Service HTTP boundary."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response
from voice_platform_config import SERVICE_TOKEN_HEADER, service_token_is_valid
from voice_platform_config.settings import database_url_from_env
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
    MessageHistoryEntry,
    MessageHistoryResponse,
    TranscriptHistoryEntry,
    TranscriptHistoryResponse,
    TranscriptQuery,
)
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import Message

from .client import LeadServiceClient, LeadServiceResponseError, LeadServiceUnavailableError
from .service import (
    ActiveCallError,
    ActiveConversationError,
    CallVersionConflictError,
    ConversationNotFoundError,
    ConversationService,
    ConversationVersionConflictError,
    TurnCallNotConnectedError,
    TurnConflictError,
)
from .settings import ConversationSettings


def _conversation_response(conversation: Any) -> ConversationResponse:
    return ConversationResponse.model_validate(conversation)


def _call_response(call: Any) -> CallResponse:
    return CallResponse.model_validate(call)


def _json_object(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _message_response(message: Any) -> MessageHistoryEntry:
    return MessageHistoryEntry(
        id=message.id,
        conversation_id=message.conversation_id,
        call_id=message.call_id,
        speaker=message.speaker,
        text=message.text,
        sequence_number=message.sequence_number,
        provider=message.provider,
        model=message.model,
        message_metadata=_json_object(message.message_metadata),
        turn_status=message.turn_status,
        qualification_error=message.qualification_error,
        redacted=message.redacted_at is not None,
        redacted_at=message.redacted_at,
        redaction_reason=message.redaction_reason,
        content_sha256=message.content_sha256,
        created_at=message.created_at,
    )


def _segment_response(segment: Any) -> TranscriptHistoryEntry:
    return TranscriptHistoryEntry(
        id=segment.id,
        conversation_id=segment.conversation_id,
        call_id=segment.call_id,
        speaker=segment.speaker,
        text=segment.text,
        segment_type=segment.segment_type,
        sequence_number=segment.sequence_number,
        is_final=segment.is_final,
        provider=segment.provider,
        provider_segment_id=segment.provider_segment_id,
        started_at=segment.started_at,
        ended_at=segment.ended_at,
        segment_metadata=_json_object(segment.segment_metadata),
        redacted=segment.redacted_at is not None,
        redacted_at=segment.redacted_at,
        redaction_reason=segment.redaction_reason,
        content_sha256=segment.content_sha256,
        created_at=segment.created_at,
    )


def create_app(
    *,
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    lead_client: LeadServiceClient | None = None,
    app_env: str | None = None,
    service_auth_token: str | None = None,
    lead_service_url: str | None = None,
) -> FastAPI:
    environment = (
        (app_env if app_env is not None else os.getenv("APP_ENV", "local")).strip().lower()
    )
    settings = ConversationSettings(
        environment=environment,
        lead_service_url=lead_service_url
        or os.getenv("LEAD_SERVICE_URL")
        or "http://lead-service:8001",
        service_auth_token=service_auth_token
        if service_auth_token is not None
        else os.getenv("LEAD_SERVICE_AUTH_TOKEN") or None,
        request_timeout_seconds=float(os.getenv("CONVERSATION_SERVICE_TIMEOUT_SECONDS", "5")),
    )

    async def verify_service_auth(
        service_token: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
    ) -> None:
        if not service_token_is_valid(settings.service_auth_token, service_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid service credentials"
            )

    async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
        factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
        async with factory() as session:
            yield session

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if session_factory is not None:
            app.state.session_factory = session_factory
            if lead_client is not None:
                app.state.lead_client = lead_client
                yield
                return
            http_client = httpx.AsyncClient(
                base_url=settings.lead_service_url,
                timeout=httpx.Timeout(settings.request_timeout_seconds),
            )
            app.state.http_client = http_client
            app.state.lead_client = LeadServiceClient(
                http_client,
                service_auth_token=settings.service_auth_token,
                timeout_seconds=settings.request_timeout_seconds,
            )
            try:
                yield
            finally:
                await http_client.aclose()
            return
        engine = create_async_engine(database_url or database_url_from_env())
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        if lead_client is not None:
            app.state.lead_client = lead_client
            try:
                yield
            finally:
                await engine.dispose()
            return
        http_client = httpx.AsyncClient(
            base_url=settings.lead_service_url,
            timeout=httpx.Timeout(settings.request_timeout_seconds),
        )
        app.state.http_client = http_client
        app.state.lead_client = LeadServiceClient(
            http_client,
            service_auth_token=settings.service_auth_token,
            timeout_seconds=settings.request_timeout_seconds,
        )
        try:
            yield
        finally:
            await http_client.aclose()
            await engine.dispose()

    app = FastAPI(title="Conversation Service", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        # Keep the shared request ID contract without exposing arbitrary header lengths.
        if len(request_id) > 120 or any(ord(char) < 33 or ord(char) > 126 for char in request_id):
            request_id = str(uuid4())
        request.state.request_id = request_id
        response = cast(Response, await call_next(request))
        response.headers["X-Request-ID"] = request_id
        return response

    async def database_failure(_: Request, exc: Exception) -> JSONResponse:
        transient = not isinstance(exc, DBAPIError) or exc.connection_invalidated
        return JSONResponse(
            status_code=503 if transient else 500,
            content={"detail": "database unavailable" if transient else "internal server error"},
        )

    for error_type in (DBAPIError, SQLAlchemyError, OSError, TimeoutError):
        app.add_exception_handler(error_type, database_failure)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    @app.exception_handler(ConversationNotFoundError)
    async def not_found(_: Request, exc: ConversationNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    async def conflict(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    for conflict_type in (
        ActiveCallError,
        ActiveConversationError,
        CallVersionConflictError,
        ConversationVersionConflictError,
        TurnConflictError,
        TurnCallNotConnectedError,
    ):
        app.add_exception_handler(conflict_type, conflict)

    @app.exception_handler(LeadServiceUnavailableError)
    async def lead_unavailable(_: Request, __: LeadServiceUnavailableError) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": "lead service unavailable"})

    @app.exception_handler(LeadServiceResponseError)
    async def lead_error(_: Request, exc: LeadServiceResponseError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def client(request: Request) -> LeadServiceClient:
        return cast(LeadServiceClient, request.app.state.lead_client)

    def request_id(request: Request) -> str:
        return cast(str, request.state.request_id)

    @app.get("/health", dependencies=[Depends(verify_service_auth)])
    async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    @app.post(
        "/v1/conversations",
        response_model=ConversationResponse,
        status_code=201,
        dependencies=[Depends(verify_service_auth)],
    )
    async def create_conversation(
        data: ConversationCreate,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> ConversationResponse:
        await lead.get_lead(lead_id=data.lead_id, request_id=request_id(request))
        async with session.begin():
            conversation = await ConversationService(
                session, request_id=request_id(request)
            ).create_conversation(data)
        return _conversation_response(conversation)

    @app.get(
        "/v1/conversations/{conversation_id}",
        response_model=ConversationResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def get_conversation(
        conversation_id: UUID,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> ConversationResponse:
        async with session.begin():
            conversation = await ConversationService(session).get_conversation(conversation_id)
        return _conversation_response(conversation)

    @app.post(
        "/v1/conversations/{conversation_id}/transitions",
        response_model=ConversationResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def transition_conversation(
        conversation_id: UUID,
        data: ConversationTransition,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> ConversationResponse:
        async with session.begin():
            conversation = await ConversationService(
                session, request_id=request_id(request)
            ).transition_conversation(conversation_id, data)
        return _conversation_response(conversation)

    @app.post(
        "/v1/conversations/{conversation_id}/calls",
        response_model=CallResponse,
        status_code=201,
        dependencies=[Depends(verify_service_auth)],
    )
    async def create_call(
        conversation_id: UUID,
        data: CallCreate,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> CallResponse:
        async with session.begin():
            call = await ConversationService(session, request_id=request_id(request)).create_call(
                conversation_id, data
            )
        return _call_response(call)

    @app.post(
        "/v1/conversations/{conversation_id}/calls/{call_id}/transitions",
        response_model=CallResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def transition_call(
        conversation_id: UUID,
        call_id: UUID,
        data: CallTransition,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> CallResponse:
        async with session.begin():
            call = await ConversationService(
                session, request_id=request_id(request)
            ).transition_call(conversation_id, call_id, data)
        return _call_response(call)

    @app.post(
        "/v1/conversations/{conversation_id}/turns",
        response_model=ConversationTurnResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def ingest_turn(
        conversation_id: UUID,
        data: ConversationTurn,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> ConversationTurnResponse:
        conversation_service = ConversationService(session, request_id=request_id(request))
        async with session.begin():
            persisted = await conversation_service.persist_turn(conversation_id, data)
        try:
            qualification = await lead.get_qualification(
                lead_id=persisted.conversation.lead_id, request_id=request_id(request)
            )
            if data.facts and persisted.message.turn_status != "APPLIED":
                from voice_platform_contracts.lead import QualificationUpdate

                qualification = await lead.update_qualification(
                    lead_id=persisted.conversation.lead_id,
                    data=QualificationUpdate(
                        conversation_id=conversation_id,
                        turn_id=data.turn_id,
                        expected_profile_version=qualification.version,
                        facts=data.facts,
                    ),
                    request_id=request_id(request),
                )
        except Exception as exc:
            async with session.begin():
                await conversation_service.mark_turn_pending(
                    conversation_id,
                    data.turn_id,
                    exc,
                    rejected=isinstance(exc, LeadServiceResponseError) and exc.status_code == 422,
                )
            raise
        async with session.begin():
            conversation = await conversation_service.finalize_turn(
                conversation_id, data.turn_id, qualification
            )
            call = await conversation_service.get_call(conversation_id, data.call_id)
            stored_message = await session.get(Message, data.turn_id)
            assert stored_message is not None
        return ConversationTurnResponse(
            conversation=_conversation_response(conversation),
            call=_call_response(call),
            message_id=stored_message.id,
            sequence_number=stored_message.sequence_number,
            turn_status=cast(Any, stored_message.turn_status),
            qualification_error=stored_message.qualification_error,
            qualification=qualification,
            next_action=conversation.next_action,
        )

    @app.get(
        "/v1/conversations/{conversation_id}/live-state",
        response_model=ConversationLiveState,
        dependencies=[Depends(verify_service_auth)],
    )
    async def live_state(
        conversation_id: UUID,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> ConversationLiveState:
        async with session.begin():
            conversation = await ConversationService(session).get_conversation(conversation_id)
            active_call = await ConversationService(session).active_call(conversation_id)
        qualification = await lead.get_qualification(
            lead_id=conversation.lead_id, request_id=request_id(request)
        )
        return ConversationLiveState(
            conversation=_conversation_response(conversation),
            active_call=_call_response(active_call) if active_call is not None else None,
            qualification=qualification,
        )

    @app.get(
        "/v1/conversations/{conversation_id}/history",
        response_model=MessageHistoryResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def message_history(
        conversation_id: UUID,
        query: TranscriptQuery = Depends(),
        session: AsyncSession = Depends(get_session),
    ) -> MessageHistoryResponse:
        async with session.begin():
            page = await ConversationService(session).list_messages(conversation_id, query)
        return MessageHistoryResponse(
            items=[_message_response(item) for item in page.items],
            has_more=page.has_more,
            next_before_sequence=page.next_before_sequence,
            next_after_sequence=page.next_after_sequence,
        )

    @app.get(
        "/v1/conversations/{conversation_id}/transcript",
        response_model=TranscriptHistoryResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def transcript_history(
        conversation_id: UUID,
        query: TranscriptQuery = Depends(),
        session: AsyncSession = Depends(get_session),
    ) -> TranscriptHistoryResponse:
        async with session.begin():
            page = await ConversationService(session).list_transcript_segments(
                conversation_id, query
            )
        return TranscriptHistoryResponse(
            items=[_segment_response(item) for item in page.items],
            has_more=page.has_more,
            next_before_sequence=page.next_before_sequence,
            next_after_sequence=page.next_after_sequence,
        )

    return app


app = create_app()
