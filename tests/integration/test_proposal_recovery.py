"""Rejected model extraction is durable context, never a fabricated qualification update."""

from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from test_staged_qualification import ToolProvider, proposals
from voice_platform_db.models import DomainEvent, LeadScoreHistory, Message, TranscriptSegment
from voice_platform_llm import LLMRouter, ProviderSlot
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.qualified import QualifiedDialogue

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def runtime(system: System, provider: ToolProvider) -> QualifiedDialogue:
    return QualifiedDialogue(
        Backend(system.conversation, "test-token"),
        LLMRouter((ProviderSlot(provider),)),
        system.conversation_id,
        system.call_id,
    )


@pytest.mark.parametrize(
    "kind", ["evidence", "value", "unsupported", "extra", "confirmation", "mixed"]
)
async def test_rejected_extraction_preserves_input_and_continues_without_mutating_lead(
    system: System,
    kind: str,
) -> None:
    await transition_to_greeting(system)
    text = "I want to go to USA and I need H1B visa."
    batch = proposals(text)["proposals"]
    code, source = "invalid_evidence", "lead_validation"
    if kind == "value":
        batch[0]["value"] = "not-an-education-level"
        code = "invalid_value"
    elif kind == "unsupported":
        batch[0]["field_key"] = "destination"
        code, source = "invalid_proposal", "extraction"
    elif kind == "extra":
        batch[0]["score"] = 60
        code, source = "invalid_proposal", "extraction"
    elif kind == "confirmation":
        text = "I have a masters degree."
        batch = proposals(text, resolve=True)["proposals"]
        code = "conflict_not_present"
    elif kind == "mixed":
        text = "I confirm my masters degree. I want to go to USA."
        batch = proposals("I confirm my masters degree.")["proposals"]
        batch += proposals("I want to go to USA.", "advanced", "english_level")["proposals"]
    provider = ToolProvider(batch)
    dialogue = runtime(system, provider)
    before = (
        await dialogue.backend.qualification_context(dialogue.cid, uuid4())
    ).plan.qualification
    events: list[dict[str, object]] = []

    async def notify(event: dict[str, object]) -> None:
        events.append(event)

    dialogue.notify = notify
    tid = uuid4()
    output, _ = await dialogue.reply(text, tid)
    assert output and dialogue.pending is None
    after = (await dialogue.backend.qualification_context(dialogue.cid, uuid4())).plan.qualification
    assert after == before
    async with system.sessions() as db:
        user = await db.get(Message, tid)
        assert user is not None and user.text == text and user.turn_status == "APPLIED"
        metadata: Any = user.message_metadata
        assert metadata["qualification_facts"] == []
        rejection = metadata["proposal_rejection"]
        assert (rejection["source"], rejection["code"]) == (source, code)
        assert "evidence" not in rejection and "value" not in rejection
        assert await db.scalar(select(func.count()).select_from(LeadScoreHistory)) == 0
        assert await db.scalar(select(func.count()).select_from(TranscriptSegment)) == 2
    assert any(event["type"] == "qualification_rejected" for event in events)
    requests = len(provider.requests)
    await dialogue.recover()
    assert len(provider.requests) == requests  # No re-extraction, regeneration or replay.
    provider.proposed = []
    await dialogue.reply("What happens next?", uuid4())
    assert dialogue.pending is None


async def test_lost_rejection_binding_reply_recovers_frozen_empty_batch_without_extraction(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await transition_to_greeting(system)
    text = "I want to go to USA and I need H1B visa."
    provider = ToolProvider(proposals(text)["proposals"])
    dialogue = runtime(system, provider)
    bind = dialogue.backend.bind
    tid = uuid4()

    async def lost(*args: Any, **kwargs: Any) -> Any:
        await bind(*args, **kwargs)
        raise DependencyError(503)

    monkeypatch.setattr(dialogue.backend, "bind", lost)
    with pytest.raises(DependencyError) as failure:
        await dialogue.reply(text, tid)
    assert failure.value.code == "dependency_unavailable"
    assert dialogue.pending is not None
    fresh_provider = ToolProvider([])
    restored = runtime(system, fresh_provider)
    await restored.recover()
    assert restored.pending is None and fresh_provider.requests == []
    await restored.recover()
    async with system.sessions() as db:
        message = await db.get(Message, tid)
        assert message is not None and message.turn_status == "APPLIED"
        metadata: Any = message.message_metadata
        assert metadata["proposal_rejection"]["code"] == "invalid_evidence"
        assert await db.scalar(select(func.count()).select_from(Message)) == 1
        assert await db.scalar(select(func.count()).select_from(LeadScoreHistory)) == 0
        assert (
            await db.scalar(
                select(func.count())
                .select_from(DomainEvent)
                .where(DomainEvent.event_type == "conversation.turn.facts_bound")
            )
            == 1
        )


async def test_malformed_extraction_stream_continues_but_provider_outage_requires_recovery(
    system: System,
) -> None:
    from voice_platform_llm import GenerationRequest, LLMError

    await transition_to_greeting(system)

    class BrokenExtraction(ToolProvider):
        code = "invalid_output"

        async def stream(self, request: GenerationRequest) -> Any:
            if request.tools[0].name == "propose_qualification":
                raise LLMError(
                    cast(Any, self.code), request_id=request.request_id, provider=self.name
                )
            async for event in super().stream(request):
                yield event

    provider = BrokenExtraction([])
    dialogue = runtime(system, provider)
    await dialogue.reply("I want to go to USA.", uuid4())
    assert dialogue.pending is None
    provider.code = "unavailable"
    with pytest.raises(LLMError) as error:
        await dialogue.reply("Can we discuss my options?", uuid4())
    assert error.value.code == "unavailable" and dialogue.pending is not None
