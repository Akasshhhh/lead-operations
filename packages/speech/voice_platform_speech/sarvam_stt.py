"""One manually delimited Sarvam realtime utterance per request-owned socket."""

import asyncio
import base64
import json
from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from typing import Protocol
from urllib.parse import urlencode

from pydantic import SecretStr
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, SecurityError

from .contracts import (
    AudioChunk,
    TranscriptEvent,
    TranscriptionCompleted,
    TranscriptionEvent,
    TranscriptionRequest,
)
from .http_tts import LANGUAGES, status_code
from .provider import SpeechError, SpeechStream
from .runtime import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS


class Socket(Protocol):
    async def send(self, message: str) -> None: ...
    async def recv(self) -> str | bytes: ...


class Connector(Protocol):
    def __call__(
        self, url: str, headers: dict[str, str], timeout: float
    ) -> AbstractAsyncContextManager[Socket]: ...


class _Connect(connect):
    def process_redirect(self, exc: Exception) -> Exception | str:
        # No authorization header may follow a vendor redirect.
        if isinstance(exc, InvalidStatus) and 300 <= exc.response.status_code < 400:
            return SecurityError("speech redirects are disabled")
        return super().process_redirect(exc)


@asynccontextmanager
async def websocket_connector(
    url: str, headers: dict[str, str], timeout: float
) -> AsyncGenerator[Socket, None]:
    async with _Connect(
        url,
        additional_headers=headers,
        open_timeout=timeout,
        close_timeout=1,
        max_size=262144,
        max_queue=1,
        proxy=None,
    ) as socket:
        yield socket


async def _recv(socket: Socket, sender: asyncio.Task[None]) -> str | bytes:
    receiver = asyncio.create_task(socket.recv())
    try:
        if not sender.done():
            await asyncio.wait({receiver, sender}, return_when=asyncio.FIRST_COMPLETED)
        if sender.done():
            sender.result()  # Surface input/send errors while recv is waiting.
        return await receiver
    finally:
        if not receiver.done():
            receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)


