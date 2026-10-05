"""Pipecat frames bridge bounded speech streams and durable dialogue coordination."""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing, suppress
from dataclasses import dataclass
from uuid import UUID, uuid4

from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    StartFrame,
    SystemFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from voice_platform_llm import LLMError
from voice_platform_speech import (
    AudioChunk,
    AudioEvent,
    AudioFormat,
    SpeechError,
    STTRouter,
    SynthesisRequest,
    TranscriptEvent,
    TranscriptionRequest,
    TTSRouter,
)

from .backend import DependencyError
from .dialogue import Dialogue


@dataclass
class CommandFrame(SystemFrame):
    command: str


class AudioFeed:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[AudioChunk | None] = asyncio.Queue(maxsize=64)
        self.sequence = 0
        self.samples = 0
        self.closed = False

    def add(self, data: bytes) -> None:
        if self.closed or self.samples + len(data) // 2 > 16000 * 30:
            raise ValueError("utterance limit")
        self.queue.put_nowait(AudioChunk(sequence=self.sequence, data=data))
        self.sequence += 1
        self.samples += len(data) // 2

    def finish(self) -> None:
        self.queue.put_nowait(None)

    def __aiter__(self) -> "AudioFeed":
        return self

    async def __anext__(self) -> AudioChunk:
        if self.closed:
            raise StopAsyncIteration
        value = await self.queue.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def aclose(self) -> None:
        self.closed = True
        while not self.queue.empty():
            self.queue.get_nowait()


