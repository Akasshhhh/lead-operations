"""Request-scoped HTTP PCM adapters with caller-owned HTTPX clients."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

import httpx
from pydantic import SecretStr

from .contracts import AudioChunk, AudioEvent, SynthesisCompleted, SynthesisEvent, SynthesisRequest
from .provider import ErrorCode, SpeechError
from .runtime import MAX_AUDIO_BYTES, MAX_AUDIO_SECONDS

LANGUAGES = frozenset({"en", "en-IN", "hi", "hi-IN"})


def status_code(status: int) -> ErrorCode:
    if status == 429:
        return "rate_limited"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "unavailable"
    return "provider_error"


class HTTPStreamingTTS:
    name: str
    endpoint: str
    model: str

    def __init__(self, client: httpx.AsyncClient, key: SecretStr) -> None:
        if not key.get_secret_value() or any(
            ord(char) < 33 or ord(char) > 126 for char in key.get_secret_value()
        ):
            raise ValueError("speech API key is required")
        self.client = client
        self._key = key

    def accepts(self, request: SynthesisRequest) -> bool:
        return request.audio_format.sample_rate == 24000 and request.language in LANGUAGES

    def payload(self, request: SynthesisRequest) -> dict[str, object]:
        raise NotImplementedError

    def headers(self) -> dict[str, str]:
        raise NotImplementedError

    async def synthesize(self, request: SynthesisRequest) -> AsyncGenerator[SynthesisEvent, None]:
        if not self.accepts(request):
            raise SpeechError("invalid_input", request_id=request.request_id, provider=self.name)
        samples = sequence = 0
        carry = b""
        deadline = asyncio.get_running_loop().time() + request.timeout_seconds
        try:
            async with AsyncExitStack() as stack:
                async with asyncio.timeout_at(deadline):
                    response = await stack.enter_async_context(
                        self.client.stream(
                            "POST",
                            self.endpoint,
                            json=self.payload(request),
                            headers=self.headers() | {"X-Request-ID": str(request.request_id)},
                            timeout=request.timeout_seconds,
                            follow_redirects=False,
                        )
                    )
                if response.status_code != 200:
                    raise SpeechError(
                        status_code(response.status_code),
                        request_id=request.request_id,
                        provider=self.name,
                    )
                # No compressed/container decoder or implicit sample-rate conversion.
                content_type = response.headers.get("content-type", "").lower()
                if content_type.split(";", 1)[0].strip() not in {
                    "audio/pcm",
                    "application/octet-stream",
                }:
                    raise ValueError("expected raw PCM")
                parameters = dict(
                    part.strip().split("=", 1)
                    for part in content_type.split(";")[1:]
                    if "=" in part
                )
                if any(
                    parameters.get(key, value) != value
                    for key, value in {
                        "rate": "24000",
                        "channels": "1",
                        "format": "s16le",
                    }.items()
                ):
                    raise ValueError("incompatible PCM metadata")
                source = response.aiter_bytes(chunk_size=4096)
                while True:
                    async with asyncio.timeout_at(deadline):
                        try:
                            raw = await anext(source)
                        except StopAsyncIteration:
                            break
                    data = carry + raw
                    aligned = len(data) - len(data) % 2
                    carry = data[aligned:]
                    if aligned:
                        samples += aligned // 2
                        if samples * 2 > MAX_AUDIO_BYTES or samples > MAX_AUDIO_SECONDS * 24000:
                            raise ValueError("excessive audio")
                        yield AudioEvent(chunk=AudioChunk(sequence=sequence, data=data[:aligned]))
                        sequence += 1
                if carry or not samples:
                    raise ValueError("truncated or empty PCM")
            # HTTP body EOF and response closure precede success.
            yield SynthesisCompleted(output_samples=samples)
        except (TimeoutError, httpx.TimeoutException):
            raise SpeechError(
                "timeout", request_id=request.request_id, provider=self.name
            ) from None
        except SpeechError:
            raise
        except httpx.TransportError:
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


class SarvamTTSProvider(HTTPStreamingTTS):
    name = "sarvam-tts"
    endpoint = "https://api.sarvam.ai/text-to-speech/stream"
    model = "bulbul:v3"

    def __init__(self, client: httpx.AsyncClient, key: SecretStr, *, voice: str = "shubh") -> None:
        super().__init__(client, key)
        self.voice = voice

    def accepts(self, request: SynthesisRequest) -> bool:
        return (
            super().accepts(request)
            and len(request.text) <= 3500
            and request.voice in {None, self.voice}
        )

    def headers(self) -> dict[str, str]:
        return {"Api-Subscription-Key": self._key.get_secret_value()}

    def payload(self, request: SynthesisRequest) -> dict[str, object]:
        return {
            "text": request.text,
            "model": self.model,
            "speaker": request.voice or self.voice,
            "language_code": {"en": "en-IN", "hi": "hi-IN"}.get(request.language, request.language),
            "speech_sample_rate": 24000,
            "output_audio_codec": "linear16",
        }


class RumikTTSProvider(HTTPStreamingTTS):
    name = "rumik-tts"
    endpoint = "https://silk-api.rumik.ai/v1/tts"

    def __init__(
        self,
        client: httpx.AsyncClient,
        key: SecretStr,
        *,
        model: str = "mulberry",
        voice: str = "siya",
    ) -> None:
        super().__init__(client, key)
        if model not in {"mulberry", "muga"}:
            raise ValueError("unsupported Rumik model")
        self.model = model
        self.voice = voice

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key.get_secret_value()}"}

    def accepts(self, request: SynthesisRequest) -> bool:
        return (
            super().accepts(request)
            and len(request.text) <= 2000
            and (
                request.voice in {None, self.voice}
                if self.model == "mulberry"
                else request.voice is None
            )
        )

    def payload(self, request: SynthesisRequest) -> dict[str, object]:
        data: dict[str, object] = {"text": request.text, "model": self.model, "audio_format": "pcm"}
        if self.model == "mulberry":
            data.update(
                speaker=request.voice or self.voice,
                description="a clear, calm voice with conversational pacing",
            )
        return data
