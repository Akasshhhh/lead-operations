"""Bounded, content-free diagnostics and faults preserve actual router semantics."""

import asyncio
import json
from contextlib import aclosing
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from voice_platform_config.observability import RequestTelemetry, Telemetry, request_context
from voice_platform_contracts.observability import OperationalSnapshot
from voice_platform_llm import (
    GenerationRequest,
    LLMError,
    LLMRouter,
    MockLLMProvider,
    MockScript,
    PromptContext,
    ProviderSlot,
    RouterPolicy,
)
from voice_platform_runtime.diagnostics import (
    FaultConflict,
    FaultCreate,
    Faults,
    ObservedLLM,
    ObservedSTT,
    ObservedTTS,
    faults_enabled,
)
from voice_platform_speech import (
    AudioChunk,
    AudioFormat,
    MockSTTProvider,
    MockTTSProvider,
    SpeechError,
    SpeechRouterPolicy,
    SpeechSlot,
    STTRouter,
    SynthesisRequest,
    TranscriptionRequest,
    TTSRouter,
)


def request() -> GenerationRequest:
    return GenerationRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        turn_id=uuid4(),
        context=PromptContext(system_instruction="private instructions"),
    )


def fault(**values: Any) -> FaultCreate:
    return FaultCreate(operation_id=uuid4(), **values)


def test_measurements_are_bounded_and_failed_spans_preserve_error_code() -> None:
    telemetry = Telemetry("test")
    with pytest.raises(ValueError):
        with telemetry.span("write") as result:
            result["outcome"] = "timeout"
            raise ValueError("private details")
    for index in range(300):
        telemetry.record(f"known-operation-{index}", "ok", 1)
    snapshot = OperationalSnapshot.model_validate(telemetry.snapshot())
    assert len(snapshot.metrics) == 128 and len(snapshot.recent) == 32
    assert snapshot.metrics[0].errors == 1
    assert "private details" not in snapshot.model_dump_json()


@pytest.mark.asyncio
async def test_request_logs_have_template_ids_status_not_query_or_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    telemetry = Telemetry("test")
    app.add_middleware(RequestTelemetry, telemetry=telemetry)

    @app.get("/records/{record_id}")
    async def read(record_id: str) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"request": str(request_context.get())}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        responses = await asyncio.gather(
            *(
                client.get(
                    "/records/private-body?token=private-token",
                    headers={"X-Request-ID": f"req-{i}"},
                )
                for i in range(5)
            )
        )
        for i, response in enumerate(responses):
            assert response.json()["request"] == f"req-{i}"
        await client.get("/unknown/private-path")
    encoded = json.dumps(telemetry.snapshot()) + caplog.text
    assert "GET /records/{record_id}" in encoded
    assert "private-token" not in encoded and "private-body" not in encoded
    assert "private-path" not in encoded
    assert request_context.get() is None


@pytest.mark.parametrize("environment", ["production", "unknown"])
def test_fault_opt_in_rejects_non_demo_environments(
    monkeypatch: pytest.MonkeyPatch,
    environment: str,
) -> None:
    monkeypatch.setenv("DEMO_FAULTS_ENABLED", "1")
    monkeypatch.setenv("APP_ENV", environment)
    with pytest.raises(ValueError):
        faults_enabled()


@pytest.mark.parametrize(
    "payload",
    [
        {"target": "database", "mode": "unavailable"},
        {"target": "llm", "mode": "unavailable", "attempts": 11},
        {"target": "llm", "mode": "unavailable", "duration_seconds": 61},
        {"target": "llm", "mode": "unavailable", "attempts": True},
        {"target": "llm", "mode": "unavailable", "token": "private"},
    ],
)
def test_fault_contract_rejects_arbitrary_targets_and_unbounded_effects(
    payload: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError):
        FaultCreate.model_validate({"operation_id": str(uuid4()), **payload})


@pytest.mark.asyncio
async def test_expiry_reservation_reset_and_session_isolation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, other = Faults(Telemetry("one")), Faults(Telemetry("two"))
    data = fault(target="llm", mode="unavailable", attempts=2)
    first.arm(data)
    assert await other.hit("llm", timeout=1) is None
    outcomes = await asyncio.gather(*(first.hit("llm", timeout=1) for _ in range(5)))
    assert outcomes.count("unavailable") == 2
    first.arm(data)
    assert first.snapshot() is None
    with pytest.raises(FaultConflict):
        first.arm(data.model_copy(update={"target": "tts"}))
    first.arm(fault(target="tts", mode="latency"))
    monkeypatch.setattr("voice_platform_runtime.diagnostics.time.monotonic", lambda: 10**12)
    assert first.snapshot() is None
    first.reset()
    assert await first.hit("tts", timeout=1) is None


