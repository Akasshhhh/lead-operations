# Module 10 — STT/TTS provider interfaces

## Scope and ownership

Module 10 introduced `packages/speech/voice_platform_speech` with vendor-independent speech
contracts, closable asynchronous STT/TTS protocols, stateless validation adapters,
deterministic text scripts, and PCM fixture audio. It adds no dependency, network
client, credentials, route, service, migration, business tool, or persistence.
Module 11 subsequently extends the library with real adapters/routing; see
[`module-11.md`](module-11.md). The statements below describe Module 10's scope.

Conversation Service continues to own transcript/message persistence and call
state. Lead Service remains authoritative for qualification/scoring. Pipecat,
transport, turn detection/VAD, playback, interruption control, and service
integration belong to Module 12. These speech streams are library interfaces;
they do not constitute a working browser call or a Pipecat processor yet.

## Contracts

| Contract | Meaning |
|---|---|
| `AudioFormat` | Raw mono signed 16-bit little-endian PCM, explicit sample rate: 8000, 16000, 22050, 24000, or 48000 Hz; default 16000 |
| `AudioChunk` | Immutable bytes, zero-based contiguous sequence, 2–65536 bytes, complete samples; audio excluded from repr |
| `TranscriptionRequest` | Request/conversation/call/utterance UUIDs, input format, language hint, total deadline |
| `TranscriptEvent` | Stable segment ID/index, replaceable interim or immutable final text, sample offsets, optional confidence |
| `TranscriptionCompleted` | Final segment count and consumed sample count; emitted only after input EOF and valid provider EOF |
| `SynthesisRequest` | Same correlation IDs, requested output format, complete text, language hint, optional voice identifier, total deadline |
| `AudioEvent` | One PCM output chunk in the requested format |
| `SynthesisCompleted` | Exact output sample count, emitted only after provider EOF and resource closure |

All models reject unknown fields and mutation. Requests retain explicit identity;
the library does not generate business turn IDs or implement durable idempotency.
An utterance ID identifies one bounded speech operation. STT segment IDs are
utterance-scoped; future persistence must map them into the existing Conversation
contracts rather than treating local segment indexes as database sequence numbers.

Sample offsets are relative to the utterance's declared format, not wall-clock
timestamps. `seconds = samples / sample_rate`. Frames contain raw PCM without
WAV headers, compressed codecs, implicit resampling, or codec conversion. A future
adapter must negotiate/reject unsupported formats and normalize vendor audio to
the declared format. A language hint does not select a multilingual routing policy.

Text is valid UTF-8, nonblank, NUL-free, and at most 20000 characters. Empty final
transcript text is forbidden. A nonempty silence utterance may legitimately finish
with zero transcript segments; no text is fabricated. Empty input audio is an
`invalid_input` failure.

## Streaming and lifecycle

`STTProvider.transcribe(request, audio)` consumes a closable `SpeechStream[AudioChunk]`
and returns a closable transcription stream. `TTSProvider.synthesize(request)`
returns a closable audio stream. Providers expose `name` and `model`; the concrete
runtime instance identifies which provider produced its events. Vendor session
reuse and resource ownership remain adapter concerns; each request's stream must
close without closing an unrelated caller-owned client.

`SpeechToTextRuntime` validates input as the provider pulls it. It does not drain
or buffer the utterance in advance. This permits an adapter to produce interim
text before audio EOF. EOF means the caller has finished this utterance, not that
the whole call has ended. Only one pending transcript segment exists at a time:
interims replace text under the same ID/index/start offset, finalization closes
that segment, then the next index advances. Final IDs cannot recur, segments
cannot overlap, and offsets cannot exceed already consumed audio. Completion
requires all segments finalized and all input consumed.

`TextToSpeechRuntime` validates contiguous chunks and matching terminal sample
accounting without buffering audio. Its input is one complete text payload, with
streaming audio output. Incremental LLM-to-speech sentence handling belongs to
the future runtime; no vendor WebSocket session protocol is exposed here.

Each operation is limited to 120 seconds of audio, 16 MiB, and 10000 chunks.
STT additionally limits output to 1000 events, 200 final segments, and 20000 total
final text characters. Interim text counts against the remaining text budget.
Bounds, input/output sequencing, completion counts, and termination are checked
before terminal success is exposed. Missing completion, unresolved interims,
trailing events, and malformed frames fail explicitly.

One deadline (default 10 seconds, positive and at most 300) covers provider work,
input reads, output reads, EOF validation, and pauses between consumer pulls.
Consumer pauses consume the budget; expiration is observed on the next pull.
Async providers/inputs must cooperate with cancellation. Close operations must be
idempotent and prompt; hostile implementations that block cancellation/cleanup
are outside this cooperative protocol.

Runtime takes ownership of the input stream once STT consumption starts. It closes
input and provider output on success, failure, timeout, cancellation, or explicit
close. Consumers must use `contextlib.aclosing` when they may stop early. Cancelling
an in-flight pull propagates `CancelledError`; early close propagates generator
shutdown. Closing an unstarted generator does not run it or take input ownership.

