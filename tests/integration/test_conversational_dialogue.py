"""Conversational presentation over unchanged Lead qualification and durable turns."""

import json
from collections.abc import AsyncGenerator
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from voice_platform_contracts.conversation import AgentMessageCreate, MessageHistoryEntry
from voice_platform_contracts.workflow import AgentOutputCheck, AgentOutputDecision
from voice_platform_db.models import LeadScoreHistory, Message, TranscriptSegment
from voice_platform_llm import (
    CompletionEvent,
    GenerationRequest,
    LLMRouter,
    ProviderSlot,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.qualified import QualifiedDialogue

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


class ConversationProvider:
    name = "conversation-fixture"
    model = "natural-v1"

    def __init__(self, draft: str, proposed: list[dict[str, Any]] | None = None) -> None:
        self.draft, self.proposed = draft, proposed or []
        self.requests: list[GenerationRequest] = []

    async def stream(self, request: GenerationRequest) -> AsyncGenerator[StreamEvent, None]:
        self.requests.append(request)
        if request.tools[0].name == "propose_qualification":
            yield ToolCallEvent(
                call=ToolCall(
                    id="facts",
                    name="propose_qualification",
                    arguments=cast(Any, {"proposals": self.proposed}),
                )
            )
            yield CompletionEvent(finish_reason="tool_calls")
        else:
            yield TextDelta(text=self.draft)
            yield CompletionEvent(finish_reason="stop")


def dialogue(system: System, provider: ConversationProvider) -> QualifiedDialogue:
    return QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )


def fact(text: str, field: str, value: Any, *, resolve: bool = False) -> list[dict[str, Any]]:
    return [{"field_key": field, "value": value, "evidence": text, "resolve_conflict": resolve}]


@pytest.mark.parametrize(
    "text,draft",
    [
        (
            "What is the next step?",
            "We'll collect the remaining details so a consultant can review them. "
            "We can take that at your pace.",
        ),
        (
            "Is my voice audible?",
            "Your words are coming through. What would you like help with today?",
        ),
        (
            "I'm not sure.",
            "That's okay. I mean the latest qualification you've completed. "
            "Was that school, a diploma, or a degree?",
        ),
        (
            "My main concern is the cost.",
            "It's reasonable to want clarity on costs. We can take this one step at a time. "
            "Could you tell me a little about your education first?",
        ),
        (
            "I'm currently working in Bangalore.",
            "Got it, you're working in Bangalore. "
            "What qualification did you complete before starting work?",
        ),
        (
            "I've been thinking about moving for a while.",
            "It sounds like you've been considering this for some time. "
            "What is the highest qualification you've completed?",
        ),
    ],
)
async def test_contextual_reply_is_spoken_and_persisted_without_inventing_facts(
    system: System,
    text: str,
    draft: str,
) -> None:
    await transition_to_greeting(system)
    provider = ConversationProvider(draft)
    runtime = dialogue(system, provider)
    output, mid = await runtime.reply(text, uuid4())
    assert output == draft and output.count("?") <= 1
    request = provider.requests[-1]
    assert "live browser voice call" in request.context.system_instruction
    assert "microphone quality" in request.context.system_instruction
    assert any(m.role == "user" and m.content == text for m in request.context.messages)
    plan = json.loads(request.context.messages[-1].content.split(": ", 1)[1])["plan"]
    assert plan["next_field"] == "education_level" and plan["qualification"]["answers"] == []
    async with system.sessions() as db:
        message = await db.get(Message, mid)
        assert message is not None and message.text == draft
        assert message.provider == provider.name and message.model == provider.model
        assert message.turn_status == "APPLIED"
        segment = await db.scalar(
            select(TranscriptSegment).where(
                TranscriptSegment.conversation_id == system.conversation_id,
                TranscriptSegment.provider_segment_id == str(mid),
            )
        )
        assert segment is not None and segment.text == draft


