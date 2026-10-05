# Architecture Decisions

## ADR-001: Pipecat for real-time voice orchestration

Pipecat owns audio frames, turn detection, streaming STT/LLM/TTS integration,
transport lifecycle, interruption, and cancellation. Business logic remains in
domain services so the voice runtime stays thin.

## ADR-002: Redis Streams initially

Redis Streams provide asynchronous event delivery, consumer groups, replay within
retention, and
simple local operations. An `EventBus` abstraction keeps Kafka as a future
option if throughput or replay requirements change.

## ADR-003: PostgreSQL as durable source of truth

PostgreSQL owns business state, transcripts, event outbox records, and
workflow actions. The evaluation schema is retained as a foundation; current
evaluation artifacts are local JSON reports. Redis is never the authoritative store.

## ADR-004: Provider routers inside Voice Runtime initially

Keeping streaming routers in the runtime avoids an additional network hop and
reduces audio-path failure modes. The routers have independent interfaces,
tests, health state, and circuit breakers so they can later be extracted.

## ADR-005: Local development without external provider credentials

Deterministic mock providers will support local development, integration tests,
and CI. Real provider adapters remain optional and are enabled only through
environment configuration.

## ADR-006: Docker Compose before orchestration platforms

The expected development and portfolio workload does not justify Kubernetes.
Compose provides reproducible local infrastructure while preserving service
boundaries and independent process lifecycles.

## ADR-007: Internal REST between API Gateway and Lead Service

The API Gateway communicates with the Lead Service through internal REST using
`httpx`. REST matches the current request/response shape, is straightforward to
inspect and contract-test, and avoids introducing protobuf generation or a
second RPC stack before the real-time voice path exists.

The Gateway is the only host-published application API. The Lead Service is
reachable only on the Compose network. Requests carry `X-Request-ID` for
correlation and `X-Service-Token` for local service authentication.

The token is a development mechanism, not a complete production identity
system. Production startup requires a configured token. The client boundary is
isolated so mTLS, JWT workload identity, or a service mesh can replace the
shared-secret check without changing public routes or domain ownership.

## ADR-008: Module 5 delivery, retry, and retention policy

Approved during Module 5 clarification: at-least-once delivery, five configurable
attempts, exponential backoff with jitter, consumer dead letters, and explicit
operator replay. Exhausted outbox rows remain in PostgreSQL. Stream entries are
retained until safe retention is implemented; unconditional MAXLEN trimming could
discard pending work. The tradeoff is growing Redis storage, visible through the
inspection command. PostgreSQL history supports operator recovery from Redis loss.

Only the currently needed domain stream and its DLQ are created. Per-aggregate
publication order uses PostgreSQL row locks and a lower-unpublished-version check.
A Redis distributed mutex would add another failure dependency without improving
this transaction boundary. Concurrent consumer completion is not globally ordered.
The Redis adapter uses XPENDING/XCLAIM to apply per-entry retry delay, equivalent
to XAUTOCLAIM recovery with explicit visibility checks.

## ADR-009: One active conversation per lead and Lead-authoritative recovery

The Module 6 business invariant is intentional: one nonterminal conversation per
lead, enforced by a PostgreSQL partial unique index, and one active call per
conversation. Serial live qualification flows avoid competing ownership of one
Lead profile. Historical conversations/calls remain available; call connection
state is independent of business conversation completion.

Conversation Service commits transcript input before its synchronous Lead call.
Only Lead Service calculates and persists qualification scores. During Lead
unavailability, qualification-dependent orchestration pauses and APIs return 503
without synthesizing or substituting a score. Persisted conversation/history
reads remain available. Replay with the original turn identity recovers ambiguous
partial success; later turns and replacement conversations wait for pending work.
A definitive validation rejection uses the existing `FAILED` turn status and
permits corrected input under a new turn ID.

Transcript retention excludes terminal conversations with pending turns. The
final verification adds fingerprints and locking fixes within existing records
and transactions; it preserves the database models, state graphs, and version 1
event envelope. See [`modules-1-7-verification.md`](modules-1-7-verification.md).

## ADR-010: Reduced Modules 8–18 product scope

The user-approved remaining roadmap supersedes the original 32-module plan:
provider interfaces and A/B routers, Pipecat browser voice, tool-driven live
qualification/scoring, lightweight policies/handoff/follow-up, one dashboard,
useful observability, E2E evaluation, and final hardening.

Lead/Conversation ownership and the Redis Streams/outbox guarantees remain.
Pipecat owns media lifecycle; provider routers stay embedded in Voice Runtime;
business logic stays in domain services. Lightweight workflow execution uses
existing service/worker boundaries. Evaluation is a test/scenario capability,
and the Gateway serves one central product UI.

Separate Analytics/Evaluation/Workflow services, generalized workflow/evaluation
platforms, complex read models, Kafka, Kubernetes, workload-identity infrastructure,
external business integrations, sophisticated multilingual routing, specialized
STT variants, automatic retention, multiple dashboards, and elaborate load-test
infrastructure are deferred. Existing foundational tables/migrations are retained.
The exact per-module scope, verification gates, and conflict-stop rule are in
[`implementation-plan.md`](implementation-plan.md). Module 8 is now complete.

## ADR-011: Module 8 stateless LLM boundary and complete tool proposals