@pytest.mark.asyncio
async def test_faults_drive_retries_fallback_and_do_not_reset_circuit_health() -> None:
    class Secondary(MockLLMProvider):
        name = "secondary"

    telemetry = Telemetry("test")
    faults = Faults(telemetry)
    faults.arm(fault(target="llm", mode="unavailable", attempts=2))
    router = LLMRouter(
        (
            ProviderSlot(ObservedLLM(MockLLMProvider(), telemetry, faults, True)),
            ProviderSlot(ObservedLLM(Secondary(), telemetry, faults, False)),
        ),
        policy=RouterPolicy(retry_delay_seconds=0),
    )
    result = await router.generate(request())
    assert result.provider == "secondary"
    assert router.health()[0].failure_count == 2 and router.health()[0].failover_count == 1
    faults.reset()
    assert router.health()[0].failure_count == 2
    assert (await router.generate(request())).provider == "mock"
    assert telemetry.metrics["llm.attempt"]["errors"] == 2


@pytest.mark.asyncio
async def test_observer_never_replays_partial_provider_output() -> None:
    telemetry, faults = Telemetry("test"), Faults(Telemetry("faults"))
    provider = MockLLMProvider(MockScript(text="partial private text", fail_after_events=1))
    router = LLMRouter((ProviderSlot(ObservedLLM(provider, telemetry, faults, True)),))
    exposed: list[Any] = []
    with pytest.raises(LLMError):
        async with aclosing(router.stream(request())) as events:
            async for event in events:
                exposed.append(event)
    assert len(exposed) == 1
    assert telemetry.metrics["llm.attempt"]["count"] == 1
    assert "private text" not in json.dumps(telemetry.snapshot())


@pytest.mark.asyncio
async def test_latency_respects_existing_deadline_and_cancellation() -> None:
    telemetry = Telemetry("test")
    faults = Faults(telemetry)
    faults.arm(fault(target="llm", mode="latency", attempts=1))
    router = LLMRouter(
        (ProviderSlot(ObservedLLM(MockLLMProvider(), telemetry, faults, True)),),
        policy=RouterPolicy(attempt_timeout_seconds=0.01, retry_delay_seconds=0),
    )
    # The attempt times out before invoking the vendor; the existing safe retry can succeed.
    assert (await router.generate(request())).provider == "mock"
    assert router.health()[0].failure_count == 1
    faults.arm(fault(target="llm", mode="latency"))
    stream = ObservedLLM(MockLLMProvider(), telemetry, faults, True).stream(request())
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stream.aclose()
    assert telemetry.recent[-1]["outcome"] == "cancelled"


@pytest.mark.asyncio
async def test_speech_faults_are_observed_by_existing_routers() -> None:
    telemetry = Telemetry("test")
    faults = Faults(telemetry)
    tts = TTSRouter(
        (SpeechSlot(ObservedTTS(MockTTSProvider(), telemetry, faults, True)),),
        policy=SpeechRouterPolicy(max_attempts=1),
    )
    faults.arm(fault(target="tts", mode="unavailable"))
    synth = SynthesisRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        call_id=uuid4(),
        utterance_id=uuid4(),
        text="private response",
        audio_format=AudioFormat(sample_rate=24000),
    )
    with pytest.raises(SpeechError):
        async with aclosing(tts.synthesize(synth)) as stream:
            await anext(stream)
    assert tts.health()[0].failure_count == 1
    faults.reset()
    async with aclosing(tts.synthesize(synth)) as stream:
        assert len([item async for item in stream]) > 1
    stt = STTRouter(
        (SpeechSlot(ObservedSTT(MockSTTProvider(), telemetry, faults, True)),),
        policy=SpeechRouterPolicy(max_attempts=1),
    )
    faults.arm(fault(target="stt", mode="unavailable"))

    async def audio() -> Any:
        yield AudioChunk(sequence=0, data=b"\x00\x00" * 320)

    transcription = TranscriptionRequest(
        request_id=uuid4(), conversation_id=uuid4(), call_id=uuid4(), utterance_id=uuid4()
    )
    with pytest.raises(SpeechError):
        async with aclosing(stt.transcribe(transcription, audio())) as transcript_stream:
            await anext(transcript_stream)
    assert stt.health()[0].failure_count == 1
    assert "private response" not in json.dumps(telemetry.snapshot())
