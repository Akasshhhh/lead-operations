"""Caller-confirmed current truth across calls, without losing pending/history semantics."""

import asyncio
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from conversation_service.client import LeadServiceUnavailableError
from conversation_service.service import ConversationService
from sqlalchemy import select
from sqlalchemy.exc import TimeoutError as DatabaseTimeoutError
from test_conversation_service import System, transition_to_greeting
from test_conversation_service import system as system
from test_staged_qualification import HEADERS, apply, base, proposals, record
from voice_platform_db.models import LeadScoreHistory, Message

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def turn(
    system: System, field: str, value: Any, text: str, resolve: bool = False
) -> dict[str, Any]:
    current = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}", headers=HEADERS
    )
    message = await record(system, text, current.json()["version"])
    result = await apply(system, message["id"], proposals(text, value, field, resolve))
    assert result.status_code == 200, result.text
    return cast(dict[str, Any], result.json())


async def later_call(system: System) -> UUID:
    previous = system.call_id
    ended = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls/{previous}/transitions",
        headers=HEADERS,
        json={"target_status": "ENDED", "expected_version": 3},
    )
    assert ended.status_code == 200, ended.text
    created = await system.conversation.post(
        f"/v1/conversations/{system.conversation_id}/calls",
        headers=HEADERS,
        json={"runtime_instance_id": "next-call"},
    )
    assert created.status_code == 201, created.text
    system.call_id = UUID(created.json()["id"])
    for status, version in (("CONNECTING", 1), ("CONNECTED", 2)):
        connected = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions",
            headers=HEADERS,
            json={"target_status": status, "expected_version": version},
        )
        assert connected.status_code == 200, connected.text
    return previous


CASES = [
    ("education_level", "masters", "bachelors", "my masters degree", "my bachelors degree", 10),
    ("years_experience", 3, 4, "3 years of experience", "4 years of experience", 10),
    (
        "english_level",
        "intermediate",
        "advanced",
        "my English is intermediate",
        "my English is advanced",
        10,
    ),
    ("has_job_offer", False, True, "I have no job offer", "I have a job offer", 10),
    ("budget_ready", True, False, "my budget is ready", "my budget is not ready", 10),
    ("urgency", "high", "low", "my urgency is high", "my urgency is low", 10),
    (
        "target_country",
        "united states",
        "canada",
        "my target country is USA",
        "my target country is Canada",
        0,
    ),
    ("visa_type", "h-1b", "f-1", "my visa type is H1B", "my visa type is F1", 0),
]


@pytest.mark.parametrize("field,old,new,old_words,new_words,score", CASES)
async def test_later_call_candidate_then_confirmed_promotion_for_every_field(
    system: System,
    field: str,
    old: Any,
    new: Any,
    old_words: str,
    new_words: str,
    score: int,
) -> None:
    await transition_to_greeting(system)
    initial = await turn(system, field, old, "I confirm " + old_words + ".")
    assert initial["qualification"]["score"]["score"] == score
    first_call = await later_call(system)
    candidate = await turn(system, field, new, new_words + ".")
    answer = candidate["qualification"]["answers"][0]
    assert (answer["value"], answer["answer_status"], answer["call_id"]) == (
        old,
        "CONFIRMED",
        str(first_call),
    )
    assert answer["pending_value"] == new and answer["pending_call_id"] == str(system.call_id)
    assert candidate["qualification"]["score"]["score"] == score
    context = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/qualification-context",
        headers=HEADERS,
    )
    plan = context.json()["plan"]
    assert field in plan["provisional_fields"] and field not in plan["contradictory_fields"]
    assert "I confirm" in plan["next_question"]
    if not isinstance(new, bool):
        assert str(new) in plan["next_question"]
    # A later call needs confirmation, but does not require resolve_conflict.
    promoted = await turn(
        system, field, new, "I confirm " + new_words + ".", resolve=field == "target_country"
    )
    answer = promoted["qualification"]["answers"][0]
    assert (answer["value"], answer["answer_status"], answer["call_id"]) == (
        new,
        "CONFIRMED",
        str(system.call_id),
    )
    assert answer["pending_value"] is None and answer["conflict_value"] is None
    assert promoted["qualification"]["score"]["score"] == score
    profile = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    if field == "target_country":
        assert profile["target_country"] == new
    tid = promoted["message_id"]
    replay = await system.conversation.post(f"{base(system)}/{tid}/apply", headers=HEADERS)
    assert replay.status_code == 200
    async with system.sessions() as db:
        history = list(
            (
                await db.scalars(
                    select(LeadScoreHistory)
                    .where(
                        LeadScoreHistory.lead_id == system.lead_id,
                    )
                    .order_by(LeadScoreHistory.created_at)
                )
            ).all()
        )
        assert len(history) == 3
        change = cast(list[dict[str, Any]], history[-1].answer_changes)[0]
        assert change["previous"]["value"] == old
        assert change["previous"]["pending_value"] == new
        assert change["current"]["value"] == new
        assert change["call_id"] == str(system.call_id) and change["turn_id"] == tid
        message = await db.get(Message, UUID(tid))
        assert message is not None
        assert cast(dict[str, Any], message.message_metadata)["provenance"][0]["call_id"] == str(
            system.call_id
        )


