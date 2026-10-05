"""Real Pipecat frame execution with deterministic providers and controlled domain boundary."""

import asyncio
from contextlib import suppress
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest

pytest.importorskip("pipecat")

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    TTSAudioRawFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.workers.runner import WorkerRunner
from voice_platform_runtime.backend import Backend, DependencyError
from voice_platform_runtime.dialogue import Dialogue
from voice_platform_runtime.processor import AudioFeed, CommandFrame, VoiceProcessor
from voice_platform_speech import (
    MockSTTProvider,
    MockTTSProvider,
    MockTTSScript,
    SpeechSlot,
    STTRouter,
    TTSRouter,
    fixture_pcm,
)
from voice_platform_speech.provider import STTProvider, TTSProvider


class FakeDialogue:
    cid = uuid4()
    call_id = uuid4()
    pending = None
    close_media_requested = False

    def __init__(self, delay: float = 0, fail: bool = False) -> None:
        self.users: list[tuple[str, UUID]] = []
        self.delay = delay
        self.fail = fail
        self.cancelled = False
        self.recovered = False

    async def reply(self, text: str, uid: UUID) -> tuple[str, UUID]:
        self.users.append((text, uid))
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.fail:
            raise DependencyError()
        return "A bounded response", uuid4()

    async def greeting(self) -> tuple[str, UUID] | None:
        return None

    async def recover(self) -> None:
        self.recovered = True


@pytest.mark.asyncio
async def test_workflow_ack_audio_drains_before_runtime_media_finish() -> None:
    dialogue = FakeDialogue()
    dialogue.close_media_requested = True
    events: list[dict[str, object]] = []

    async def notify(payload: dict[str, object]) -> None:
        events.append(payload)

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
        manual=True,
    )
    sink = Sink()
    worker = PipelineWorker(Pipeline([processor, sink]), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)

    async def finish() -> None:
        await worker.queue_frame(EndFrame())

    processor.finish_media = finish
    processor.ready = True
    task = asyncio.create_task(runner.run())
    try:
        await worker.queue_frame(CommandFrame("start"))
        await worker.queue_frame(
            InputAudioRawFrame(audio=fixture_pcm(), sample_rate=16000, num_channels=1)
        )
        await worker.queue_frame(CommandFrame("stop"))
        await asyncio.wait_for(task, 5)
        audio_indices = [i for i, f in enumerate(sink.frames) if isinstance(f, TTSAudioRawFrame)]
        end_indices = [i for i, f in enumerate(sink.frames) if isinstance(f, EndFrame)]
        assert audio_indices and end_indices and max(audio_indices) < min(end_indices)
        assert not processor.ready
    finally:
        await runner.cancel()
        await task


class Sink(FrameProcessor):
    def __init__(self) -> None:
        super().__init__()
        self.frames: list[Frame] = []
        self.audio = asyncio.Event()

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        self.frames.append(frame)
        if isinstance(frame, TTSAudioRawFrame):
            self.audio.set()
        await self.push_frame(frame, direction)


async def wait_until(check: Any) -> None:
    async with asyncio.timeout(5):
        while not check():
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_real_pipecat_pipeline_bridges_stt_dialogue_tts_and_handles_failure(
    fail: bool,
) -> None:
    dialogue = FakeDialogue(fail=fail)
    notifications: list[dict[str, object]] = []

    async def notify(payload: dict[str, object]) -> None:
        notifications.append(payload)

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
        manual=True,
    )
    processor.ready = True
    sink = Sink()
    worker = PipelineWorker(Pipeline([processor, sink]), idle_timeout_secs=None)
    runner = WorkerRunner(handle_sigint=False)
    await runner.add_workers(worker)
    task = asyncio.create_task(runner.run())
    try:
        await worker.queue_frame(CommandFrame("start"))
        await worker.queue_frame(
            InputAudioRawFrame(audio=fixture_pcm(), sample_rate=16000, num_channels=1)
        )
        await worker.queue_frame(CommandFrame("stop"))
        await wait_until(
            lambda: any(n.get("type") == ("error" if fail else "ready") for n in notifications)
        )
        assert dialogue.users[0][0] == "Hello"
        assert any(n.get("type") == "transcript" and n.get("final") for n in notifications)
        if fail:
            assert not sink.audio.is_set()
            assert any(n.get("code") == "dependency_unavailable" for n in notifications)
        else:
            assert sink.audio.is_set()
            assert all(
                f.sample_rate == 24000 for f in sink.frames if isinstance(f, TTSAudioRawFrame)
            )
    finally:
        await runner.cancel()
        await task
    assert processor.stt_task is None and processor.response_task is None


@pytest.mark.asyncio
async def test_barge_in_cancels_generation_and_discards_output() -> None:
    dialogue = FakeDialogue(delay=10)
    events: list[dict[str, object]] = []

    async def notify(value: dict[str, object]) -> None:
        events.append(value)

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
        manual=True,
    )
    pushed: list[Frame] = []

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        pushed.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    processor.ready = True
    processor.response_task = asyncio.create_task(
        processor._respond(lambda: dialogue.reply("hello", uuid4()))
    )
    await wait_until(lambda: bool(dialogue.users))
    await processor.begin_utterance()
    assert dialogue.cancelled
    assert any(isinstance(frame, InterruptionFrame) for frame in pushed)
    assert not any(isinstance(frame, TTSAudioRawFrame) for frame in pushed)
    await processor.halt()