async def test_prior_confirmed_facts_and_latest_turn_guide_followup_and_complete_profile(
    system: System,
) -> None:
    await transition_to_greeting(system)
    steps = [
        (
            "I confirm my masters degree.",
            "education_level",
            "masters",
            "Thanks, that helps. How many years have you been working?",
            "years_experience",
        ),
        (
            "I confirm 3 years of experience.",
            "years_experience",
            3,
            "Three years in work gives me some context. How would you describe your English?",
            "english_level",
        ),
        (
            "I confirm my English is advanced.",
            "english_level",
            "advanced",
            "Got it. Do you already have a job offer?",
            "has_job_offer",
        ),
        (
            "I confirm I have no job offer.",
            "has_job_offer",
            False,
            "You're still exploring work options. Is your budget for the process ready?",
            "budget_ready",
        ),
        (
            "I confirm my budget is not ready.",
            "budget_ready",
            False,
            "We can take the financial planning step by step. How urgent is your plan?",
            "urgency",
        ),
        (
            "I confirm my urgency is high.",
            "urgency",
            "high",
            "Thanks, that gives me a clearer picture of your plans.",
            None,
        ),
    ]
    runtime = dialogue(system, ConversationProvider("unused"))
    for index, (text, field, value, draft, next_field) in enumerate(steps):
        provider = ConversationProvider(draft, fact(text, field, value))
        runtime.llm = LLMRouter((ProviderSlot(provider),))
        output, _ = await runtime.reply(text, uuid4())
        assert output == draft and output.count("?") <= 1
        request = provider.requests[-1]
        plan = json.loads(request.context.messages[-1].content.split(": ", 1)[1])["plan"]
        assert plan["next_field"] == next_field
        assert plan["qualification"]["score"]["score"] == (index + 1) * 10
        assert all(
            answer["answer_status"] == "CONFIRMED" for answer in plan["qualification"]["answers"]
        )
        if index == 1:
            assert any(m.content == steps[0][0] for m in request.context.messages)
            assert "education" not in output.lower()
        count = len(provider.requests)
        await runtime.recover()
        assert len(provider.requests) == count  # no generation or audio replay
    async with system.sessions() as db:
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.conversation_id == system.conversation_id)
            )
            == 12
        )
        assert (
            await db.scalar(
                select(func.count())
                .select_from(LeadScoreHistory)
                .where(LeadScoreHistory.lead_id == system.lead_id)
            )
            == 6
        )


@pytest.mark.parametrize(
    "draft",
    [
        "Can I treat your masters degree as confirmed?",
        "What is your education? How many years have you worked?",
        "You are guaranteed visa approval. What is your education?",
    ],
)
async def test_unsuitable_or_unsafe_draft_uses_lead_fallback_without_confirming(
    system: System,
    draft: str,
) -> None:
    await transition_to_greeting(system)
    text = "I have a masters degree."
    provider = ConversationProvider(draft, fact(text, "education_level", "masters"))
    runtime = dialogue(system, provider)
    output, mid = await runtime.reply(text, uuid4())
    context = await runtime.backend.qualification_context(runtime.cid, uuid4())
    assert output == context.plan.next_question and "I confirm" in output
    assert context.plan.qualification.answers[0].answer_status == "PROVISIONAL"
    assert (
        context.plan.qualification.score is not None and context.plan.qualification.score.score == 0
    )
    async with system.sessions() as db:
        row = await db.get(Message, mid)
        assert row is not None and row.model == "qualification-policy-v1"


@pytest.mark.parametrize(
    "draft",
    [
        "You are guaranteed visa approval. What is your education?",
        "Next I will call get_missing_fields.",
        'I will call {"name": "request_workflow_action", "arguments": {}}.',
    ],
)
async def test_existing_safety_policy_reviews_model_speech_even_with_missing_fields(
    system: System,
    draft: str,
) -> None:
    await transition_to_greeting(system)
    runtime = dialogue(system, ConversationProvider(draft))
    output, mid = await runtime.reply("What is the next step?", uuid4())
    context = await runtime.backend.qualification_context(runtime.cid, uuid4())
    assert output == context.plan.next_question and "guaranteed" not in output
    async with system.sessions() as db:
        message = await db.get(Message, mid)
        assert message is not None and message.text == output
        assert message.model == "qualification-policy-v1"


async def test_warm_confirmation_retains_exact_protocol_and_conflicts_still_take_priority(
    system: System,
) -> None:
    await transition_to_greeting(system)
    runtime = dialogue(system, ConversationProvider("unused"))
    text = "I have a masters degree."
    provider = ConversationProvider(
        "Thanks for sharing. Please confirm: say 'I confirm my education level is masters', "
        "or tell me the correct value.",
        fact(text, "education_level", "masters"),
    )
    runtime.llm = LLMRouter((ProviderSlot(provider),))
    output, _ = await runtime.reply(text, uuid4())
    assert output == provider.draft
    text = "I confirm my masters degree."
    runtime.llm = LLMRouter(
        (
            ProviderSlot(
                ConversationProvider(
                    "Thanks. How long have you worked?", fact(text, "education_level", "masters")
                )
            ),
        )
    )
    await runtime.reply(text, uuid4())
    text = "I confirm my bachelors degree."
    runtime.llm = LLMRouter(
        (
            ProviderSlot(
                ConversationProvider(
                    "Let's just use bachelors. How long have you worked?",
                    fact(text, "education_level", "bachelors"),
                )
            ),
        )
    )
    output, _ = await runtime.reply(text, uuid4())
    context = await runtime.backend.qualification_context(runtime.cid, uuid4())
    assert output == context.plan.next_question and "answers conflict" in output
    assert context.plan.contradictory_fields == ["education_level"]


