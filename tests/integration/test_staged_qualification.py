"""Real PostgreSQL/Lead boundary, staged recovery, and tool-driven qualification."""

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from conversation_service.client import LeadServiceUnavailableError
from conversation_service.service import ConversationService
from sqlalchemy import func, select, update
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from voice_platform_db.models import (
    Conversation,
    DomainEvent,
    LeadScoreHistory,
    Message,
    TranscriptSegment,
)
from voice_platform_llm import (
    CompletionEvent,
    GenerationRequest,
    LLMError,
    LLMRouter,
    MockLLMProvider,
    MockScript,
    ProviderSlot,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.qualified import QualifiedDialogue

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
HEADERS = {"X-Service-Token": "test-token"}


def base(system: System) -> str:
    return f"/v1/conversations/{system.conversation_id}/staged-turns"


async def record(system: System, text: str, version: int = 3) -> dict[str, Any]:
    response = await system.conversation.post(
        base(system),
        headers=HEADERS,
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": version,
            "user_text": text,
        },
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def proposals(
    text: str, value: Any = "masters", field: str = "education_level", resolve: bool = False
) -> dict[str, Any]:
    return {
        "proposals": [
            {"field_key": field, "value": value, "evidence": text, "resolve_conflict": resolve}
        ],
        "provider": "mock",
        "model": "tools-test",
    }


async def apply(system: System, tid: str, payload: dict[str, Any]) -> httpx.Response:
    bound = await system.conversation.post(
        f"{base(system)}/{tid}/facts", headers=HEADERS, json=payload
    )
    assert bound.status_code == 200, bound.text
    return await system.conversation.post(f"{base(system)}/{tid}/apply", headers=HEADERS)


async def test_each_stage_is_concurrent_idempotent_and_legacy_turn_cannot_bypass(
    system: System,
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    tid = str(uuid4())
    payload = {
        "turn_id": tid,
        "call_id": str(system.call_id),
        "expected_version": 3,
        "user_text": text,
    }
    responses = await asyncio.gather(
        *(system.conversation.post(base(system), headers=HEADERS, json=payload) for _ in range(5))
    )
    assert all(r.status_code == 200 for r in responses)
    assert all(r.json()["turn_status"] == "PENDING" for r in responses)
    assert (
        await system.conversation.post(
            base(system), headers=HEADERS, json=payload | {"expected_version": 1}
        )
    ).status_code == 200
    assert (
        await system.conversation.post(
            base(system), headers=HEADERS, json=payload | {"user_text": "changed"}
        )
    ).status_code == 409
    legacy = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/turns", headers=HEADERS, json=payload
    )
    assert legacy.status_code == 409
    assert (
        await system.conversation.post(f"{base(system)}/{tid}/apply", headers=HEADERS)
    ).status_code == 409
    binds = await asyncio.gather(
        *(
            system.conversation.post(
                f"{base(system)}/{tid}/facts", headers=HEADERS, json=proposals(text)
            )
            for _ in range(3)
        )
    )
    assert all(r.status_code == 200 for r in binds)
    assert (
        await system.conversation.post(
            f"{base(system)}/{tid}/facts", headers=HEADERS, json=proposals(text, "bachelors")
        )
    ).status_code == 409
    applied = await asyncio.gather(
        *(
            system.conversation.post(f"{base(system)}/{tid}/apply", headers=HEADERS)
            for _ in range(4)
        )
    )
    assert all(r.status_code == 200 for r in applied), [r.text for r in applied]
    assert all(r.json()["qualification"]["score"]["score"] == 10 for r in applied)
    assert all(r.json()["conversation"]["version"] == 4 for r in applied)
    async with system.sessions() as db:
        for model in (Message, TranscriptSegment):
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.conversation_id == system.conversation_id)
                )
                == 1
            )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.aggregate_id == UUID(tid))
            )
            == 3
        )
        message = await db.get(Message, UUID(tid))
        assert (
            message is not None
            and cast(dict[str, Any], message.message_metadata)["provenance"][0]["confirmation"]
            == "explicit"
        )