`SpeechError` exposes only a safe code, original request ID, and provider name:
`timeout`, `unavailable`, `rate_limited`, `invalid_input`, `invalid_output`, or
`provider_error`. Vendor messages, text, audio, and credentials are excluded from
the error message. The transient/retryable flag is guidance, never an automatic
retry. Partial audio/text already exposed remains exposed; neither runtime replays
or rolls it back. A final segment is immutable within a stream, but a later failure
still means the whole operation did not complete successfully.

## Deterministic local path

`MockSTTProvider` drains fixture audio then emits scripted interim/final text; it
does not recognize speech. Stable mock segment IDs derive from the utterance UUID.
An empty scripted text emits zero-segment completion. `MockTTSProvider` emits a
repeatable low-amplitude 400 Hz PCM tone, not spoken words. `fixture_pcm` produces
approximately 20 ms at the requested rate (integer sample count); `fixture_audio`
supplies ordered chunks. Mocks support delay and failure injection after a chosen
number of events. Scripts are immutable, reusable across requests and restarts,
and require no keys. The tone is test audio, not a measure of vendor speech quality.

After `make install`, run:

```python
import asyncio
from contextlib import aclosing
from uuid import uuid4

from voice_platform_speech import (
    AudioFormat,
    MockSTTProvider,
    MockTTSProvider,
    SpeechToTextRuntime,
    SynthesisRequest,
    TextToSpeechRuntime,
    TranscriptionRequest,
    fixture_audio,
)


async def main():
    ids = {name: uuid4() for name in ("request_id", "conversation_id", "call_id", "utterance_id")}
    audio_format = AudioFormat(sample_rate=24000)
    request = TranscriptionRequest(**ids, audio_format=audio_format)
    async with aclosing(
        SpeechToTextRuntime(MockSTTProvider()).transcribe(request, fixture_audio(audio_format))
    ) as events:
        async for event in events:
            print(event.kind)
    synthesis = SynthesisRequest(**ids, audio_format=audio_format, text="Hello")
    async with aclosing(TextToSpeechRuntime(MockTTSProvider()).synthesize(synthesis)) as events:
        async for event in events:
            print(event.kind)


asyncio.run(main())
```

## Verification — 2026-10-04

The credential-free speech gate passed **75 tests**:

```bash
.venv/bin/python -m pytest tests/unit/test_speech_contracts.py tests/integration/test_speech_runtime.py
```

Contract tests cover invalid formats, PCM alignment/bounds, explicit rates,
immutable correlation, Unicode/text/deadline validation and deterministic tone
fixtures. Runtime integration tests cover concurrent/restarted scripts, silence,
incremental input/backpressure, interim replacement, incomplete/duplicate/trailing
events, malformed audio, total text/audio/event bounds, partial failure, input
and provider deadlines, EOF timeout, redacted exceptions, early close, cancellation
during input/output, and resource closure before terminal success.

The full suite passed **336 tests with one expected opt-in Compose skip** (337
collected; event-stack deployment tests excluded), using a fresh disposable
PostgreSQL database and isolated Redis keys. The database was removed afterward.
Migration roundtrip/rebuild/preservation/drift checks passed at unchanged head
`e8f2a6b3c901`. Ruff format/lint, strict mypy (**94 source files**), `pip check`,
Compose configuration and whitespace checks passed. The local example above also
ran successfully. No live speech calls, browser audio, new wheel build or Compose
image/deployment rebuild was performed; Modules 1–7 deployment evidence remains
historical. This verifies contracts/mock integration, not vendor speech quality.

## Provider preferences and deferred integration

The user requested OpenAI Realtime or Sarvam/Rumik for TTS. These are candidates
for Module 11, not three implemented paths or a finalized selection. The LLM
choice remains OpenAI plus OpenRouter. STT vendor selection is still open.

[OpenAI Realtime](https://platform.openai.com/docs/api-reference/realtime) specifies
24 kHz PCM. It generates spoken responses; Module 11 must verify preservation of
the independently generated LLM text before treating it as a TTS adapter. Switching
to an end-to-end speech agent would change the approved pipeline and requires an
explicit architectural decision. No such redesign is implemented in Module 10.

[Sarvam's streaming TTS](https://docs.sarvam.ai/api-reference/text-to-speech/stream)
documents 22050/24000 Hz defaults and completion events. [Rumik Silk](https://rumik.ai/silk-api)
advertises streaming TTS and 24 kHz output. Adapter-specific model/voice limits,
codecs, session cancellation, API access, latency, and exact completion semantics
must be verified in Module 11. Current mocks do not prove paid-provider support.

Real providers, capability selection, retry/failover and health are Module 11.
Pipecat/browser transport, barge-in, playback-buffer flushing, call integration,
and durable transcript mapping are Module 12. Business tool execution is Module
13. No later module is implemented in this pass.