async def test_same_call_reconnection_preserves_conflict_protocol_and_same_value_stays_confirmed(
    system: System,
) -> None:
    await transition_to_greeting(system)
    await turn(system, "education_level", "masters", "I confirm my masters degree.")
    for status, version in (("RECONNECTING", 3), ("CONNECTED", 4)):
        response = await system.conversation.post(
            f"/v1/conversations/{system.conversation_id}/calls/{system.call_id}/transitions",
            headers=HEADERS,
            json={"target_status": status, "expected_version": version},
        )
        assert response.status_code == 200, response.text
    contradiction = await turn(
        system, "education_level", "bachelors", "I confirm my bachelors degree."
    )
    answer = contradiction["qualification"]["answers"][0]
    assert answer["value"] == "masters" and answer["conflict_value"] == "bachelors"
    assert (
        answer["answer_status"] == "CONTRADICTORY"
        and contradiction["qualification"]["score"]["score"] == 0
    )
    corrected = await turn(
        system, "education_level", "bachelors", "I confirm my bachelors degree.", True
    )
    assert corrected["qualification"]["score"]["score"] == 10
    repeated = await turn(system, "education_level", "bachelors", "I have a bachelors degree.")
    assert repeated["qualification"]["answers"][0]["answer_status"] == "CONFIRMED"
    assert repeated["qualification"]["score"]["score"] == 10