@pytest.mark.parametrize("phase", ["validation", "read", "update", "lost_reply", "finalize"])
async def test_staged_dependency_and_partial_success_recovery(
    system: System, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    message = await record(system, text)
    tid = message["id"]
    client = system.conversation_app.state.lead_client
    original_update = client.update_qualification
    original_finalize = ConversationService.finalize_turn

    async def unavailable(*_: Any, **kwargs: Any) -> Any:
        if phase == "lost_reply":
            await original_update(**kwargs)
        raise LeadServiceUnavailableError("private dependency detail")

    async def rollback(self: ConversationService, *args: Any) -> Any:
        await original_finalize(self, *args)
        raise DatabaseTimeoutError("before commit")

    with monkeypatch.context() as patch:
        if phase == "validation":
            patch.setattr(client, "validate", unavailable)
            failed = await system.conversation.post(
                f"{base(system)}/{tid}/facts", headers=HEADERS, json=proposals(text)
            )
        else:
            if phase == "finalize":
                patch.setattr(ConversationService, "finalize_turn", rollback)
            else:
                patch.setattr(
                    client,
                    "get_qualification" if phase == "read" else "update_qualification",
                    unavailable,
                )
            failed = await apply(system, tid, proposals(text))
    assert failed.status_code == 503, failed.text
    assert "score" not in failed.json() and "private" not in failed.text
    async with system.sessions() as db:
        persisted = await db.get(Message, UUID(tid))
        assert (
            persisted is not None and persisted.turn_status == "PENDING" and persisted.text == text
        )
    for _ in range(2):
        recovered = await apply(system, tid, proposals(text))
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["qualification"]["score"]["score"] == 10
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


async def test_provisional_contradictory_resolved_facts_drive_backend_questions(
    system: System,
) -> None:
    await transition_to_greeting(system)
    texts = [
        "I have a masters degree.",
        "I confirm my masters degree.",
        "I confirm my bachelors degree.",
        "I confirm my bachelors degree.",
    ]
    version = 3
    scores = []
    for index, text in enumerate(texts):
        staged = await record(system, text, version)
        response = await apply(
            system,
            staged["id"],
            proposals(text, "masters" if index < 2 else "bachelors", resolve=index == 3),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        version = body["conversation"]["version"]
        scores.append(body["qualification"]["score"]["score"])
        context = await system.conversation.get(
            f"/v1/conversations/{system.conversation_id}/qualification-context", headers=HEADERS
        )
        assert context.status_code == 200, context.text
        plan = context.json()["plan"]
        if index == 0:
            assert plan["next_field"] == "education_level" and "confirm" in plan["next_question"]
        elif index == 2:
            assert plan["contradictory_fields"] == ["education_level"]
            assert "conflict" in plan["next_question"]
    assert scores == [0, 10, 0, 10]


@pytest.mark.parametrize(
    "bad",
    [
        "invented_evidence",
        "wrong_value",
        "score",
        "status",
        "unknown_field",
        "resolution_without_confirmation",
    ],
)
async def test_tool_proposal_rejection_never_mutates_authoritative_data(
    system: System, bad: str
) -> None:
    await transition_to_greeting(system)
    text = "I have a masters degree."
    staged = await record(system, text)
    data = proposals(text)
    proposal = data["proposals"][0]
    if bad == "invented_evidence":
        proposal["evidence"] = "I confirm my masters degree."
    elif bad == "wrong_value":
        proposal["value"] = "bachelors"
    elif bad == "unknown_field":
        proposal["field_key"] = "lead_score"
    elif bad == "resolution_without_confirmation":
        proposal["resolve_conflict"] = True
    else:
        proposal[bad] = 100 if bad == "score" else "CONFIRMED"
    response = await system.conversation.post(
        f"{base(system)}/{staged['id']}/facts", headers=HEADERS, json=data
    )
    assert response.status_code == 422, response.text
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 0
        )
        message = await db.get(Message, UUID(staged["id"]))
        assert message is not None and message.turn_status == "PENDING"
    # A rejected proposal is not a frozen receipt; the original evidence can be re-proposed safely.
    assert (await apply(system, staged["id"], proposals(text))).status_code == 200


async def test_pending_staged_input_protects_retention_and_recovers_in_terminal_state(
    system: System,
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    staged = await record(system, text)
    async with system.sessions.begin() as db:
        await db.execute(
            update(Conversation)
            .where(Conversation.id == system.conversation_id)
            .values(state="FAILED", version=4, completed_at=datetime.now(UTC) - timedelta(days=1))
        )
        result = await ConversationService(db).redact_expired_content(cutoff=datetime.now(UTC))
        assert result.messages_redacted == 0
    response = await apply(system, staged["id"], proposals(text))
    assert response.status_code == 200, response.text
    assert response.json()["conversation"]["state"] == "FAILED"
    async with system.sessions.begin() as db:
        result = await ConversationService(db).redact_expired_content(cutoff=datetime.now(UTC))
        assert result.messages_redacted == 1
    response = await apply(system, staged["id"], proposals(text))
    assert response.status_code == 200, response.text


class ToolProvider:
    name = "mock-tools"
    model = "scenario-v1"

    def __init__(self, proposed: list[dict[str, Any]]) -> None:
        self.proposed = proposed
        self.requests: list[GenerationRequest] = []

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        self.requests.append(request)
        if request.tools[0].name == "propose_qualification":
            yield ToolCallEvent(
                call=ToolCall(
                    id="extract",
                    name="propose_qualification",
                    arguments=cast(Any, {"proposals": self.proposed}),
                )
            )
            yield CompletionEvent(finish_reason="tool_calls")
        elif request.context.messages[-1].role != "tool":
            yield ToolCallEvent(
                call=ToolCall(id="read-current", name="get_missing_fields", arguments={})
            )
            yield CompletionEvent(finish_reason="tool_calls")
        else:
            plan = json.loads(request.context.messages[-1].content)
            yield TextDelta(text="Thanks, that helps. " + (plan["next_question"] or ""))
            yield CompletionEvent(finish_reason="stop")


async def test_runtime_tool_flow_persists_before_extraction_and_emits_live_score(
    system: System,
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    provider = ToolProvider(proposals(text)["proposals"])
    backend = Backend(system.conversation, "test-token")
    dialogue = QualifiedDialogue(
        backend, LLMRouter((ProviderSlot(provider),)), system.conversation_id, system.call_id
    )
    events: list[dict[str, object]] = []

    async def notify(event: dict[str, object]) -> None:
        events.append(event)

    dialogue.notify = notify
    output, _ = await dialogue.reply(text, uuid4())
    assert "experience" in output
    assert len(provider.requests) == 3
    assert provider.requests[0].tools[0].name == "propose_qualification"
    assert events[0]["type"] == "qualification"
    assert cast(dict[str, Any], events[0]["qualification"])["score"]["score"] == 10
    assert dialogue.pending is None
    async with system.sessions() as db:
        rows = list(
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == system.conversation_id)
                .order_by(Message.sequence_number)
            )
        )
        assert [row.speaker for row in rows] == ["USER", "AGENT"]
        assert cast(dict[str, Any], rows[0].message_metadata)["stage"] == "BOUND"


async def test_restart_after_extraction_failure_keeps_input_and_recovers_without_audio(
    system: System,
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider(MockScript(fail_after_events=0))),)),
        system.conversation_id,
        system.call_id,
    )
    with pytest.raises(LLMError):
        await dialogue.reply(text, uuid4())
    assert dialogue.pending is not None
    async with system.sessions() as db:
        message = await db.scalar(
            select(Message).where(Message.conversation_id == system.conversation_id)
        )
        assert message is not None and message.text == text and message.turn_status == "PENDING"
    restarted = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(ToolProvider(proposals(text)["proposals"])),)),
        system.conversation_id,
        system.call_id,
    )
    await restarted.recover()
    assert restarted.pending is None
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