@pytest.mark.asyncio
async def test_interruption_during_tts_closes_stream_and_no_more_audio() -> None:
    dialogue = FakeDialogue()
    events: list[dict[str, object]] = []

    async def notify(value: dict[str, object]) -> None:
        events.append(value)

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter(
            (
                SpeechSlot[TTSProvider](
                    MockTTSProvider(MockTTSScript(chunks=10, delay_seconds=0.05))
                ),
            )
        ),
        notify,
    )
    frames: list[Frame] = []

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        frames.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    processor.response_task = asyncio.create_task(
        processor._respond(lambda: dialogue.reply("hello", uuid4()))
    )
    await wait_until(lambda: any(isinstance(f, TTSAudioRawFrame) for f in frames))
    await processor.interrupt()
    count = len(frames)
    await asyncio.sleep(0.12)
    assert len(frames) == count
    assert processor.response_task is None


@pytest.mark.asyncio
async def test_audio_feed_has_backpressure_bounds_and_cleanup() -> None:
    feed = AudioFeed()
    for _ in range(64):
        feed.add(fixture_pcm())
    with pytest.raises(asyncio.QueueFull):
        feed.add(fixture_pcm())
    first = await anext(feed)
    assert first.sequence == 0
    feed.finish()
    await feed.aclose()
    assert feed.queue.empty()
    with pytest.raises(StopAsyncIteration):
        await anext(feed)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 409, 422, 500, 503])
async def test_backend_status_errors_do_not_expose_vendor_or_database_content(status: int) -> None:
    from voice_platform_contracts.conversation import ConversationResponse

    async with httpx.AsyncClient(
        base_url="http://conversation",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status, text="private token and transcript")
        ),
    ) as client:
        backend = Backend(client, "test-token", timeout=0.1)
        with pytest.raises(DependencyError) as error:
            await backend.request("GET", "/test", ConversationResponse, uuid4())
    assert "private" not in str(error.value)
    assert error.value.status == (status if status in {409, 422} else 503)


@pytest.mark.asyncio
async def test_backend_timeout_closes_request_and_preserves_client_ownership() -> None:
    from voice_platform_contracts.conversation import ConversationResponse

    cancelled = asyncio.Event()

    async def slow(_request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(
        base_url="http://conversation", transport=httpx.MockTransport(slow)
    ) as client:
        with pytest.raises(DependencyError):
            await Backend(client, "token", 0.01).request(
                "GET", "/test", ConversationResponse, uuid4()
            )
        assert cancelled.is_set() and not client.is_closed


@pytest.mark.asyncio
async def test_audio_feed_cancelled_consumer_can_close_without_leaking_queue() -> None:
    feed = AudioFeed()
    task = asyncio.create_task(anext(feed))
    await asyncio.sleep(0)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await feed.aclose()
    assert feed.closed


@pytest.mark.asyncio
async def test_invalid_media_cancels_whole_utterance_instead_of_skipping_audio() -> None:
    dialogue = FakeDialogue()
    events: list[dict[str, object]] = []

    async def notify(payload: dict[str, object]) -> None:
        events.append(payload)

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
        manual=True,
    )

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        pass

    processor.push_frame = push  # type: ignore[method-assign]
    processor.ready = True
    await processor.begin_utterance()
    feed = processor.feed
    await processor.process_frame(
        InputAudioRawFrame(audio=fixture_pcm(), sample_rate=48000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    assert feed is not None and feed.closed
    assert processor.stt_task is None and not processor.ready
    assert dialogue.users == []
    assert events[-1] == {"type": "error", "code": "invalid_audio", "retry_required": True}


@pytest.mark.asyncio
async def test_repeated_greeting_command_cancels_owned_response_before_replacing_task() -> None:
    dialogue = FakeDialogue(delay=10)

    async def notify(payload: dict[str, object]) -> None:
        pass

    processor = VoiceProcessor(
        cast(Dialogue, dialogue),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
    )

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        pass

    processor.push_frame = push  # type: ignore[method-assign]
    old = asyncio.create_task(processor._respond(lambda: dialogue.reply("hello", uuid4())))
    processor.response_task = old
    await wait_until(lambda: bool(dialogue.users))
    await processor.process_frame(CommandFrame("greet"), FrameDirection.DOWNSTREAM)
    assert old.done() and dialogue.cancelled
    assert processor.response_task is not old
    await processor.halt()


@pytest.mark.asyncio
async def test_interruption_flushes_audio_before_waiting_for_an_inflight_mutation() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    flushed = asyncio.Event()

    class CommittingDialogue(FakeDialogue):
        async def reply(self, text: str, uid: UUID) -> tuple[str, UUID]:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                # A durable mutation must finish even when media is interrupted.
                await release.wait()
                raise
            return text, uid

    async def notify(payload: dict[str, object]) -> None:
        pass

    processor = VoiceProcessor(
        cast(Dialogue, CommittingDialogue()),
        STTRouter((SpeechSlot[STTProvider](MockSTTProvider()),)),
        TTSRouter((SpeechSlot[TTSProvider](MockTTSProvider()),)),
        notify,
    )

    async def push(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        if isinstance(frame, InterruptionFrame):
            flushed.set()

    processor.push_frame = push  # type: ignore[method-assign]
    processor.response_task = asyncio.create_task(
        processor._respond(lambda: processor.dialogue.reply("hello", uuid4()))
    )
    await asyncio.wait_for(started.wait(), 1)
    interruption = asyncio.create_task(processor.interrupt())
    try:
        await asyncio.sleep(0.02)
        assert not interruption.done()  # The database mutation is still being joined.
        assert flushed.is_set()  # Audio must stop without waiting for that commit.
    finally:
        release.set()
        await interruption
        await processor.halt()