@pytest.mark.parametrize("phase", ["lost_reply", "rollback"])
async def test_replacement_recovery_is_atomic_and_does_not_duplicate_history(
    system: System,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    await transition_to_greeting(system)
    await turn(system, "target_country", "united states", "I confirm my target country is USA.")
    await later_call(system)
    current = (
        await system.conversation.get(
            f"/v1/conversations/{system.conversation_id}", headers=HEADERS
        )
    ).json()
    text = "I confirm my target country is Canada."
    message = await record(system, text, current["version"])
    payload = proposals(text, "canada", "target_country")
    client = system.conversation_app.state.lead_client
    original_update = client.update_qualification
    original_finalize = ConversationService.finalize_turn

    async def lost(**kwargs: Any) -> Any:
        await original_update(**kwargs)
        raise LeadServiceUnavailableError("lost acknowledgement")

    async def rollback(self: ConversationService, *args: Any) -> Any:
        await original_finalize(self, *args)
        raise DatabaseTimeoutError("rollback finalization")

    with monkeypatch.context() as patch:
        if phase == "lost_reply":
            patch.setattr(client, "update_qualification", lost)
        else:
            patch.setattr(ConversationService, "finalize_turn", rollback)
        response = await apply(system, message["id"], payload)
    assert response.status_code == 503, response.text
    profile = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    assert profile["target_country"] == "canada"
    recovered = await asyncio.gather(*(apply(system, message["id"], payload) for _ in range(3)))
    assert all(r.status_code == 200 for r in recovered)
    async with system.sessions() as db:
        history = list(
            (
                await db.scalars(
                    select(LeadScoreHistory).where(LeadScoreHistory.lead_id == system.lead_id)
                )
            ).all()
        )
        assert len(history) == 2
    after = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    assert after["version"] == profile["version"]


async def test_patch_metadata_never_confers_confirmation_and_old_bound_receipts_keep_identity(
    system: System,
) -> None:
    await transition_to_greeting(system)
    lead = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    patched = await system.lead.patch(
        f"/v1/leads/{system.lead_id}",
        headers=HEADERS,
        json={"expected_version": lead["version"], "target_country": "Canada"},
    )
    assert patched.status_code == 200
    qualification = (
        await system.lead.get(f"/v1/leads/{system.lead_id}/qualification", headers=HEADERS)
    ).json()
    assert qualification["answers"] == [] and qualification["score"] is None
    text = "I confirm my masters degree."
    message = await record(system, text)
    bound = await system.conversation.post(
        f"{base(system)}/{message['id']}/facts", headers=HEADERS, json=proposals(text)
    )
    assert bound.status_code == 200
    async with system.sessions.begin() as db:
        stored = await db.get(Message, UUID(message["id"]), with_for_update=True)
        assert stored is not None
        metadata = dict(cast(dict[str, Any], stored.message_metadata))
        metadata.pop("call_provenance")
        stored.message_metadata = metadata  # Represents an already-bound pre-upgrade receipt.
    applied = await apply(system, message["id"], proposals(text))
    assert applied.status_code == 200
    assert applied.json()["qualification"]["answers"][0]["call_id"] is None
    replay = await apply(system, message["id"], proposals(text))
    assert replay.status_code == 200
    await later_call(system)
    revised = await turn(system, "education_level", "bachelors", "I confirm my bachelors degree.")
    assert revised["qualification"]["answers"][0]["value"] == "bachelors"


async def test_later_call_can_confirm_a_revision_after_an_earlier_call_conflict(
    system: System,
) -> None:
    await transition_to_greeting(system)
    await turn(system, "years_experience", 3, "I confirm 3 years of experience.")
    conflict = await turn(system, "years_experience", 4, "I have 4 years of experience.")
    assert conflict["qualification"]["answers"][0]["answer_status"] == "CONTRADICTORY"
    first_call = await later_call(system)
    candidate = await turn(system, "years_experience", 5, "I have 5 years of experience.")
    answer = candidate["qualification"]["answers"][0]
    assert answer["value"] == 3 and answer["pending_value"] == 5
    assert answer["call_id"] == str(first_call)
    context = await system.conversation.get(
        f"/v1/conversations/{system.conversation_id}/qualification-context",
        headers=HEADERS,
    )
    assert "years_experience" not in context.json()["plan"]["contradictory_fields"]
    assert "I confirm 5 years of experience" in context.json()["plan"]["next_question"]
    confirmed = await turn(system, "years_experience", 5, "I confirm 5 years of experience.")
    answer = confirmed["qualification"]["answers"][0]
    assert answer["answer_status"] == "CONFIRMED" and answer["value"] == 5
    assert confirmed["qualification"]["score"]["score"] == 10


async def test_original_immigration_utterance_persists_two_non_scoring_intake_facts(
    system: System,
) -> None:
    await transition_to_greeting(system)
    await turn(system, "education_level", "masters", "I confirm my masters degree.")
    current = (
        await system.conversation.get(
            f"/v1/conversations/{system.conversation_id}",
            headers=HEADERS,
        )
    ).json()
    text = "I want to go to USA and I need H1B visa."
    message = await record(system, text, current["version"])
    payload = proposals(text, "USA", "target_country")
    payload["proposals"].append({"field_key": "visa_type", "value": "H1B", "evidence": text})
    applied = await apply(system, message["id"], payload)
    assert applied.status_code == 200, applied.text
    qualification = applied.json()["qualification"]
    answers = {a["field_key"]: a for a in qualification["answers"]}
    assert answers["target_country"]["value"] == "united states"
    assert answers["visa_type"]["value"] == "h-1b"
    assert (
        answers["target_country"]["answer_status"]
        == answers["visa_type"]["answer_status"]
        == "PROVISIONAL"
    )
    assert qualification["score"]["score"] == 10 and qualification["completeness"] == 17
    profile = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    assert profile["target_country"] is None


async def test_lead_promotion_and_profile_mirror_roll_back_together(
    system: System, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lead_service.service import LeadService

    await transition_to_greeting(system)
    await turn(system, "target_country", "united states", "I confirm my target country is USA.")
    await later_call(system)
    current = (
        await system.conversation.get(
            f"/v1/conversations/{system.conversation_id}",
            headers=HEADERS,
        )
    ).json()
    text = "I confirm my target country is Canada."
    message = await record(system, text, current["version"])
    payload = proposals(text, "canada", "target_country")
    original = LeadService.update_lead

    async def rollback(self: LeadService, *args: Any) -> Any:
        await original(self, *args)
        raise DatabaseTimeoutError("rollback Lead transaction after profile update")

    with monkeypatch.context() as patch:
        patch.setattr(LeadService, "update_lead", rollback)
        failed = await apply(system, message["id"], payload)
    assert failed.status_code == 503, failed.text
    profile = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    assert profile["target_country"] == "united states"
    qualification = (
        await system.lead.get(
            f"/v1/leads/{system.lead_id}/qualification",
            headers=HEADERS,
        )
    ).json()
    assert qualification["answers"][0]["value"] == "united states"
    async with system.sessions() as db:
        history = list(
            (
                await db.scalars(
                    select(LeadScoreHistory).where(
                        LeadScoreHistory.lead_id == system.lead_id,
                    )
                )
            ).all()
        )
        assert len(history) == 1
    recovered = await apply(system, message["id"], payload)
    assert recovered.status_code == 200
    assert recovered.json()["qualification"]["answers"][0]["value"] == "canada"


async def test_operator_country_patch_does_not_inherit_caller_confirmation(system: System) -> None:
    await transition_to_greeting(system)
    await turn(system, "target_country", "united states", "I confirm my target country is USA.")
    profile = (await system.lead.get(f"/v1/leads/{system.lead_id}", headers=HEADERS)).json()
    patched = await system.lead.patch(
        f"/v1/leads/{system.lead_id}",
        headers=HEADERS,
        json={"expected_version": profile["version"], "target_country": "Canada"},
    )
    assert patched.status_code == 200
    qualification = (
        await system.lead.get(
            f"/v1/leads/{system.lead_id}/qualification",
            headers=HEADERS,
        )
    ).json()
    assert qualification["answers"][0]["value"] == "united states"
    assert qualification["answers"][0]["answer_status"] == "CONFIRMED"
    assert qualification["score"]["score"] == 0
    await later_call(system)
    confirmed = await turn(
        system, "target_country", "canada", "I confirm my target country is Canada."
    )
    assert confirmed["qualification"]["answers"][0]["value"] == "canada"


async def test_call_scoped_update_receipt_rejects_another_call_identity(system: System) -> None:
    await transition_to_greeting(system)
    result = await turn(system, "years_experience", 3, "I confirm 3 years of experience.")
    replay = {
        "conversation_id": str(system.conversation_id),
        "turn_id": result["message_id"],
        "call_id": str(uuid4()),
        "expected_profile_version": result["qualification"]["version"],
        "facts": [{"field_key": "years_experience", "value": 3, "status": "CONFIRMED"}],
    }
    response = await system.lead.post(
        f"/v1/leads/{system.lead_id}/qualification/updates",
        headers=HEADERS,
        json=replay,
    )
    assert response.status_code == 409