@pytest.mark.parametrize(
    "tool,args",
    [
        ("calculate_lead_score", {"score": 100}),
        ("get_qualification", {"lead_id": str(uuid4())}),
        ("end_call", {}),
        ("schedule_followup", {}),
    ],
)
async def test_unavailable_or_cross_scope_tools_are_rejected(
    system: System, tool: str, args: dict[str, Any]
) -> None:
    from voice_platform_runtime.tools import execute_read

    await transition_to_greeting(system)
    with pytest.raises(DependencyError) as error:
        await execute_read(
            ToolCall(id="unsafe", name=tool, arguments=args),
            Backend(system.conversation, "test-token"),
            system.conversation_id,
            uuid4(),
        )
    assert error.value.status == 422


async def test_complete_profile_scores_live_and_state_advances_only_in_backend(
    system: System,
) -> None:
    await transition_to_greeting(system)
    statements = [
        ("education_level", "masters", "I confirm my masters degree."),
        ("years_experience", 0, "I confirm 0 years of experience."),
        ("english_level", "advanced", "I confirm my English is advanced."),
        ("has_job_offer", False, "I confirm I have no job offer."),
        ("budget_ready", True, "I confirm my budget is ready."),
        ("urgency", "high", "I confirm my urgency is high."),
    ]
    text = " ".join(s[2] for s in statements)
    staged = await record(system, text)
    payload = {
        "provider": "mock",
        "model": "test",
        "proposals": [
            {"field_key": field, "value": value, "evidence": evidence}
            for field, value, evidence in statements
        ],
    }
    result = await apply(system, staged["id"], payload)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["qualification"]["score"]["score"] == 60
    assert body["qualification"]["score"]["classification"] == "HOT"
    assert body["qualification"]["completeness"] == 100
    version = body["conversation"]["version"]
    for state in ("QUALIFICATION", "DECISION"):
        staged = await record(system, "That is all.", version)
        response = await apply(
            system, staged["id"], {"proposals": [], "provider": "mock", "model": "test"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["conversation"]["state"] == state
        version = response.json()["conversation"]["version"]
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


async def test_dynamic_plan_prioritizes_urgent_budget_and_known_answers(system: System) -> None:
    await transition_to_greeting(system)
    text = "I confirm my urgency is high."
    staged = await record(system, text)
    assert (
        await apply(system, staged["id"], proposals(text, "high", "urgency"))
    ).status_code == 200
    response = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/qualification-context", headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["plan"]["next_field"] == "budget_ready"
    assert "urgency" not in response.json()["plan"]["missing_fields"]


async def test_bound_facts_survive_process_restart_without_reextracting(system: System) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    staged = await record(system, text)
    response = await system.conversation.post(
        f"{base(system)}/{staged['id']}/facts", headers=HEADERS, json=proposals(text)
    )
    assert response.status_code == 200
    # An unavailable extractor cannot prevent recovery of an already frozen proposal.
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider(MockScript(fail_after_events=0))),)),
        system.conversation_id,
        system.call_id,
    )
    await dialogue.recover()
    assert dialogue.pending is None
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


