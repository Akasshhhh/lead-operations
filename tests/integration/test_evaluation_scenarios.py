"""Multi-turn caller fixtures and independent concurrent qualification sessions."""

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from voice_platform_config.observability import Telemetry
from voice_platform_contracts.qualification import StagedTurnCreate
from voice_platform_db.models import DomainEvent, LeadScoreHistory, Message, TranscriptSegment
from voice_platform_llm import (
    LLMRouter,
    MockLLMProvider,
    MockScript,
    ProviderSlot,
    RouterPolicy,
)
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.diagnostics import FaultCreate, Faults, ObservedBackend
from voice_platform_runtime.qualified import QualifiedDialogue

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
other_system = system


@dataclass(frozen=True)
class Step:
    text: str
    field: str
    value: object
    score: int
    status: str
    next_field: str
    resolve: bool = False


SCENARIOS = {
    "education_correction": (
        Step(
            "I have a masters degree.",
            "education_level",
            "masters",
            0,
            "PROVISIONAL",
            "education_level",
        ),
        Step(
            "I confirm my masters degree.",
            "education_level",
            "masters",
            10,
            "CONFIRMED",
            "years_experience",
        ),
        Step(
            "I confirm my bachelors degree.",
            "education_level",
            "bachelors",
            0,
            "CONTRADICTORY",
            "education_level",
        ),
        Step(
            "I confirm my bachelors degree.",
            "education_level",
            "bachelors",
            10,
            "CONFIRMED",
            "years_experience",
            True,
        ),
    ),
    "urgent_caller_with_false_and_zero": (
        Step("I confirm my urgency is high.", "urgency", "high", 10, "CONFIRMED", "budget_ready"),
        Step(
            "I confirm my budget is not ready.",
            "budget_ready",
            False,
            20,
            "CONFIRMED",
            "has_job_offer",
        ),
        Step(
            "I confirm I have no job offer.",
            "has_job_offer",
            False,
            30,
            "CONFIRMED",
            "education_level",
        ),
        Step(
            "I confirm 0 years of experience.",
            "years_experience",
            0,
            40,
            "CONFIRMED",
            "education_level",
        ),
    ),
}


async def evaluate_step(dialogue: QualifiedDialogue, step: Step) -> None:
    from test_staged_qualification import ToolProvider

    fallback = ToolProvider(
        [
            {
                "field_key": step.field,
                "value": step.value,
                "evidence": step.text,
                "resolve_conflict": step.resolve,
            }
        ]
    )
    # Exercise existing buffered retry/failover inside a real durable runtime turn.
    dialogue.llm = LLMRouter(
        (ProviderSlot(MockLLMProvider(MockScript(fail_after_events=0))), ProviderSlot(fallback)),
        policy=RouterPolicy(
            max_attempts_per_provider=1, failure_threshold=1, retry_delay_seconds=0
        ),
    )
    tid = uuid4()
    output, _ = await dialogue.reply(step.text, tid)
    context = await dialogue.backend.qualification_context(dialogue.cid, uuid4())
    qualification = context.plan.qualification
    assert qualification.score is not None and qualification.score.score == step.score
    assert qualification.score.rule_version == "baseline-v1"
    assert qualification.score.classification == (
        "COLD" if step.score < 20 else "WARM" if step.score < 40 else "HOT"
    )
    answer = next(a for a in qualification.answers if a.field_key == step.field)
    assert answer.answer_status == step.status
    value = answer.conflict_value if step.status == "CONTRADICTORY" else answer.value
    assert value == step.value and type(value) is type(step.value)
    assert context.plan.next_field == step.next_field
    assert context.plan.next_question is not None
    assert output == "Thanks, that helps. " + context.plan.next_question
    assert output.count("?") <= 1
    assert dialogue.llm.health()[0].failover_count == 1
    assert all(r.conversation_id == dialogue.cid for r in fallback.requests)
    # Stale admission replay + simultaneous apply retries use the same durable receipt.
    await dialogue.backend.stage(
        dialogue.cid,
        StagedTurnCreate(
            turn_id=tid, call_id=dialogue.call_id, expected_version=1, user_text=step.text
        ),
        uuid4(),
    )
    replays = await asyncio.gather(
        *(dialogue.backend.apply(dialogue.cid, tid, uuid4()) for _ in range(3))
    )
    assert all(
        r.qualification is not None
        and r.qualification.score is not None
        and r.qualification.score.score == step.score
        for r in replays
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
async def test_multiturn_caller_scenario_with_failover_and_replay(
    system: System, scenario: str
) -> None:
    await transition_to_greeting(system)
    dialogue = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    for step in SCENARIOS[scenario]:
        await evaluate_step(dialogue, step)
    async with system.sessions() as db:
        messages = list(
            await db.scalars(
                select(Message)
                .where(Message.conversation_id == system.conversation_id)
                .order_by(Message.sequence_number)
            )
        )
        assert [m.speaker for m in messages] == ["USER", "AGENT"] * 4
        assert [m.sequence_number for m in messages] == list(range(1, 9))
        assert all(m.turn_status == "APPLIED" for m in messages)
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TranscriptSegment)
                .where(TranscriptSegment.conversation_id == system.conversation_id)
            )
            == 8
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 4
        )
        events = list(
            await db.scalars(
                select(DomainEvent).where(DomainEvent.aggregate_id.in_([m.id for m in messages]))
            )
        )
        assert len(events) == 16
        assert len({(e.aggregate_type, e.aggregate_id, e.aggregate_version) for e in events}) == 16


