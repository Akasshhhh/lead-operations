# Module 11 — Voice providers and router

## Implemented boundary

The existing `packages/speech/voice_platform_speech` package now contains Sarvam
realtime STT, Sarvam HTTP TTS and Rumik HTTP TTS adapters, separate STT/TTS routers,
runtime-local circuit health, bounded recovery, and explicit mock/real settings.
The two real voice paths share Sarvam STT and choose Sarvam or Rumik TTS. A second
specialized STT implementation is not required by the reduced scope.

Module 10's PCM/request/event contracts remain unchanged. No service, HTTP route,
database model, migration, transcript store, business state machine or scoring
implementation changes. Conversation continues to own persistence/call state;
Lead owns qualification/scoring. No Pipecat, browser audio or business tool
execution is implemented. `websockets>=15,<18` is now a direct dependency for STT;
TTS uses the existing HTTPX dependency.

## Adapters and capability limits

| Adapter | Wire path | Implemented capability |
|---|---|---|
| `SarvamSTTProvider` | `wss://api.sarvam.ai/speech-to-text-realtime/ws` | `saaras:v3-realtime`, mono PCM at 8000/16000 Hz, manual utterance boundaries, partial/final text |
| `SarvamTTSProvider` | `https://api.sarvam.ai/text-to-speech/stream` | `bulbul:v3`, explicitly requested raw 24000 Hz PCM, at most 3500 text characters |
| `RumikTTSProvider` | `https://silk-api.rumik.ai/v1/tts` | `mulberry` (default) or `muga`, explicitly requested raw 24000 Hz PCM, at most 2000 text characters |