async def test_gateway_staged_contract_auth_correlation_and_replay(system: System) -> None:
    from api_gateway.app import create_app
    from api_gateway.client import ConversationServiceClient, LeadServiceClient
    from api_gateway.settings import GatewaySettings

    await transition_to_greeting(system)
    app = create_app(
        settings=GatewaySettings("test", "http://lead", "test-token", 5),
        client=LeadServiceClient(system.lead, service_auth_token="test-token"),
        conversation_client=ConversationServiceClient(
            system.conversation, service_auth_token="test-token"
        ),
    )
    text = "I confirm my masters degree."
    tid = str(uuid4())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://gateway"
        ) as gateway:
            data = {
                "turn_id": tid,
                "call_id": str(system.call_id),
                "expected_version": 3,
                "user_text": text,
            }
            response = await gateway.post(
                base(system), json=data, headers={"X-Request-ID": "staged-gateway-test"}
            )
            assert response.status_code == 200, response.text
            assert response.headers["X-Request-ID"] == "staged-gateway-test"
            assert (
                await gateway.post(f"{base(system)}/{tid}/facts", json=proposals(text))
            ).status_code == 200
            for _ in range(2):
                response = await gateway.post(f"{base(system)}/{tid}/apply")
                assert response.status_code == 200, response.text
                assert response.json()["qualification"]["score"]["score"] == 10
    assert (await system.conversation.post(base(system), json=data)).status_code == 401


@pytest.mark.parametrize("phase", ["input", "binding", "application"])
async def test_runtime_lost_stage_response_reuses_original_identity(
    system: System, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    provider = ToolProvider(proposals(text)["proposals"])
    backend = Backend(system.conversation, "test-token")
    method = {"input": "stage", "binding": "bind", "application": "apply"}[phase]
    original = getattr(backend, method)
    failed_once = False

    async def lost(*args: Any, **kwargs: Any) -> Any:
        nonlocal failed_once
        result = await original(*args, **kwargs)
        if not failed_once:
            failed_once = True
            raise DependencyError()
        return result

    monkeypatch.setattr(backend, method, lost)
    dialogue = QualifiedDialogue(
        backend, LLMRouter((ProviderSlot(provider),)), system.conversation_id, system.call_id
    )
    with pytest.raises(DependencyError):
        await dialogue.reply(text, uuid4())
    assert dialogue.pending is not None
    await dialogue.recover()
    assert dialogue.pending is None
    assert (
        len(provider.requests) == 1
    )  # Never regenerates a frozen proposal or plays audio on recovery.
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 1
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )


async def test_cancelled_extraction_leaves_recoverable_durable_input(system: System) -> None:
    await transition_to_greeting(system)
    started = asyncio.Event()

    class SlowProvider(ToolProvider):
        async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
            # Inspect the database at the actual first downstream provider call.
            async with system.sessions() as db:
                message = await db.scalar(
                    select(Message).where(Message.conversation_id == system.conversation_id)
                )
                assert message is not None and message.turn_status == "PENDING"
            started.set()
            await asyncio.sleep(30)
            async for event in super().stream(request):
                yield event

    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(SlowProvider([])),)),
        system.conversation_id,
        system.call_id,
    )
    task = asyncio.create_task(dialogue.reply("Information please.", uuid4()))
    await asyncio.wait_for(started.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert dialogue.pending is not None
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 0
        )
    dialogue.llm = LLMRouter((ProviderSlot(MockLLMProvider()),))
    await dialogue.recover()
    assert dialogue.pending is None