async def test_two_sessions_isolate_pending_faults_scores_and_recovery(
    system: System,
    other_system: System,
) -> None:
    from test_staged_qualification import ToolProvider

    await asyncio.gather(transition_to_greeting(system), transition_to_greeting(other_system))
    faults = Faults(Telemetry("evaluation"))
    faults.arm(FaultCreate(operation_id=uuid4(), target="dependency", mode="timeout", attempts=1))
    first_text, other_text = "I confirm my masters degree.", "I confirm my bachelors degree."
    dialogues = [
        QualifiedDialogue(
            ObservedBackend(
                Backend(s.conversation, "test-token", timeout=0.2), Telemetry("evaluation"), faults
            )
            if index == 0
            else Backend(s.conversation, "test-token"),
            LLMRouter(
                (
                    ProviderSlot(
                        ToolProvider(
                            [{"field_key": "education_level", "value": value, "evidence": text}]
                        )
                    ),
                )
            ),
            s.conversation_id,
            s.call_id,
        )
        for index, (s, value, text) in enumerate(
            ((system, "masters", first_text), (other_system, "bachelors", other_text))
        )
    ]
    tids = [uuid4(), uuid4()]
    outcomes = await asyncio.gather(
        dialogues[0].reply(first_text, tids[0]),
        dialogues[1].reply(other_text, tids[1]),
        return_exceptions=True,
    )
    assert isinstance(outcomes[0], DependencyError) and isinstance(outcomes[1], tuple)
    async with system.sessions() as db:
        pending = await db.get(Message, tids[0])
        assert pending is not None and pending.turn_status == "PENDING"
    faults.reset()
    # Recover with a fresh runtime instance to exercise process reattachment semantics.
    recovered = QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(MockLLMProvider()),)),
        system.conversation_id,
        system.call_id,
    )
    await recovered.recover()
    await recovered.recover()
    for index, s in enumerate((system, other_system)):
        context = await dialogues[index].backend.qualification_context(s.conversation_id, uuid4())
        assert context.lead.id == s.lead_id
        assert context.plan.qualification.answers[0].value == (
            "masters" if index == 0 else "bachelors"
        )
        async with s.sessions() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(LeadScoreHistory)
                    .where(LeadScoreHistory.lead_id == s.lead_id)
                )
                == 1
            )
            messages = list(
                await db.scalars(
                    select(Message).where(Message.conversation_id == s.conversation_id)
                )
            )
            assert len(messages) == (1 if index == 0 else 2)
            assert all(m.turn_status == "APPLIED" and m.call_id == s.call_id for m in messages)
