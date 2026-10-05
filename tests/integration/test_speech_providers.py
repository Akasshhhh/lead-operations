import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing, asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from voice_platform_speech import (
    AudioEvent,
    AudioFormat,
    RumikTTSProvider,
    SarvamSTTProvider,
    SarvamTTSProvider,
    SpeechError,
    SpeechRouterPolicy,
    SpeechSlot,
    SpeechToTextRuntime,
    SynthesisCompleted,
    SynthesisRequest,
    TextToSpeechRuntime,
    TranscriptEvent,
    TranscriptionCompleted,
    TranscriptionRequest,
    TTSProvider,
    TTSRouter,
    fixture_audio,
)
from voice_platform_speech.sarvam_stt import Socket, websocket_connector
from websockets.asyncio.server import ServerConnection, serve


def stt_request(timeout: float = 1) -> TranscriptionRequest:
    return TranscriptionRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        call_id=uuid4(),
        utterance_id=uuid4(),
        timeout_seconds=timeout,
    )


def tts_request(timeout: float = 1) -> SynthesisRequest:
    return SynthesisRequest(
        **stt_request(timeout).model_dump(exclude={"audio_format"}),
        text="नमस्ते",
        audio_format=AudioFormat(sample_rate=24000),
    )


class Body(httpx.AsyncByteStream):
    def __init__(
        self, data: bytes = b"\0\1" * 5000, *, error: bool = False, delay: float = 0
    ) -> None:
        self.data, self.error, self.delay = data, error, delay
        self.closed = False

    async def __aiter__(self) -> AsyncGenerator[bytes, None]:
        for index in range(0, len(self.data), 17):
            await asyncio.sleep(self.delay)
            yield self.data[index : index + 17]
        if self.error:
            raise httpx.ReadError("private vendor message")

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sarvam", "rumik"])
async def test_tts_wire_pcm_context_and_resource_ownership(kind: str) -> None:
    body = Body()
    seen: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "audio/pcm; rate=24000; channels=1; format=s16le"},
            stream=body,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = (
            SarvamTTSProvider(client, SecretStr("test-key"))
            if kind == "sarvam"
            else RumikTTSProvider(client, SecretStr("test-key"))
        )
        request = tts_request()
        events = [event async for event in TextToSpeechRuntime(provider).synthesize(request)]
        chunks = [event.chunk for event in events if isinstance(event, AudioEvent)]
        assert b"".join(chunk.data for chunk in chunks) == body.data
        assert [chunk.sequence for chunk in chunks] == list(range(len(chunks)))
        assert events[-1] == SynthesisCompleted(output_samples=5000)
        assert body.closed and not client.is_closed
        payload = json.loads(seen[0].content)
        assert payload["text"] == request.text
        assert seen[0].headers["X-Request-ID"] == str(request.request_id)
        assert str(seen[0].url) == provider.endpoint
        if kind == "sarvam":
            assert (
                payload["output_audio_codec"] == "linear16"
                and payload["speech_sample_rate"] == 24000
            )
            assert seen[0].headers["Api-Subscription-Key"] == "test-key"
        else:
            assert payload["audio_format"] == "pcm" and payload["description"]
            assert seen[0].headers["Authorization"] == "Bearer test-key"
        assert "test-key" not in repr(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, code",
    [
        (302, "provider_error"),
        (400, "provider_error"),
        (401, "provider_error"),
        (403, "provider_error"),
        (408, "timeout"),
        (429, "rate_limited"),
        (500, "unavailable"),
        (503, "unavailable"),
    ],
)
@pytest.mark.parametrize("kind", ["sarvam", "rumik"])
async def test_tts_status_errors_are_safe(status: int, code: str, kind: str) -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, text="private key"))
    ) as client:
        provider = (
            SarvamTTSProvider(client, SecretStr("test-key"))
            if kind == "sarvam"
            else RumikTTSProvider(client, SecretStr("test-key"))
        )
        with pytest.raises(SpeechError) as failure:
            _ = [event async for event in TextToSpeechRuntime(provider).synthesize(tts_request())]
        assert failure.value.code == code
        assert "private" not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content_type, data",
    [
        ("audio/wav", b"RIFF"),
        ("audio/mpeg", b"ID3"),
        ("application/json", b"{}"),
        ("audio/pcm; rate=16000", b"aa"),
        ("audio/pcm; channels=2", b"aa"),
        ("audio/pcm", b"a"),
        ("audio/pcm", b""),
    ],
)
async def test_tts_rejects_incompatible_or_truncated_output(content_type: str, data: bytes) -> None:
    body = Body(data)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": content_type}, stream=body)
        )
    ) as client:
        with pytest.raises(SpeechError, match="invalid_output"):
            _ = [
                event
                async for event in TextToSpeechRuntime(
                    RumikTTSProvider(client, SecretStr("key"))
                ).synthesize(tts_request())
            ]
        assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("partial", [False, True])
