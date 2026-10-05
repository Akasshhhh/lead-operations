"""Conversation Service HTTP boundary."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response
from voice_platform_config import SERVICE_TOKEN_HEADER, service_token_is_valid
from voice_platform_config.settings import database_url_from_env
from voice_platform_contracts.conversation import (
    AgentMessageCreate,
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
from voice_platform_contracts.dashboard import (
    CallListResponse,
    ConversationEventsResponse,
    ConversationListResponse,
)
from voice_platform_contracts.lead import LeadResponse, QualificationFact, QualificationUpdate
from voice_platform_contracts.qualification import (
    ProposedFacts,
    QualificationContext,
    StagedTurnCreate,
    ValidateProposals,
)
from voice_platform_contracts.workflow import (
    AgentOutputCheck,
    AgentOutputDecision,
    FollowUpResponse,
    FollowUpTransition,
    HandoffResponse,
    HandoffTransition,
    WorkflowActionCreate,
    WorkflowActionResponse,
    WorkflowSnapshot,
)
from voice_platform_db import create_async_engine, create_session_factory
from voice_platform_db.models import Message

from .client import LeadServiceClient, LeadServiceResponseError, LeadServiceUnavailableError
from .dashboard import DashboardReads
from .policy import WorkflowPolicyError
from .responses import message_response as _message_response
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
from .staged import StagedTurns
from .workflow import WorkflowService


def _conversation_response(conversation: Any) -> ConversationResponse:
    return ConversationResponse.model_validate(conversation)


def _call_response(call: Any) -> CallResponse:
    return CallResponse.model_validate(call)


def _json_object(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


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

    @app.exception_handler(WorkflowPolicyError)
    async def policy_error(_: Request, exc: WorkflowPolicyError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(LeadServiceResponseError)
    async def lead_error(_: Request, exc: LeadServiceResponseError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    def client(request: Request) -> LeadServiceClient:
        return cast(LeadServiceClient, request.app.state.lead_client)

    def request_id(request: Request) -> str:
        return cast(str, request.state.request_id)

    @app.post(
        "/v1/conversations/{conversation_id}/workflow-actions",
        response_model=WorkflowActionResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def workflow_action(
        conversation_id: UUID,
        data: WorkflowActionCreate,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> WorkflowActionResponse:
        async with session.begin():
            result = await WorkflowService(session, request_id=request_id(request)).execute(
                conversation_id, data
            )
        return result

    @app.get(
        "/v1/conversations/{conversation_id}/workflow-actions/{action_id}",
        response_model=WorkflowActionResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def workflow_result(
        conversation_id: UUID,
        action_id: UUID,
        session: AsyncSession = Depends(get_session),
    ) -> WorkflowActionResponse:
        return await WorkflowService(session).result(conversation_id, action_id)

    @app.get(
        "/v1/conversations/{conversation_id}/workflows",
        response_model=WorkflowSnapshot,
        dependencies=[Depends(verify_service_auth)],
    )
    async def workflows(
        conversation_id: UUID,
        session: AsyncSession = Depends(get_session),
    ) -> WorkflowSnapshot:
        return await WorkflowService(session).snapshot(conversation_id)

    @app.post(
        "/v1/conversations/{conversation_id}/output-policy",
        response_model=AgentOutputDecision,
        dependencies=[Depends(verify_service_auth)],
    )
    async def output_policy(
        conversation_id: UUID,
        data: AgentOutputCheck,
        session: AsyncSession = Depends(get_session),
    ) -> AgentOutputDecision:
        async with session.begin():
            return await WorkflowService(session).output_check(conversation_id, data)

    @app.post(
        "/v1/conversations/{conversation_id}/handoffs/{workflow_id}/transitions",
        response_model=HandoffResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def handoff_transition(
        conversation_id: UUID,
        workflow_id: UUID,
        data: HandoffTransition,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> HandoffResponse:
        async with session.begin():
            result = await WorkflowService(session, request_id=request_id(request)).transition(
                conversation_id, workflow_id, data
            )
        return cast(HandoffResponse, result)

    @app.post(
        "/v1/conversations/{conversation_id}/follow-ups/{workflow_id}/transitions",
        response_model=FollowUpResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def follow_up_transition(
        conversation_id: UUID,
        workflow_id: UUID,
        data: FollowUpTransition,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> FollowUpResponse:
        async with session.begin():
            result = await WorkflowService(session, request_id=request_id(request)).transition(
                conversation_id, workflow_id, data
            )
        return cast(FollowUpResponse, result)

    @app.get("/health", dependencies=[Depends(verify_service_auth)])
    async def health(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
        await session.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}

    @app.get(
        "/v1/conversations",
        response_model=ConversationListResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def list_conversations(
        lead_id: UUID,
        active_only: bool = False,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        session: AsyncSession = Depends(get_session),
    ) -> ConversationListResponse:
        return await DashboardReads(session).conversations(lead_id, active_only, limit, offset)

    @app.get(
        "/v1/conversations/{conversation_id}/calls",
        response_model=CallListResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def list_calls(
        conversation_id: UUID,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        session: AsyncSession = Depends(get_session),
    ) -> CallListResponse:
        return await DashboardReads(session).calls(conversation_id, limit, offset)

    @app.get(
        "/v1/conversations/{conversation_id}/events",
        response_model=ConversationEventsResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def list_events(
        conversation_id: UUID,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=10000),
        session: AsyncSession = Depends(get_session),
    ) -> ConversationEventsResponse:
        return await DashboardReads(session).events(conversation_id, limit, offset)

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
        "/v1/conversations/{conversation_id}/qualification-context",
        response_model=QualificationContext,
        dependencies=[Depends(verify_service_auth)],
    )
    async def qualification_context(
        conversation_id: UUID,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> QualificationContext:
        async with session.begin():
            current = await ConversationService(session).get_conversation(conversation_id)
        plan = await lead.plan(current.lead_id, request_id(request))
        try:
            profile = LeadResponse.model_validate(
                await lead.get_lead(lead_id=current.lead_id, request_id=request_id(request))
            )
        except ValidationError:
            raise LeadServiceUnavailableError("invalid lead profile") from None
        if profile.id != current.lead_id:
            raise LeadServiceUnavailableError("invalid lead identity")
        return QualificationContext(lead=profile, plan=plan)

    @app.post(
        "/v1/conversations/{conversation_id}/staged-turns",
        response_model=MessageHistoryEntry,
        dependencies=[Depends(verify_service_auth)],
    )
    async def record_input(
        conversation_id: UUID,
        data: StagedTurnCreate,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> MessageHistoryEntry:
        async with session.begin():
            message = await StagedTurns(session, request_id=request_id(request)).input(
                conversation_id, data
            )
        return _message_response(message)

    @app.post(
        "/v1/conversations/{conversation_id}/staged-turns/{turn_id}/facts",
        response_model=MessageHistoryEntry,
        dependencies=[Depends(verify_service_auth)],
    )
    async def bind_input_facts(
        conversation_id: UUID,
        turn_id: UUID,
        data: ProposedFacts,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> MessageHistoryEntry:
        service = StagedTurns(session, request_id=request_id(request))
        async with session.begin():
            existing = await service.bound(conversation_id, turn_id, data)
            if existing is not None:
                return _message_response(existing)
            message = await service.message(conversation_id, turn_id)
            current = await service.get_conversation(conversation_id)
        # Validation has no side effects. Re-lock and compare the receipt after HTTP.
        validated = await lead.validate(
            current.lead_id,
            ValidateProposals(**data.model_dump(), user_text=message.text),
            request_id(request),
        )
        async with session.begin():
            message = await service.bind(conversation_id, turn_id, data, validated)
        return _message_response(message)

    @app.post(
        "/v1/conversations/{conversation_id}/staged-turns/{turn_id}/apply",
        response_model=ConversationTurnResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def apply_input(
        conversation_id: UUID,
        turn_id: UUID,
        request: Request,
        session: AsyncSession = Depends(get_session),
        lead: LeadServiceClient = Depends(client),
    ) -> ConversationTurnResponse:
        service = StagedTurns(session, request_id=request_id(request))
        async with session.begin():
            message = await service.message(conversation_id, turn_id)
            current = await service.get_conversation(conversation_id)
            if message.turn_status == "FAILED":
                raise TurnConflictError("rejected staged turn")
            metadata = _json_object(message.message_metadata)
            if message.turn_status != "APPLIED" and metadata.get("stage") != "BOUND":
                raise TurnConflictError("bind validated facts before application")
            facts = [
                QualificationFact.model_validate(item)
                for item in metadata.get("qualification_facts", [])
            ]
        try:
            qualification = await lead.get_qualification(
                lead_id=current.lead_id, request_id=request_id(request)
            )
            if message.turn_status != "APPLIED" and facts:
                qualification = await lead.update_qualification(
                    lead_id=current.lead_id,
                    request_id=request_id(request),
                    data=QualificationUpdate(
                        conversation_id=conversation_id,
                        turn_id=turn_id,
                        expected_profile_version=qualification.version,
                        facts=facts,
                    ),
                )
        except Exception as exc:
            async with session.begin():
                await service.mark_turn_pending(
                    conversation_id,
                    turn_id,
                    exc,
                    rejected=isinstance(exc, LeadServiceResponseError) and exc.status_code == 422,
                )
            raise
        async with session.begin():
            current = await service.finalize_turn(conversation_id, turn_id, qualification)
            assert message.call_id is not None
            call = await service.get_call(conversation_id, message.call_id, lock=True)
            message = await service.message(conversation_id, turn_id)
        return ConversationTurnResponse(
            conversation=_conversation_response(current),
            call=_call_response(call),
            message_id=message.id,
            sequence_number=message.sequence_number,
            turn_status=cast(Any, message.turn_status),
            qualification_error=message.qualification_error,
            qualification=qualification,
            next_action=current.next_action,
        )

    @app.post(
        "/v1/conversations/{conversation_id}/agent-messages",
        response_model=MessageHistoryEntry,
        dependencies=[Depends(verify_service_auth)],
    )
    async def record_agent_message(
        conversation_id: UUID,
        data: AgentMessageCreate,
        request: Request,
        session: AsyncSession = Depends(get_session),
    ) -> MessageHistoryEntry:
        async with session.begin():
            message = await ConversationService(
                session, request_id=request_id(request)
            ).persist_agent_message(conversation_id, data)
        return _message_response(message)

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
