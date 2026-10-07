"""Automatic turn boundaries and retryable microphone failures without business writes."""

import asyncio
from typing import Any, cast
from uuid import uuid4

import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from voice_platform_runtime.dialogue import Dialogue
from voice_platform_runtime.processor import CommandFrame, VoiceProcessor
from voice_platform_speech import (
    MockSTTProvider,
    MockSTTScript,
    MockTTSProvider,
    SpeechSlot,
    STTRouter,
    TTSRouter,
)


class CallerDialogue:
    pending = None
    cid = uuid4()
    call_id = uuid4()

    def __init__(self) -> None:
        self.turns: list[str] = []

    async def reply(self, text: str, uid: Any) -> tuple[str, Any]:
        self.turns.append(text)
        return "Your words are coming through.", uuid4()


@pytest.mark.asyncio
@pytest.mark.parametrize("manual", [False, True])
async def test_auto_stops_on_vad_but_explicit_push_to_talk_waits_for_stop(
    manual: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = CallerDialogue()
    notifications: list[dict[str, object]] = []

    async def notify(event: dict[str, object]) -> None:
        notifications.append(event)

    processor = VoiceProcessor(
        cast(Dialogue, caller),
        STTRouter((SpeechSlot(MockSTTProvider(MockSTTScript(text="Can you hear me?"))),)),
        TTSRouter((SpeechSlot(MockTTSProvider()),)),
        notify,
        manual=manual,
    )

    async def push(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(processor, "push_frame", push)
    processor.ready = True
    direction = FrameDirection.DOWNSTREAM
    await processor.process_frame(VADUserStartedSpeakingFrame(), direction)
    if manual:
        assert processor.feed is None
        await processor.process_frame(CommandFrame("start"), direction)
    await processor.process_frame(
        InputAudioRawFrame(audio=b"\0\0" * 320, sample_rate=16000, num_channels=1), direction
    )
    await processor.process_frame(VADUserStoppedSpeakingFrame(), direction)
    if manual:
        assert processor.feed is not None and caller.turns == []
        await processor.process_frame(CommandFrame("stop"), direction)
    assert processor.feed is None
    assert processor.stt_task is not None
    await asyncio.wait_for(processor.stt_task, 1)
    assert processor.response_task is not None
    await asyncio.wait_for(processor.response_task, 1)
    assert caller.turns == ["Can you hear me?"]
    assert any(event["type"] == "ready" for event in notifications)
    await processor.halt()


@pytest.mark.asyncio
async def test_failed_stt_releases_feed_and_next_utterance_can_succeed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = CallerDialogue()
    notifications: list[dict[str, object]] = []

    async def notify(event: dict[str, object]) -> None:
        notifications.append(event)

    processor = VoiceProcessor(
        cast(Dialogue, caller),
        STTRouter(
            (
                SpeechSlot(
                    MockSTTProvider(MockSTTScript(fail_after_events=0, error_code="invalid_output"))
                ),
            )
        ),
        TTSRouter((SpeechSlot(MockTTSProvider()),)),
        notify,
    )

    async def push(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(processor, "push_frame", push)
    processor.ready = True
    await processor.begin_utterance()
    assert processor.feed is not None
    processor.feed.add(b"\0\0" * 320)
    await processor.end_utterance()
    assert processor.stt_task is not None
    await asyncio.wait_for(processor.stt_task, 1)
    assert caller.turns == [] and processor.feed is None
    assert any(
        event.get("code") == "invalid_output" and event["retry_required"] is False
        for event in notifications
    )
    processor.stt = STTRouter((SpeechSlot(MockSTTProvider(MockSTTScript(text="Try again"))),))
    await processor.begin_utterance()
    assert processor.feed is not None
    processor.feed.add(b"\0\0" * 320)
    await processor.end_utterance()
    assert processor.stt_task is not None
    await asyncio.wait_for(processor.stt_task, 1)
    assert processor.response_task is not None
    await asyncio.wait_for(processor.response_task, 1)
    assert caller.turns == ["Try again"]
    await processor.halt()


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["stt", "pending", "workflow"])
async def test_speech_onset_during_accepted_work_does_not_cancel_or_require_recovery(
    busy: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    caller = CallerDialogue()
    notifications: list[dict[str, object]] = []
    frames: list[Any] = []
    started, release = asyncio.Event(), asyncio.Event()

    async def notify(event: dict[str, object]) -> None:
        notifications.append(event)

    async def push(frame: Any, *args: Any, **kwargs: Any) -> None:
        frames.append(frame)

    async def accepted_work() -> None:
        started.set()
        await release.wait()

    processor = VoiceProcessor(
        cast(Dialogue, caller),
        STTRouter((SpeechSlot(MockSTTProvider()),)),
        TTSRouter((SpeechSlot(MockTTSProvider()),)),
        notify,
    )
    monkeypatch.setattr(processor, "push_frame", push)
    processor.ready = True
    task = asyncio.create_task(accepted_work())
    if busy == "stt":
        processor.stt_task = task
    else:
        processor.response_task = task
        setattr(caller, "pending" if busy == "pending" else "workflow_pending", object())
    await started.wait()
    try:
        await processor.begin_utterance()
        await processor.end_utterance()
        assert not task.done() and processor.feed is None
        assert frames == [] and notifications == []
        release.set()
        await asyncio.wait_for(task, 1)
        assert not task.cancelled()
    finally:
        release.set()
        await processor.halt()


@pytest.mark.asyncio
async def test_failed_saved_turn_still_requires_recovery_without_flushing_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = CallerDialogue()
    cast(Any, caller).pending = object()
    notifications: list[dict[str, object]] = []
    frames: list[Any] = []

    async def notify(event: dict[str, object]) -> None:
        notifications.append(event)

    async def push(frame: Any, *args: Any, **kwargs: Any) -> None:
        frames.append(frame)

    processor = VoiceProcessor(
        cast(Dialogue, caller),
        STTRouter((SpeechSlot(MockSTTProvider()),)),
        TTSRouter((SpeechSlot(MockTTSProvider()),)),
        notify,
    )
    monkeypatch.setattr(processor, "push_frame", push)
    processor.ready = True
    await processor.begin_utterance()
    assert processor.feed is None and frames == []
    assert notifications == [{"type": "error", "code": "turn_busy", "retry_required": True}]