async def test_ambiguous_agent_write_recovers_original_conversational_text_once(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await transition_to_greeting(system)
    provider = ConversationProvider(
        "We can take it slowly. What qualification did you complete most recently?"
    )
    runtime = dialogue(system, provider)
    agent = runtime.backend.agent
    attempts = 0

    async def lose_receipt(cid: UUID, data: AgentMessageCreate, rid: UUID) -> MessageHistoryEntry:
        nonlocal attempts
        result = await agent(cid, data, rid)
        attempts += 1
        if attempts == 1:
            raise DependencyError()
        return result

    monkeypatch.setattr(runtime.backend, "agent", lose_receipt)
    with pytest.raises(DependencyError):
        await runtime.reply("I'm not sure.", uuid4())
    count = len(provider.requests)
    await runtime.recover()
    assert runtime.pending is None and len(provider.requests) == count
    history = await runtime.backend.history(runtime.cid, uuid4())
    assert len(history.items) == 2 and history.items[-1].text == provider.draft


async def test_policy_outage_never_persists_unreviewed_model_speech(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await transition_to_greeting(system)
    provider = ConversationProvider("We can take it slowly. What qualification did you complete?")
    runtime = dialogue(system, provider)

    async def unavailable(cid: UUID, data: AgentOutputCheck, rid: UUID) -> AgentOutputDecision:
        assert data.text == provider.draft and data.call_id == system.call_id
        raise DependencyError()

    monkeypatch.setattr(runtime.backend, "output_policy", unavailable)
    with pytest.raises(DependencyError):
        await runtime.reply("I'm not sure.", uuid4())
    history = await runtime.backend.history(runtime.cid, uuid4())
    assert len(history.items) == 1
    assert history.items[0].speaker == "USER" and history.items[0].turn_status == "APPLIED"


async def test_speech_onset_does_not_abandon_inflight_staged_qualification(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from voice_platform_runtime.dialogue import Dialogue
    from voice_platform_runtime.processor import VoiceProcessor
    from voice_platform_speech import (
        MockSTTProvider,
        MockTTSProvider,
        SpeechSlot,
        STTRouter,
        TTSRouter,
    )

    await transition_to_greeting(system)
    text = "I confirm my education is masters."
    runtime = dialogue(
        system,
        ConversationProvider(
            "Thanks. How long have you been working?", fact(text, "education_level", "masters")
        ),
    )
    entered, release = asyncio.Event(), asyncio.Event()
    bind = runtime.backend.bind
    events: list[dict[str, object]] = []
    frames: list[Any] = []

    async def slow_bind(*args: Any, **kwargs: Any) -> MessageHistoryEntry:
        entered.set()
        await release.wait()
        return await bind(*args, **kwargs)

    async def notify(event: dict[str, object]) -> None:
        events.append(event)

    async def push(frame: Any, *args: Any, **kwargs: Any) -> None:
        frames.append(frame)

    monkeypatch.setattr(runtime.backend, "bind", slow_bind)
    processor = VoiceProcessor(
        cast(Dialogue, runtime),
        STTRouter((SpeechSlot(MockSTTProvider()),)),
        TTSRouter((SpeechSlot(MockTTSProvider()),)),
        notify,
    )
    monkeypatch.setattr(processor, "push_frame", push)
    processor.ready = True
    task = asyncio.create_task(processor._respond(lambda: runtime.reply(text, uuid4())))
    processor.response_task = task
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert runtime.pending is not None
        await processor.begin_utterance()
        assert not task.done() and frames == [] and events == []
        release.set()
        await asyncio.wait_for(task, 5)
        assert runtime.pending is None
        assert not any(event["type"] == "error" for event in events)
        history = await runtime.backend.history(runtime.cid, uuid4())
        assert len(history.items) == 2
        assert all(item.turn_status == "APPLIED" for item in history.items)
        context = await runtime.backend.qualification_context(runtime.cid, uuid4())
        assert context.plan.qualification.score is not None
        assert context.plan.qualification.score.score == 10
    finally:
        release.set()
        await processor.halt()