async def test_actual_sarvam_to_rumik_fallback_and_no_replay_after_audio(partial: bool) -> None:
    seen: list[httpx.Request] = []
    bodies: list[Body] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.sarvam.ai":
            if not partial:
                return httpx.Response(503, text="private")
            body = Body(b"aa" * 3000, error=True)
        else:
            body = Body()
        bodies.append(body)
        return httpx.Response(200, headers={"content-type": "audio/pcm"}, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        router = TTSRouter(
            (
                SpeechSlot[TTSProvider](SarvamTTSProvider(client, SecretStr("key"))),
                SpeechSlot[TTSProvider](RumikTTSProvider(client, SecretStr("key"))),
            ),
            policy=SpeechRouterPolicy(max_attempts=1),
        )
        request = tts_request()
        if partial:
            events = []
            with pytest.raises(SpeechError, match="unavailable"):
                async for event in router.synthesize(request):
                    events.append(event)
            assert any(isinstance(event, AudioEvent) for event in events)
            assert len(seen) == 1
        else:
            events = [event async for event in router.synthesize(request)]
            assert isinstance(events[-1], SynthesisCompleted)
            assert len(seen) == 2
            assert json.loads(seen[0].content)["text"] == json.loads(seen[1].content)["text"]
            assert seen[0].headers["X-Request-ID"] == seen[1].headers["X-Request-ID"]
        assert all(body.closed for body in bodies)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["timeout", "cancel", "close", "pause"])
async def test_http_cleanup_and_consumer_pause(mode: str) -> None:
    body = Body(delay=0.1 if mode == "timeout" else 0)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"content-type": "audio/pcm"}, stream=body)
        )
    ) as client:
        source = TextToSpeechRuntime(RumikTTSProvider(client, SecretStr("key"))).synthesize(
            tts_request(0.02)
        )
        async with aclosing(source):
            if mode == "timeout":
                with pytest.raises(SpeechError, match="timeout"):
                    await anext(source)
            else:
                await anext(source)
                if mode == "cancel":
                    body.delay = 0.1
                    task = asyncio.create_task(anext(source))
                    await asyncio.sleep(0)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif mode == "pause":
                    await asyncio.sleep(0.03)  # Provider must not cancel unrelated consumer work.
                    with pytest.raises(SpeechError, match="timeout"):
                        await anext(source)
        assert body.closed and not client.is_closed


class FakeSocket:
    def __init__(self, *, frames: list[Any] | None = None, send_error: bool = False) -> None:
        self.queue: asyncio.Queue[str | bytes] = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.send_error = send_error
        self.frames = frames
        self.queue.put_nowait(
            json.dumps({"event": "session.begin", "request_id": "vendor-session"})
        )

    async def send(self, message: str) -> None:
        data = json.loads(message)
        self.sent.append(data)
        if data["event"] == "audio_input" and self.send_error:
            raise OSError("private credential")
        if data["event"] == "audio_input" and len(self.sent) == 2 and self.frames is None:
            self.queue.put_nowait(
                json.dumps({"event": "transcript.partial", "utterance_idx": 0, "text": "Hel"})
            )
        if data["event"] == "end":
            frames = (
                self.frames
                if self.frames is not None
                else [
                    {"event": "transcript.final", "utterance_idx": 0, "text": "Hello"},
                    {"event": "session.end", "request_id": "vendor-session", "total_utterances": 1},
                ]
            )
            for frame in frames:
                self.queue.put_nowait(
                    frame if isinstance(frame, (str, bytes)) else json.dumps(frame)
                )

    async def recv(self) -> str | bytes:
        return await self.queue.get()