async def test_pending_staged_turn_blocks_overtaking_output_and_other_turns(system: System) -> None:
    from test_conversation_service import agent_body

    await transition_to_greeting(system)
    await record(system, "Information please.")
    conflict = await system.conversation.post(
        base(system),
        headers=HEADERS,
        json={
            "turn_id": str(uuid4()),
            "call_id": str(system.call_id),
            "expected_version": 3,
            "user_text": "Next input",
        },
    )
    assert conflict.status_code == 409
    conflict = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/agent-messages",
        headers=HEADERS,
        json=agent_body(system, 3),
    )
    assert conflict.status_code == 409


async def test_invalid_extraction_finishes_without_facts_and_next_turn_can_correct(
    system: System,
) -> None:
    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    provider = ToolProvider(proposals(text, "bachelors")["proposals"])
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )
    await dialogue.reply(text, uuid4())
    assert dialogue.proposal is None and dialogue.pending is None
    qualification = await dialogue.backend.qualification_context(dialogue.cid, uuid4())
    assert qualification.plan.qualification.score is None
    provider.proposed = proposals(text)["proposals"]
    await dialogue.reply(text, uuid4())
    assert dialogue.pending is None
    qualification = await dialogue.backend.qualification_context(dialogue.cid, uuid4())
    assert qualification.plan.qualification.score is not None
    assert qualification.plan.qualification.score.score == 10


async def test_repeated_tool_ids_and_unbounded_loops_stop_after_durable_application(
    system: System,
) -> None:
    await transition_to_greeting(system)

    class LoopProvider(ToolProvider):
        async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
            if request.tools[0].name == "propose_qualification":
                async for event in super().stream(request):
                    yield event
            else:
                yield ToolCallEvent(
                    call=ToolCall(id="repeat", name="get_qualification", arguments={})
                )
                yield CompletionEvent(finish_reason="tool_calls")

    provider = LoopProvider([])
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )
    output, _ = await dialogue.reply("Information please.", uuid4())
    assert output == "What is your highest education level?"
    assert dialogue.pending is None
    async with system.sessions() as db:
        message = await db.scalar(
            select(Message).where(Message.conversation_id == system.conversation_id)
        )
        assert message is not None and message.turn_status == "APPLIED"


async def test_runtime_lead_outage_pauses_without_emitting_an_old_score(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    await transition_to_greeting(system)
    first_text = "I confirm my masters degree."
    staged = await record(system, first_text)
    first = await apply(system, staged["id"], proposals(first_text))
    assert first.json()["qualification"]["score"]["score"] == 10
    text = "I confirm my English is advanced."
    provider = ToolProvider(proposals(text, "advanced", "english_level")["proposals"])
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )
    events: list[dict[str, object]] = []

    async def notify(event: dict[str, object]) -> None:
        events.append(event)

    async def unavailable(**_: Any) -> Any:
        raise LeadServiceUnavailableError("dependency unavailable")

    dialogue.notify = notify
    with monkeypatch.context() as patch:
        patch.setattr(
            system.conversation_app.state.lead_client, "update_qualification", unavailable
        )
        with pytest.raises(DependencyError) as error:
            await dialogue.reply(text, uuid4())
    assert error.value.status == 503
    assert dialogue.pending is not None and events == []
    await dialogue.recover()
    assert cast(dict[str, Any], events[0]["qualification"])["score"]["score"] == 20


async def test_definitive_apply_rejection_releases_runtime_for_corrected_input(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    from conversation_service.client import LeadServiceResponseError

    await transition_to_greeting(system)
    text = "I confirm my masters degree."
    provider = ToolProvider(proposals(text)["proposals"])
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )

    async def rejected(**_: Any) -> Any:
        raise LeadServiceResponseError(422, "invalid qualification update")

    tid = uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(system.conversation_app.state.lead_client, "update_qualification", rejected)
        with pytest.raises(DependencyError) as error:
            await dialogue.reply(text, tid)
    assert error.value.status == 422
    assert dialogue.pending is None
    async with system.sessions() as db:
        message = await db.get(Message, tid)
        assert message is not None and message.turn_status == "FAILED"
    # A new identity submits the original assertion after recovery without changing the old receipt.
    await dialogue.reply(text, uuid4())
    assert dialogue.pending is None
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 1
        )