class VoiceProcessor(FrameProcessor):
    def __init__(
        self,
        dialogue: Dialogue,
        stt: STTRouter,
        tts: TTSRouter,
        notify: Callable[[dict[str, object]], Awaitable[None]],
        *,
        manual: bool = False,
    ) -> None:
        super().__init__()
        self.dialogue = dialogue
        self.stt = stt
        self.tts = tts
        self.notify = notify
        self.manual = manual
        self.ready = False
        self.feed: AudioFeed | None = None
        self.stt_task: asyncio.Task[None] | None = None
        self.response_task: asyncio.Task[None] | None = None
        self.prebuffer = b""
        self.finish_media: Callable[[], Awaitable[None]] | None = None

    async def _cancel_response(self, *, flush: bool = False) -> None:
        if self.response_task is not None:
            self.response_task.cancel()
        if flush:
            # Stop queued playback promptly; joining a shielded write can take seconds.
            await self.push_frame(InterruptionFrame())
        if self.response_task is not None:
            with suppress(asyncio.CancelledError):
                await self.response_task
            self.response_task = None

    async def interrupt(self) -> None:
        await self._cancel_response(flush=True)
        await self.notify({"type": "interrupted"})

    async def halt(self) -> None:
        self.ready = False
        await self.interrupt()
        if self.stt_task is not None:
            self.stt_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.stt_task
            self.stt_task = None
        if self.feed is not None:
            await self.feed.aclose()
            self.feed = None
        self.prebuffer = b""

    async def cleanup(self) -> None:
        await self.halt()
        await super().cleanup()  # type: ignore[no-untyped-call]

    async def _error(self, code: str) -> None:
        await self.notify(
            {
                "type": "error",
                "code": code,
                "retry_required": self.dialogue.pending is not None
                or not self.ready
                or getattr(self.dialogue, "workflow_pending", None) is not None
                or getattr(self.dialogue, "close_media_requested", False),
            }
        )

    async def _speak(self, text: str, mid: UUID) -> None:
        await self.notify(
            {"type": "agent", "text": text, "message_id": str(mid), "output_kind": "generated"}
        )
        request = SynthesisRequest(
            request_id=uuid4(),
            conversation_id=self.dialogue.cid,
            call_id=self.dialogue.call_id,
            utterance_id=mid,
            text=text,
            audio_format=AudioFormat(sample_rate=24000),
            timeout_seconds=30,
        )
        await self.push_frame(TTSStartedFrame())
        async with aclosing(self.tts.synthesize(request)) as stream:
            async for event in stream:
                if isinstance(event, AudioEvent):
                    await self.push_frame(
                        TTSAudioRawFrame(audio=event.chunk.data, sample_rate=24000, num_channels=1)
                    )
        await self.push_frame(TTSStoppedFrame())
        await self.notify({"type": "ready"})

    async def _respond(self, operation: Callable[[], Awaitable[tuple[str, UUID] | None]]) -> None:
        try:
            result = await operation()
            if result is not None:
                await self._speak(*result)
                if getattr(self.dialogue, "close_media_requested", False) and self.finish_media:
                    self.ready = False
                    await self.finish_media()
        except (DependencyError, SpeechError, LLMError) as exc:
            await self.push_frame(InterruptionFrame())
            await self._error(
                "dependency_unavailable" if isinstance(exc, DependencyError) else exc.code
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.push_frame(InterruptionFrame())
            await self._error("runtime_error")

    async def _transcribe(self, feed: AudioFeed, uid: UUID) -> None:
        try:
            request = TranscriptionRequest(
                request_id=uuid4(),
                conversation_id=self.dialogue.cid,
                call_id=self.dialogue.call_id,
                utterance_id=uid,
                timeout_seconds=45,
            )
            final: list[str] = []
            async with aclosing(self.stt.transcribe(request, feed)) as stream:
                async for event in stream:
                    if isinstance(event, TranscriptEvent):
                        await self.notify(
                            {
                                "type": "transcript",
                                "text": event.text,
                                "final": event.is_final,
                                "utterance_id": str(uid),
                            }
                        )
                        if event.is_final:
                            final.append(event.text)
            if final:
                self.response_task = asyncio.create_task(
                    self._respond(lambda: self.dialogue.reply(" ".join(final), uid))
                )
        except SpeechError as exc:
            await self._error(exc.code)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._error("runtime_error")
        finally:
            await feed.aclose()

    async def begin_utterance(self) -> None:
        if not self.ready:
            return
        await self.interrupt()
        if self.dialogue.pending is not None or (
            self.stt_task is not None and not self.stt_task.done()
        ):
            await self._error("turn_busy")
            return
        self.feed = AudioFeed()
        if self.prebuffer:
            self.feed.add(self.prebuffer)
        self.stt_task = asyncio.create_task(self._transcribe(self.feed, uuid4()))

    async def end_utterance(self) -> None:
        if self.feed is not None:
            try:
                self.feed.finish()
            except asyncio.QueueFull:
                await self.halt()
                await self._error("audio_overflow")
            self.feed = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
        elif isinstance(frame, CommandFrame):
            if frame.command == "greet":
                await self._cancel_response()
                self.response_task = asyncio.create_task(self._respond(self.dialogue.greeting))
            elif frame.command == "start" and self.manual:
                await self.begin_utterance()
            elif frame.command == "stop" and self.manual:
                await self.end_utterance()
            elif frame.command == "retry":
                await self._cancel_response()
                try:
                    await self.dialogue.recover()
                    await self.notify({"type": "recovered", "audio_replayed": False})
                    if getattr(self.dialogue, "close_media_requested", False) and self.finish_media:
                        self.ready = False
                        await self.finish_media()
                except DependencyError:
                    await self._error("dependency_unavailable")
        elif isinstance(frame, VADUserStartedSpeakingFrame) and not self.manual:
            await self.begin_utterance()
        elif isinstance(frame, VADUserStoppedSpeakingFrame) and not self.manual:
            await self.end_utterance()
        elif isinstance(frame, InputAudioRawFrame):
            if frame.sample_rate != 16000 or frame.num_channels != 1 or len(frame.audio) % 2:
                await self.halt()
                await self._error("invalid_audio")
                return
            if self.feed is not None:
                try:
                    self.feed.add(frame.audio)
                except (ValueError, asyncio.QueueFull):
                    await self.halt()
                    await self._error("audio_overflow")
            self.prebuffer = (self.prebuffer + frame.audio)[-6400:]
        elif isinstance(frame, (CancelFrame, EndFrame)):
            await self.halt()
            await self.push_frame(frame, direction)
        elif isinstance(frame, (StartFrame, InterruptionFrame)):
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)
