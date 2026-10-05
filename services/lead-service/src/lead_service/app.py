"""FastAPI application for the Lead Service."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast
from uuid import UUID

from asyncpg import PostgresError  # type: ignore[import-untyped]
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import JsonValue
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import Response
from voice_platform_config import SERVICE_TOKEN_HEADER, service_token_is_valid
from voice_platform_config.observability import RequestTelemetry, Telemetry
from voice_platform_config.service_auth import validate_service_auth
from voice_platform_config.settings import database_url_from_env
from voice_platform_contracts.http import normalize_request_id
from voice_platform_contracts.observability import OperationalSnapshot
from voice_platform_contracts.qualification import (
    QualificationPlan,
    ValidatedFacts,
    ValidateProposals,
)
from voice_platform_db import create_async_engine, create_session_factory

from .qualification import qualification_plan, validate_proposals
from .schemas import (
    HealthResponse,
    LeadCreate,
    LeadListResponse,
    LeadResponse,
    LeadScoreResponse,
    LeadUpdate,
    QualificationAnswerResponse,
    QualificationResponse,
    QualificationUpdate,
)
from .service import (
    DuplicateLeadError,
    LeadNotFoundError,
    LeadService,
    LeadVersionConflictError,
    QualificationIdempotencyConflictError,
    QualificationValidationError,
    QualificationVersionConflictError,
)


def resolve_database_url() -> str:
    """Resolve the explicit database URL or derive one from platform settings."""

    return database_url_from_env()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory.begin() as session:
        yield session


def create_app(
    *,
    database_url: str | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    app_env: str | None = None,
    service_auth_token: str | None = None,
) -> FastAPI:
    """Create the Lead Service application with optional test injection."""

    environment = (
        (app_env if app_env is not None else os.getenv("APP_ENV", "local")).strip().lower()
    )
    configured_auth_token = validate_service_auth(
        environment,
        service_auth_token
        if service_auth_token is not None
        else os.getenv("LEAD_SERVICE_AUTH_TOKEN"),
    )

    async def verify_service_auth(
        service_token: str | None = Header(default=None, alias=SERVICE_TOKEN_HEADER),
    ) -> None:
        if not service_token_is_valid(configured_auth_token, service_token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid service credentials",
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if session_factory is not None:
            app.state.session_factory = session_factory
            yield
            return

        engine = create_async_engine(database_url or resolve_database_url())
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(title="Lead Service", version="0.1.0", lifespan=lifespan)
    telemetry = Telemetry("lead-service")

    @app.get(
        "/v1/observability",
        response_model=OperationalSnapshot,
        dependencies=[Depends(verify_service_auth)],
    )
    async def operational_status() -> OperationalSnapshot:
        return OperationalSnapshot.model_validate(telemetry.snapshot())

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next: Any) -> Response:
        request.state.request_id = normalize_request_id(request.headers.get("X-Request-ID"))
        response = cast(Response, await call_next(request))
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    async def database_failure(_: Request, exc: Exception) -> JSONResponse:
        # Data/constraint/programming errors are server defects, not transient outages.
        transient = not isinstance(exc, DBAPIError) or (
            exc.connection_invalidated
            or (getattr(exc.orig, "sqlstate", "") or "").startswith(("08", "57"))
        )
        return JSONResponse(
            status_code=503 if transient else 500,
            content={"detail": "database unavailable" if transient else "internal server error"},
        )

    for error_type in (DBAPIError, PoolTimeoutError, OSError, TimeoutError, PostgresError):
        app.add_exception_handler(error_type, database_failure)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "invalid request"})

    @app.exception_handler(LeadNotFoundError)
    async def handle_not_found(_: Request, exc: LeadNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})

    @app.exception_handler(DuplicateLeadError)
    async def handle_duplicate(_: Request, exc: DuplicateLeadError) -> JSONResponse:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    @app.exception_handler(LeadVersionConflictError)
    async def handle_version_conflict(_: Request, exc: LeadVersionConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": f"stale lead version: {exc}"},
        )

    @app.exception_handler(QualificationVersionConflictError)
    async def handle_qualification_conflict(
        _: Request, exc: QualificationVersionConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": f"stale qualification version: {exc}"},
        )

    @app.exception_handler(QualificationIdempotencyConflictError)
    async def handle_idempotency_conflict(
        _: Request, exc: QualificationIdempotencyConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT, content={"detail": f"turn already used: {exc}"}
        )

    @app.exception_handler(QualificationValidationError)
    async def handle_qualification_validation(
        _: Request, exc: QualificationValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": f"invalid qualification fact: {exc}"},
        )

    @app.get("/health", response_model=HealthResponse, dependencies=[Depends(verify_service_auth)])
    async def health(
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> HealthResponse:
        try:
            await session.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="database unavailable"
            ) from exc
        return HealthResponse(status="ok", database="ok")

    @app.get(
        "/v1/leads",
        response_model=LeadListResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def list_leads(
        status_filter: str | None = Query(
            default=None, alias="status", max_length=32, pattern=r"^[^\x00]*$"
        ),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=9_223_372_036_854_775_807),
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> LeadListResponse:
        result = await LeadService(session).list_leads(
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        return LeadListResponse(
            items=[LeadResponse.model_validate(item) for item in result.items],
            total=result.total,
            limit=limit,
            offset=offset,
        )

    @app.post(
        "/v1/leads",
        response_model=LeadResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(verify_service_auth)],
    )
    async def create_lead(
        request: Request,
        data: LeadCreate,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> LeadResponse:
        lead = await LeadService(session, request_id=request.state.request_id).create_lead(data)
        return LeadResponse.model_validate(lead)

    @app.get(
        "/v1/leads/{lead_id}",
        response_model=LeadResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def get_lead(
        lead_id: UUID,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> LeadResponse:
        lead = await LeadService(session).get_lead(lead_id)
        return LeadResponse.model_validate(lead)

    @app.patch(
        "/v1/leads/{lead_id}",
        response_model=LeadResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def update_lead(
        request: Request,
        lead_id: UUID,
        data: LeadUpdate,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> LeadResponse:
        lead = await LeadService(session, request_id=request.state.request_id).update_lead(
            lead_id, data
        )
        return LeadResponse.model_validate(lead)

    @app.get(
        "/v1/leads/{lead_id}/qualification",
        response_model=QualificationResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def get_qualification(
        lead_id: UUID,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> QualificationResponse:
        result = await LeadService(session).get_qualification(lead_id)
        return QualificationResponse(
            profile_id=result.profile.id,
            lead_id=result.profile.lead_id,
            status=result.profile.status,
            completeness=result.profile.completeness,
            version=result.profile.version,
            answers=[
                QualificationAnswerResponse.model_validate(answer) for answer in result.answers
            ],
            score=(
                LeadScoreResponse(
                    score=result.score.score,
                    classification=result.score.classification,
                    rule_version=result.score.rule_version,
                    reasons=cast(list[JsonValue], result.score.reasons),
                    calculated_at=result.score.calculated_at,
                )
                if result.score is not None
                else None
            ),
        )

    @app.get(
        "/v1/leads/{lead_id}/qualification/plan",
        response_model=QualificationPlan,
        dependencies=[Depends(verify_service_auth)],
    )
    async def get_plan(
        lead_id: UUID, session: AsyncSession = Depends(get_session, scope="function")
    ) -> QualificationPlan:
        return qualification_plan(await get_qualification(lead_id, session))

    @app.post(
        "/v1/leads/{lead_id}/qualification/validate",
        response_model=ValidatedFacts,
        dependencies=[Depends(verify_service_auth)],
    )
    async def validate_facts(
        lead_id: UUID,
        data: ValidateProposals,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> ValidatedFacts:
        current = await LeadService(session).get_qualification(lead_id)
        answers = {answer.field_key: answer for answer in current.answers}
        for proposal in data.proposals:
            if proposal.resolve_conflict and (
                proposal.field_key not in answers
                or answers[proposal.field_key].answer_status != "CONTRADICTORY"
            ):
                raise QualificationValidationError(proposal.field_key)
        return validate_proposals(data)

    @app.post(
        "/v1/leads/{lead_id}/qualification/updates",
        response_model=QualificationResponse,
        dependencies=[Depends(verify_service_auth)],
    )
    async def update_qualification(
        request: Request,
        lead_id: UUID,
        data: QualificationUpdate,
        session: AsyncSession = Depends(get_session, scope="function"),
    ) -> QualificationResponse:
        result = await LeadService(
            session, request_id=request.state.request_id
        ).apply_qualification_update(lead_id, data)
        return QualificationResponse(
            profile_id=result.profile.id,
            lead_id=result.profile.lead_id,
            status=result.profile.status,
            completeness=result.profile.completeness,
            version=result.profile.version,
            answers=[
                QualificationAnswerResponse.model_validate(answer) for answer in result.answers
            ],
            score=(
                LeadScoreResponse(
                    score=result.score.score,
                    classification=result.score.classification,
                    rule_version=result.score.rule_version,
                    reasons=cast(list[JsonValue], result.score.reasons),
                    calculated_at=result.score.calculated_at,
                )
                if result.score is not None
                else None
            ),
        )

    app.add_middleware(RequestTelemetry, telemetry=telemetry)
    return app


app = create_app()