A shared Python library supplies provider-independent generation and streaming
contracts, a stateless runtime adapter, and deterministic local fixtures. Trusted
instructions are separate from history/tool results; callers explicitly supply
context from existing durable records. No second memory or transcript store is
introduced. Request/conversation/turn IDs correlate calls without creating a new
business idempotency mechanism.

Adapters normalize vendor fragments into text deltas and complete JSON tool
proposals. The runtime validates tool names/IDs, bounds, finish consistency, EOF,
and a total generation deadline. Partial events remain provisional until validated
completion; tool execution and authoritative domain validation are Module 13.
Cancellation propagates and closes request-owned provider resources. Real
providers, routing, and retries are Module 9. See [`module-8.md`](implementation-plan.md).

## ADR-012: OpenAI/OpenRouter adapters and safe partial-output recovery

Module 9 uses OpenAI and OpenRouter (Llama 3.3 70B Instruct by default), following
the user's provider preference. Both adapters use existing HTTPX behind Module 8
contracts. OpenRouter's content-free repeated terminal usage frame is normalized
explicitly; strict validation remains for all content, tools, completion, and EOF.
Its internal provider fallback is disabled so runtime attempts remain visible.

The router has request-local attribution and unchanged full context. Collected
generation buffers/discards incomplete attempts before retry/failover. Live streams
cannot retry or fail over once output is exposed, preventing duplicate text/tool
proposals. No tool execution or durable effects occur inside this boundary.

Circuit state and capability selection live in the runtime process. Half-open
probes are exclusive; epoch guards protect recovery from stale in-flight results.
Cancellation releases request resources and probe admission without a failure
count. The existing provider-health table and domain services remain unchanged.
Configuration, verification limits, and later integration: [`module-9.md`](implementation-plan.md).

## ADR-013: Speech contracts preserve domain ownership and explicit audio formats

Module 10 introduces a separate dependency-free speech library beside the LLM
library. Raw mono signed 16-bit little-endian PCM, explicit sample rates, bounded
utterance streams, and sample-relative offsets avoid vendor codecs/session state
inside domain contracts. STT interim revisions are provisional; finals are
immutable within their stream. TTS emits ordered audio for one supplied text.
Terminal success requires valid accounting and EOF. Cancellation closes owned
streams; partial audio/text never triggers an implicit replay.

Conversation remains the only transcript/call owner; Lead remains the scoring
authority. Mocks use scripted text and deterministic tones, without recognition
or synthesis claims. Real adapters/routing are Module 11; Pipecat, playback,
VAD, interruption and durable mapping are Module 12. The user's TTS candidates
are OpenAI Realtime or Sarvam/Rumik. A Realtime adapter must first demonstrate
that it preserves the separate LLM-to-TTS boundary; an end-to-end agent is not
approved by this provider preference. Details: [`module-10.md`](module-10.md).

## ADR-014: Sarvam/Rumik speech paths and recovery before output only

Module 11 shares Sarvam realtime STT across two TTS paths (Sarvam and Rumik),
following the user's TTS alternatives and avoiding multiple specialized STT
implementations. Request-scoped HTTP PCM TTS fits the complete-text input contract;
Sarvam STT uses a manually bounded utterance/socket. Audio format, text limits and
configured voice capabilities are explicit. OpenAI Realtime is deferred because
it is a response-generating interface and an exact separate text-to-audio boundary
was not established; the OpenAI/OpenRouter LLM layer remains unchanged.

Speech recovery never replays exposed text/audio. Before exposure, bounded STT
input replay retains consumed frames without a durable audio store. An interrupted
input read is unsafe to resume and fails rather than silently truncate. Provider
health/circuits remain process-local with exclusive probes and epoch guards;
domain state is unaffected. Pipecat/playback/VAD and durable call/transcript
integration remain Module 12. Details: [`module-11.md`](module-11.md).

## ADR-015: Durable domain boundaries before media effects

Modules 12–14 preserve Lead scoring ownership and Conversation persistence. User
input is staged durably; validated confirmed tool facts are frozen before Lead
application. Agent messages persist idempotently before TTS. Ambiguous failures
recover using the same identities, without replaying exposed audio or fabricating
scores. Workflow intent and acknowledgement recovery live in Conversation, not a
new service. The state graphs and schema remain unchanged.

## ADR-016: Session diagnostics and repository evaluation

Modules 15–17 expose safe operational metadata through the existing Gateway and
same-origin dashboard proxy. Provider/circuit/fault state stays process-local;
PostgreSQL remains business truth. Capability-protected, opt-in local faults
exercise existing failures without changing domain decisions. Evaluation runs
selected deterministic regression scenarios and emits bounded content-free JSON;
skips/errors fail the gate. It does not claim paid-provider quality or implement
live-call scoring in the foundational evaluation tables.

## ADR-017: Readiness and least-privilege local containers

Module 18 uses existing authenticated health contracts for startup readiness and
runs Python images/migrations as a dedicated UID 10001. Dashboard remains `node`.
Readiness is scoped: domain DB access, Gateway Lead access, dashboard page serving.
No new distributed health state or automatic outage controller is introduced.
A scripted Chrome demo reuses the verified product scenario. Isolated fresh
Compose volumes verify clean startup and destructive recovery tests without
resetting operator data. Production identity and paid/media smoke tests remain
explicitly outside these verification claims.