class SarvamSTTProvider:
    name = "sarvam-stt"
    model = "saaras:v3-realtime"

    def __init__(self, key: SecretStr, *, connector: Connector = websocket_connector) -> None:
        if not key.get_secret_value() or any(
            ord(char) < 33 or ord(char) > 126 for char in key.get_secret_value()
        ):
            raise ValueError("speech API key is required")
        self._key = key
        self.connector = connector

    def accepts(self, request: TranscriptionRequest) -> bool:
        return request.audio_format.sample_rate in {8000, 16000} and request.language in LANGUAGES

    async def transcribe(
        self, request: TranscriptionRequest, audio: SpeechStream[AudioChunk]
    ) -> AsyncGenerator[TranscriptionEvent, None]:
        if not self.accepts(request):
            raise SpeechError("invalid_input", request_id=request.request_id, provider=self.name)
        samples = chunks = 0
        final = partial = False
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        url = "wss://api.sarvam.ai/speech-to-text-realtime/ws?" + urlencode(
            {
                "model": self.model,
                "language_code": {"en": "en-IN", "hi": "hi-IN"}.get(
                    request.language, request.language
                ),
                "sample_rate": request.audio_format.sample_rate,
                "encoding": "linear16",
                "endpointing": "manual",
                "stream_type": "balanced",
                "mode": "transcribe",
                "return_timestamps": "false",
            }
        )
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout_at(deadline):
                    socket = await stack.enter_async_context(
                        self.connector(
                            url,
                            {
                                "Api-Subscription-Key": self._key.get_secret_value(),
                                "X-Request-ID": str(request.request_id),
                            },
                            request.timeout_seconds,
                        )
                    )

                async def send() -> None:
                    nonlocal samples, chunks
                    await socket.send(json.dumps({"event": "speech_start"}))
                    async for raw in audio:
                        chunk = AudioChunk.model_validate(raw)
                        if chunk.sequence != chunks:
                            raise SpeechError(
                                "invalid_input",
                                request_id=request.request_id,
                                provider=self.name,
                            )
                        chunks += 1
                        samples += len(chunk.data) // 2
                        if (
                            chunks > 10000
                            or samples * 2 > MAX_AUDIO_BYTES
                            or samples > MAX_AUDIO_SECONDS * request.audio_format.sample_rate
                        ):
                            raise SpeechError(
                                "invalid_input",
                                request_id=request.request_id,
                                provider=self.name,
                            )
                        await socket.send(
                            json.dumps(
                                {
                                    "event": "audio_input",
                                    "audio": base64.b64encode(chunk.data).decode("ascii"),
                                }
                            )
                        )
                    if not samples:
                        raise SpeechError(
                            "invalid_input", request_id=request.request_id, provider=self.name
                        )
                    await socket.send(json.dumps({"event": "speech_end"}))
                    await socket.send(json.dumps({"event": "end"}))

                async def timed_send() -> None:
                    async with asyncio.timeout_at(deadline):
                        await send()

                sender = asyncio.create_task(timed_send())
                session: str | None = None
                try:
                    for _ in range(1000):
                        async with asyncio.timeout_at(deadline):
                            raw = await _recv(socket, sender)
                        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 262144:
                            raise ValueError("invalid STT frame")
                        data = json.loads(raw, parse_constant=lambda _: None)
                        if not isinstance(data, dict):
                            raise ValueError("invalid STT event")
                        event = data.get("event")
                        if event == "error":
                            status = data.get("status_code")
                            code = status_code(status) if type(status) is int else "provider_error"
                            raise SpeechError(
                                code, request_id=request.request_id, provider=self.name
                            )
                        if event == "session.begin":
                            if (
                                session is not None
                                or not isinstance(data.get("request_id"), str)
                                or not data["request_id"]
                            ):
                                raise ValueError("invalid session identity")
                            session = data["request_id"]
                        elif session is None:
                            raise ValueError("missing session begin")
                        elif event in {"transcript.partial", "transcript.final"}:
                            if (
                                final
                                or type(data.get("utterance_idx")) is not int
                                or data["utterance_idx"] != 0
                            ):
                                raise ValueError("unexpected utterance")
                            text = data.get("text")
                            if not isinstance(text, str):
                                raise ValueError("missing transcript text")
                            is_final = event == "transcript.final"
                            if is_final:
                                async with asyncio.timeout_at(deadline):
                                    await sender
                                final = True
                            if not text.strip():
                                if is_final and partial:
                                    raise ValueError("unresolved partial")
                                continue
                            partial = True
                            # Coverage of this manually supplied utterance, not word alignment.
                            yield TranscriptEvent(
                                segment_id=f"sarvam_{request.utterance_id.hex}_0",
                                segment_index=0,
                                text=text,
                                is_final=is_final,
                                start_sample=0,
                                end_sample=samples,
                            )
                        elif event == "session.end":
                            async with asyncio.timeout_at(deadline):
                                await sender
                            if data.get("request_id") != session or (
                                not final and (partial or data.get("total_utterances") != 0)
                            ):
                                raise ValueError("incomplete STT session")
                            break
                        else:
                            raise ValueError("unexpected STT event")
                    else:
                        raise ValueError("too many STT events")
                finally:
                    if not sender.done():
                        sender.cancel()
                    await asyncio.gather(sender, return_exceptions=True)
            yield TranscriptionCompleted(final_segments=int(partial), input_samples=samples)
        except TimeoutError:
            raise SpeechError(
                "timeout", request_id=request.request_id, provider=self.name
            ) from None
        except SpeechError:
            raise
        except InvalidStatus as exc:
            raise SpeechError(
                status_code(exc.response.status_code),
                request_id=request.request_id,
                provider=self.name,
            ) from None
        except ConnectionClosed as exc:
            code = "unavailable" if exc.rcvd and exc.rcvd.code == 1011 else "provider_error"
            raise SpeechError(code, request_id=request.request_id, provider=self.name) from None
        except OSError:
            raise SpeechError(
                "unavailable", request_id=request.request_id, provider=self.name
            ) from None
        except ValueError:
            raise SpeechError(
                "invalid_output", request_id=request.request_id, provider=self.name
            ) from None
        except Exception:
            raise SpeechError(
                "provider_error", request_id=request.request_id, provider=self.name
            ) from None