Official sources: [Sarvam STT protocol](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming),
[Sarvam HTTP TTS](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api/http-stream),
[Rumik TTS schema](https://docs.rumik.ai/api-reference/speech/synthesize-speech),
and [Rumik PCM format](https://docs.rumik.ai/audio-formats).

The demo adapter capabilities accept English/Hindi hints (`en`, `en-IN`, `hi`,
`hi-IN`), without multilingual routing/detection. Sarvam maps short hints to
BCP-47 codes. Rumik infers speech language from supplied text and has no language
field in this TTS request. No text truncation, translation, automatic sentence
splitting or resampling occurs. Unsupported requests skip a router slot or fail
explicitly when an adapter is used directly. Real TTS callers must request
`AudioFormat(sample_rate=24000)`; the shared default 16000 Hz stays compatible
with existing mock/STT contracts.

Voice defaults are `shubh` for Sarvam and `siya` for Rumik Mulberry. Configure a
vendor-supported voice once per adapter. A request may omit voice or specify
that adapter's configured voice; a foreign voice is not silently remapped on
failover. Rumik Muga rejects explicit voice requests and does not send Mulberry's
speaker/description fields. Mulberry supplies a fixed clear/calm conversational
description in addition to its configured speaker. Additional style controls
and automatic voice discovery are outside this module.

Both HTTP adapters disable redirects, forward `X-Request-ID`, and keep the injected
HTTPX client caller-owned. They stream bounded body fragments, reassemble split
16-bit samples, reject empty/odd terminal bytes or incompatible MIME/rate/channel
metadata, enforce Module 10's audio bounds, and close the response before exposing
completion. Raw `audio/pcm` or `application/octet-stream` is accepted under the
explicit PCM request; compressed/container MIME types are rejected. Rumik's HTTP
endpoint returns binary audio; fixture streaming proves the adapter's behavior,
not live time-to-first-byte or whether a vendor buffers synthesis internally.

STT owns one socket per request. It sends `speech_start`, PCM `audio_input` frames,
`speech_end`, then `end`. A concurrent sender and receiver allow interim output
before input EOF. Sender failures wake the receiver; timeout, cancellation and
early close cancel/join pending tasks and close the socket. Redirects are rejected;
production connects only to the fixed Sarvam WSS endpoint. The connector is
injectable for deterministic wire tests, not an end-user endpoint setting.

The adapter requires `session.begin`, one matching utterance index, and a matching
`session.end` after finalized text. Explicit zero-utterance session completion
supports silence without fabricated text. Transcript IDs derive from the
utterance UUID. Sample offsets describe coverage of the caller-supplied manual
utterance (start zero, end consumed samples), not vendor word/speech alignment;
no confidence or precise word timestamps are invented. Later persistence must
map these events through existing Conversation contracts.

HTTP/handshake 429 maps to `rate_limited`, 408 to `timeout`, 5xx to `unavailable`,
and other rejected statuses to `provider_error`. STT error frames use their
HTTP-equivalent status when present; ambiguous vendor codes remain
`provider_error`. STT 1011 socket closes are unavailable; other premature closes
are controlled failures. Errors expose safe codes and original request identity,
never vendor response bodies or close reasons.

## Recovery and health

`STTRouter` and `TTSRouter` use ordered typed `SpeechSlot`s with immutable format,
language and text-size capabilities. Providers may additionally reject voice or
format requests. Configuration defaults to mock mode and never automatically
falls from real providers to mock output.

Both routers are streaming boundaries. They retry transient failures or select
another compatible slot only **before the first exposed event**. Interim text,
final text and audio chunks all stop automatic replay. Partial output failures
propagate without pretending the utterance completed. There is no buffered TTS
mode that could conceal audio already played, and no business idempotency claims.

One original request deadline bounds all attempts and backoff. Per-attempt budgets
are capped by the remaining total budget. Attempts are limited to 1–3 per provider;
backoff doubles from the configured base. If backoff would consume the remaining
budget, the router leaves that budget for a fallback instead. Invalid input does
not count against provider health and never retries.

STT retains a request-local, bounded PCM replay buffer: at most Module 10's 120
seconds/16 MiB/10000 chunks. Before exposed text, another attempt replays the
consumed prefix and then pulls remaining original input. EOF input can be replayed
in full. If cancellation/error interrupts an original input read, its continuity
cannot be proven; the router fails rather than retry a potentially truncated
utterance. Buffers stay in process memory and disappear with the request; no
audio archive/cache/persistence is added. The real configuration has one STT
vendor, so production STT recovery is same-provider retry, not vendor failover.
The protocol/router is tested with two deterministic STT providers.

`health()` returns immutable per-provider snapshots: provider/model, enablement,
circuit state, success/failure/consecutive/failover counts, safe last error and
remaining cooldown. Health is process-local on one event loop; it does not write
the existing provider-health table or Redis. Circuits open after the configured
failure threshold. After cooldown, only one request gets a half-open probe.
Epoch guards prevent an older in-flight success from closing a newer circuit.
Cancelled/closed probes release admission without recording a health failure.
No periodic or paid health probe is introduced. UI/status exposure remains later
module work.

## Configuration and local use

| Environment variable | Default/meaning |
|---|---|
| `SPEECH_MODE` | `mock`; explicit `real` enables adapters |
| `STT_PROVIDERS` | `sarvam`; currently the only implemented real STT |
| `TTS_PROVIDERS` | `sarvam,rumik`; unique ordered selection, either may be omitted |
| `SARVAM_API_KEY`, `RUMIK_API_KEY` | Required only for enabled real providers; never required for mock mode |
| `SARVAM_TTS_VOICE` | `shubh` |
| `RUMIK_TTS_MODEL`, `RUMIK_TTS_VOICE` | `mulberry`, `siya` |
| `SPEECH_MAX_ATTEMPTS` | `2` per provider; allowed 1–3 |
| `SPEECH_ATTEMPT_TIMEOUT_SECONDS` | `4`, positive, at most 300 |
| `SPEECH_RETRY_DELAY_SECONDS` | `0.1`, allowed 0–5, exponential backoff |
| `SPEECH_FAILURE_THRESHOLD` | `3`, allowed 1–20 |
| `SPEECH_COOLDOWN_SECONDS` | `15`, positive, at most 300 |

`SpeechSettings.from_env()` validates configuration with content-free errors and
secret fields excluded from repr. Real default configuration needs both keys;
real `TTS_PROVIDERS=sarvam` needs only Sarvam's key. STT always needs Sarvam in
real mode. External callers own HTTPX clients; library builders do not close them.
No existing service startup is changed to load these settings.

After `make install`, this explicitly credential-free example runs locally:

```python
import asyncio
from contextlib import aclosing
from uuid import uuid4

import httpx
from voice_platform_speech import AudioFormat, SpeechSettings, SynthesisRequest


async def main():
    settings = SpeechSettings.from_mapping({})
    request = SynthesisRequest(
        request_id=uuid4(),
        conversation_id=uuid4(),
        call_id=uuid4(),
        utterance_id=uuid4(),
        text="Hello",
        audio_format=AudioFormat(sample_rate=24000),
    )
    async with httpx.AsyncClient() as client:
        router = settings.build_tts(client)
        async with aclosing(router.synthesize(request)) as events:
            async for event in events:
                print(event.kind)
        print(router.health())


asyncio.run(main())
```

Live smoke verification requires real keys, account/model access and synthetic
input. No paid calls were made during this module. Deterministic test commands
are recorded in `docs/testing.md`.

## Verification — 2026-10-05

**88 new tests passed**: 52 adapter integration cases, 21 router cases and 15
configuration cases. Together with Module 10, the speech suites contain 163
passing cases. Tests use HTTPX `MockTransport`, fragmented PCM responses,
deterministic bidirectional socket fixtures, and a real local WebSocket server
exercising the production connector. They cover wire fields/headers, context and
identity, cancellation/task closure, malformed/partial output, safe status errors,
actual Sarvam-to-Rumik fallback, safe STT prefix/EOF replay, interrupted-input
protection, retries/deadlines, circuit recovery/probe exclusivity/epoch races,
capability selection, safe input-close errors and concurrent requests.

The full regression suite passed **424 tests with one expected opt-in Compose
skip** (425 collected; event-stack deployment tests excluded), using a fresh
disposable PostgreSQL database and isolated Redis keys. The database was removed
afterward. Existing database/Redis recovery and migration roundtrip/rebuild/
preservation/drift checks passed at unchanged head `e8f2a6b3c901`.

Ruff format/lint, strict mypy (**101 source files**), `pip check`, Compose
configuration and Git whitespace checks passed. The explicit mock example above
also ran successfully. Paid vendor calls, browser/Pipecat audio, new wheel builds
and Compose deployment rebuilds were not run. Historical Modules 1–7 deployment
evidence remains unchanged.

## Decisions and deferred work

Sarvam/Rumik honor the user's TTS alternatives while preserving the separate
LLM → TTS boundary. [OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime-conversations)
is a model-generated spoken-response interface. An exact independently generated
text-to-audio guarantee was not established by its documentation, so no Realtime
TTS adapter is implemented. Adopting an end-to-end speech agent would need an
explicit architectural decision. LLM providers remain OpenAI plus OpenRouter.

Persistent TTS WebSocket sessions/incremental text, VAD/turn detection, Pipecat
processors, browser transport, playback-buffer flushing/barge-in, call integration
and durable transcript mapping remain Module 12. Tool execution remains Module
13. No later module starts in this pass.