class FakeConnector:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket
        self.url = ""
        self.headers: dict[str, str] = {}

    @asynccontextmanager
    async def __call__(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> AsyncGenerator[Socket, None]:
        self.url, self.headers = url, headers
        try:
            yield self.socket
        finally:
            self.socket.closed = True


@pytest.mark.asyncio
async def test_sarvam_bidirectional_wire_and_completion() -> None:
    socket = FakeSocket()
    connector = FakeConnector(socket)
    request = stt_request()
    events = [
        event
        async for event in SpeechToTextRuntime(
            SarvamSTTProvider(SecretStr("key"), connector=connector)
        ).transcribe(request, fixture_audio())
    ]
    assert isinstance(events[0], TranscriptEvent) and not events[0].is_final
    assert isinstance(events[1], TranscriptEvent) and events[1].is_final
    assert events[0].segment_id == events[1].segment_id
    assert events[-1] == TranscriptionCompleted(final_segments=1, input_samples=960)
    assert connector.headers["X-Request-ID"] == str(request.request_id)
    assert "endpointing=manual" in connector.url and "encoding=linear16" in connector.url
    assert connector.headers["Api-Subscription-Key"] == "key"
    assert [item["event"] for item in socket.sent] == [
        "speech_start",
        "audio_input",
        "audio_input",
        "audio_input",
        "speech_end",
        "end",
    ]
    assert (
        sum(len(base64.b64decode(item["audio"])) for item in socket.sent if "audio" in item) == 1920
    )
    assert socket.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frames",
    [
        ["broken JSON"],
        [b"binary"],
        ["x" * 262145],
        [{"event": "unknown"}],
        [{"event": "session.end", "request_id": "vendor-session"}],
        [{"event": "transcript.final", "utterance_idx": 1, "text": "Hello"}],
        [{"event": "transcript.final", "utterance_idx": True, "text": "Hello"}],
        [{"event": "transcript.final", "utterance_idx": 0}],
        [
            {"event": "transcript.final", "utterance_idx": 0, "text": "Hello"},
            {"event": "session.end", "request_id": "wrong"},
        ],
        [
            {"event": "transcript.final", "utterance_idx": 0, "text": "Hello"},
            {"event": "transcript.final", "utterance_idx": 0, "text": "duplicate"},
        ],
    ],
)
async def test_stt_malformed_frames(frames: list[Any]) -> None:
    socket = FakeSocket(frames=frames)
    with pytest.raises(SpeechError, match="invalid_output"):
        _ = [
            event
            async for event in SpeechToTextRuntime(
                SarvamSTTProvider(SecretStr("key"), connector=FakeConnector(socket))
            ).transcribe(stt_request(), fixture_audio())
        ]
    assert socket.closed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, code",
    [(401, "provider_error"), (408, "timeout"), (429, "rate_limited"), (503, "unavailable")],
)
async def test_stt_wire_errors(status: int, code: str) -> None:
    socket = FakeSocket(
        frames=[
            {
                "event": "error",
                "code": "vendor",
                "is_fatal": True,
                "message": "private",
                "status_code": status,
            }
        ]
    )
    with pytest.raises(SpeechError) as failure:
        _ = [
            event
            async for event in SpeechToTextRuntime(
                SarvamSTTProvider(SecretStr("key"), connector=FakeConnector(socket))
            ).transcribe(stt_request(), fixture_audio())
        ]
    assert failure.value.code == code and "private" not in str(failure.value)
    assert socket.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["send-error", "timeout", "cancel", "close"])
async def test_stt_task_cleanup(mode: str) -> None:
    socket = FakeSocket(
        frames=[] if mode in {"timeout", "cancel"} else None, send_error=mode == "send-error"
    )
    source = SpeechToTextRuntime(
        SarvamSTTProvider(SecretStr("key"), connector=FakeConnector(socket))
    ).transcribe(stt_request(0.02), fixture_audio())
    async with aclosing(source):
        if mode == "send-error":
            with pytest.raises(SpeechError, match="unavailable"):
                await anext(source)
        elif mode == "timeout":
            with pytest.raises(SpeechError, match="timeout"):
                await anext(source)
        elif mode == "cancel":
            task = asyncio.create_task(anext(source))
            await asyncio.sleep(0.005)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            await anext(source)
    assert socket.closed


@pytest.mark.asyncio
async def test_confirmed_silence_session_does_not_fabricate_text() -> None:
    socket = FakeSocket(
        frames=[{"event": "session.end", "request_id": "vendor-session", "total_utterances": 0}]
    )
    events = [
        event
        async for event in SpeechToTextRuntime(
            SarvamSTTProvider(SecretStr("key"), connector=FakeConnector(socket))
        ).transcribe(stt_request(), fixture_audio())
    ]
    assert events == [TranscriptionCompleted(final_segments=0, input_samples=960)]


@pytest.mark.asyncio
async def test_real_websocket_connector_against_local_sarvam_wire_server() -> None:
    observed: list[dict[str, Any]] = []
    finished = asyncio.Event()

    async def handle(socket: ServerConnection) -> None:
        try:
            assert socket.request is not None
            assert socket.request.headers["Api-Subscription-Key"] == "test-key"
            await socket.send(json.dumps({"event": "session.begin", "request_id": "local"}))
            async for raw in socket:
                message = json.loads(raw)
                observed.append(message)
                if message["event"] == "end":
                    await socket.send(
                        json.dumps(
                            {"event": "transcript.final", "utterance_idx": 0, "text": "Hello"}
                        )
                    )
                    await socket.send(
                        json.dumps(
                            {"event": "session.end", "request_id": "local", "total_utterances": 1}
                        )
                    )
                    break
            await socket.wait_closed()
        finally:
            finished.set()

    async with serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        @asynccontextmanager
        async def connector(
            url: str, headers: dict[str, str], timeout: float
        ) -> AsyncGenerator[Socket, None]:
            async with websocket_connector(f"ws://127.0.0.1:{port}", headers, timeout) as socket:
                yield socket

        events = [
            event
            async for event in SpeechToTextRuntime(
                SarvamSTTProvider(SecretStr("test-key"), connector=connector)
            ).transcribe(stt_request(), fixture_audio())
        ]
        assert events[-1] == TranscriptionCompleted(final_segments=1, input_samples=960)
        await asyncio.wait_for(finished.wait(), 1)
    assert sum(item["event"] == "audio_input" for item in observed) == 3
