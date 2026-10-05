# Implementation Plan

Implementation is strictly sequential. A module is complete only after its
implementation, unit tests, integration tests, failure tests, existing-system
integration checks, and documentation are complete.

Before each module, inspect the implementation, current contracts, table owners,
state machines, migrations, and event guarantees. Preserve completed work. If the
proposed module conflicts with it, **stop**, explain the exact conflict and the
smallest resolution, and await confirmation before coding. Work only on the
current module; advance after its verification and documentation gates pass.

## Approved reduced module order

The remaining roadmap replaces the original 32-module plan. The goal is one
demonstrable browser voice qualification product with provider failover, live
Lead-owned qualification/scoring, and one central dashboard. Modules 1–7 are
implemented and reverified; Modules 8–15 are implemented and verified below.
Modules 16–18 remain planned. Module 15's approved dashboard read APIs and central
frontend gates are complete; Module 16 is unstarted.

1. Repository and development infrastructure
2. PostgreSQL, migrations, and base domain models
3. Lead Service and synthetic data
4. API Gateway
5. Redis infrastructure and Redis Streams Event Bus
6. Conversation Service, live turn ingestion, and state machine
7. Transcript history, search, and retention enhancements
8. LLM provider interfaces
9. LLM Providers A/B and router
10. STT/TTS provider interfaces
11. Voice Providers A/B and router
12. Pipecat Voice Runtime
13. AI tools, dynamic qualification, and live scoring integration
14. Lightweight safety, handoff, and follow-up
15. Central dashboard and live call interface
16. Useful observability and failure simulation
17. Evaluation and end-to-end testing
18. Final hardening and demo runbook

## Remaining module scope and gates

### Module 8 — LLM provider interfaces

- Provider-independent protocol and standard request/response models.
- Streaming output, tool-call contracts, cancellation, and explicit timeout/error
  semantics.
- Deterministic mock provider for local development and repeatable tests.
- Runtime-facing boundary independent of vendor SDKs and external credentials.

Gate: contract/unit tests plus mock streaming/tool integration and
timeout/error/cancellation cases; existing Modules 1–7 regressions and interface
documentation. No real provider credentials are needed for this module.

### Module 9 — LLM providers and router

- Provider A and B adapters behind Module 8's protocol.
- Provider health, capability-aware selection, bounded timeouts/retries, circuit
  breaker, and failover inside the runtime boundary.
- Retain conversation context across provider selection and failure.

Gate: demonstrate A failure followed by successful B execution without lost
conversation state or duplicate completed effects; verify healthy/unhealthy
selection, retries, streaming failures, and recovery. Document each adapter's
configuration and deterministic test path.

### Module 10 — STT/TTS provider interfaces

- Streaming speech-to-text and text-to-speech protocols, standard data/contracts,
  lifecycle, cancellation, and errors/timeouts.
- Deterministic mocks and fixture audio/text suitable for Pipecat integration.

Gate: contract/unit tests, streaming mock integration, failure/cancellation tests,
and documented runtime boundaries. Multilingual routing and specialized model
variants remain deferred.

### Module 11 — Voice providers and router

- Voice Provider A and B adapters implementing the STT/TTS contracts.
- Health-aware selection, bounded retries/timeouts, and failover.
- Keep streaming interfaces and cancellation compatible with Pipecat; routers
  remain embedded in the Voice Runtime.

Gate: deterministic A/B failover, stream interruption/recovery, timeout, and
adapter integration tests; configuration and provider-health documentation.

### Module 12 — Pipecat Voice Runtime

Implement the real browser path:

```text
Browser WebRTC → Pipecat transport → STT → agent/conversation → LLM → TTS → browser
```

- Streaming, turn handling, interruption/barge-in, and cancellation.
- Integrate existing call-session connection/reconnection/failure contracts and
  incremental conversation persistence.
- Graceful provider, dependency, and transport failure handling.
- Keep durable state and business decisions in domain services, outside Pipecat
  processors.

Gate: working browser audio call, transcript and call-session integration,
interruption tests, disconnect/reconnect, provider/dependency failures, and
existing-module regressions; document local launch and media lifecycle.

### Module 13 — AI tools and live qualification

- Validated tool execution and dynamic next-question selection from missing or
  contradictory qualification facts.
- Runtime/LLM proposals use the existing Conversation → Lead operation;
  qualification validation, rules, score/history, and next actions remain
  backend-authoritative.
- Add confirmation/provenance behavior at the tool boundary and integrate live
  score updates. Any scoring-rule expansion remains inside Lead Service and
  follows the contract/conflict gate.

Gate: realistic tool-driven conversations with incomplete, contradictory, and
resolved answers; replay/partial-success tests; prove the LLM cannot set the
authoritative score and Lead outages never cause a local score fallback.

### Module 14 — Lightweight safety, handoff, and follow-up

- Useful, explicit policy checks and backend-selected next actions.
- Durable handoff and follow-up records using existing domain/worker boundaries
  and foundational workflow tables; resolve their exact ownership during module
  inspection.
- Idempotent execution and recoverable failures without a standalone Workflow
  Service or generalized workflow platform.

Gate: allowed/blocked actions, handoff/follow-up lifecycle, duplicate requests,
and recovery tests; document persistence, ownership, and execution semantics.

### Module 15 — Central dashboard and live call interface

- One product dashboard: lead list/profile, start call, live transcript,
  conversation/call state, qualification, authoritative score, provider health,
  events, sessions, and demo failure controls.
- Use Gateway contracts; represent dependency unavailability explicitly and
  preserve access to durable transcript/call state.

Gate: browser integration with the actual services/runtime, live updates and
call controls, loading/error/reconnect behavior, and an end-to-end demo. No
additional analytics or evaluation dashboard is planned.

### Module 16 — Observability and failure simulation

- Correlated structured logs, tracing where practical, useful metrics, health,
  and actionable failure context.
- Wire demo faults for LLM/voice failure, dependency timeout, and browser
  disconnect/reconnect into the central interface.

Gate: injected failures produce visible signals and documented recovery through
the implemented stack; verify fault reset and restored operation. Keep the
observability footprint proportionate to the demo.

### Module 17 — Evaluation and end-to-end testing

- Realistic lead scenarios and qualification/score assertions.
- LLM/voice provider failover, full voice calls, failure recovery, replay, and
  basic concurrent calls/turns.
- Reuse deterministic mocks and existing test/event infrastructure; no separate
  Evaluation Service or generalized evaluation platform.

Gate: repeatable scenario and E2E results, no duplicate durable effects or lost
conversation state, and a documented evaluation command/report.

### Module 18 — Final hardening

- Full regression/deployment gates, clean Compose startup, migration/drift and
  recovery checks.
- Configuration/provider credential documentation and deterministic local mode.
- Final architecture/ADRs, README, demo script, and operational runbook.

Gate: reproducible full product demo, passing relevant static/unit/integration/
failure/E2E checks, verified migrations and process recovery, and current docs.

## Explicitly deferred scope

- Separate Analytics, Evaluation, and Workflow services; complex analytics read
  models or a generalized workflow/evaluation platform.
- Kafka, Kubernetes, and production-grade mTLS/JWT workload identity.
- CRM, calendar, WhatsApp, and SMS integrations.
- Sophisticated multilingual routing and specialized STT variants.
- Full-text search infrastructure and automatic retention scheduling.
- Multiple dashboards and elaborate load-testing infrastructure.

Existing foundational tables, migrations, service contracts, ownership, state
machines, and Redis Streams guarantees remain in place. Deferred table capacity
does not require adding services or removing models.

## Module 1 status

Complete. Repository tooling, configuration validation, Compose infrastructure,
and development documentation are implemented and verified.

## Module 2 status

Complete. SQLAlchemy async persistence, Alembic migrations, the initial logical
schemas, core domain tables, session helpers, and PostgreSQL integration tests
are implemented and verified. The audit added migration `913be7264a01` to correct
24 timestamp defaults and enabled server-default drift detection.

## Module 3 status

Complete. The Lead Service provides validated lead APIs, qualification profile
persistence, deterministic idempotent synthetic seeding, optimistic compare-
and-swap updates, transactional lead outbox records, dependency failure
handling, and a verified container image.

## Module 4 status

Complete after the Modules 1–5 audit and expanded regression coverage:

- [x] Preserve omitted PATCH fields and explicit nullable values.
- [x] Map HTTP transport failures and whole-request deadlines to controlled 503s.
- [x] Reject missing, blank, and demo production tokens in both API services.
- [x] Handle malformed downstream responses with redacted 502 error envelopes.
- [x] Bound request IDs and persist them with lead outbox events.
- [x] Verify the real Gateway → Lead → PostgreSQL mutation/rollback path.
- [x] Verify rebuilt images during actual dependency outages and recovery.

The earlier passing suite missed these cases. The audit record is
[`modules-1-5-audit.md`](modules-1-5-audit.md).

## Module 1 scope

Module 1 contains only version conventions, root tooling, environment
validation, Compose infrastructure, and development documentation. It does not
create placeholder business services or provider integrations.

## Module 5 status and Definition of Done

Complete. Verified against real PostgreSQL/Redis and the running Compose stack.
The audit expanded coverage for slow publication failures, failed COMMIT logging,
malformed UTF-8, configuration, and process recovery. Current verification totals
are recorded in [`testing.md`](testing.md).

- [x] Version 1 event envelope and `EventBus` protocol
- [x] Redis Streams publish/subscribe, groups, pending recovery, and owned ACK
- [x] Independently deployable PostgreSQL outbox relay and polling index migration
- [x] Bounded retries/backoff, exhausted outbox inspection, dead letters/replay
- [x] Transactional processed-event deduplication and callback effects
- [x] Unit tests and real Redis/PostgreSQL integration tests
- [x] Crash-window, timeout, duplicate, Redis-loss, and database-failure tests
- [x] Existing-module regression suite
- [x] Compose Redis outage + relay restart test
- [x] Final documentation and deployment command review

## Module 6 scope and ownership resolution

Module 6 owns the Conversation Service, guarded conversation state transitions,
durable call-session lifecycle, incremental message/transcript persistence needed
while a call is active, the validated turn-ingestion boundary, and live
conversation orchestration. Module 7 adds richer transcript history, search,
retention, and retrieval without removing Module 6's transactional turn records.

Qualification and lead scoring remain owned by the Lead Service. After a
meaningful turn, Conversation Service synchronously invokes the Lead Service's
qualification operation; the returned qualification and score are authoritative.
Conversation Service does not calculate, persist, or override lead scores, and
LLM/runtime input is accepted only as validated structured facts.

The initial Lead Service scoring contract is `baseline-v1`:

- score starts at 0;
- six existing synthetic-profile attributes are each worth 10 points when
  confirmed and validated: `education_level`, `years_experience`, `english_level`,
  `has_job_offer`, `budget_ready`, and `urgency`;
- missing, provisional, unknown, invalid, or contradictory values contribute 0;
- contradiction is retained as explicit qualification state;
- score classification is `COLD` for 0–19, `WARM` for 20–39, and `HOT` for 40–60;
- the score and history retain the rule version and deterministic reasons.

This deliberately simple baseline is a stable interface for Module 13 to integrate
with AI tools and dynamic qualification. It does not introduce a scoring component
into Conversation Service or invent
domain-specific weights beyond the explicit equal baseline.

## Module 6 status and Definition of Done

Complete. The Conversation Service, Lead-owned live qualification/scoring
boundary, guarded state machines, independent call sessions, active-turn
transcript persistence, Gateway routes, Compose deployment, failure handling, and
documentation are implemented and verified.

- [x] Conversation state graph with optimistic version guards and terminal-state protection
- [x] Durable call-session lifecycle with reconnect attempts, failure metadata, and history
- [x] PostgreSQL uniqueness for one active conversation per lead and one active call per conversation
- [x] Incremental Message and final TranscriptSegment persistence with stable turn IDs
- [x] Lead-owned `baseline-v1` validation, deterministic score, classification, reasons, and history
- [x] Synchronous Conversation → Lead qualification operation after meaningful turns
- [x] Explicit contradiction tracking and conflict-resolution flow
- [x] Idempotent turn retries and Lead qualification update retries
- [x] Database/Lead outage handling with durable pending turns
- [x] Gateway proxy routes and live-state reads
- [x] Unit, integration, failure, existing-module regression, and Compose deployment tests
- [x] Migration upgrade/downgrade/drift and image verification

Module 7 provides richer transcript history, search, retention, and retrieval.
Module 13 integrates tools and dynamic qualification behind the stable Lead
Service scoring contract. Module 12 provides Pipecat media/transport integration.

## Module 7 scope and contract

Module 7 extends the Conversation Service without changing Module 6 live-turn
ownership or persistence. It provides bounded cursor pagination for message
history and transcript segments, case-insensitive text search within one
conversation, and an explicit operator-run retention/redaction command.

History queries support `limit=1..100`, `before_sequence`, `after_sequence`,
optional speaker filtering, and a bounded search term. Before/after cursors are
mutually exclusive. Search excludes redacted content. Results preserve
conversation sequence order and return the next cursor plus `has_more`.

Retention is deliberately not an automatic scheduler. An operator supplies a
cutoff timestamp, batch size, and reason, with a dry-run mode. Only `COMPLETED`
or `FAILED` conversations with `completed_at <= cutoff` and no pending turns
qualify. Redaction replaces message/transcript text with `[REDACTED]`, clears JSON
metadata, stores
the original content SHA-256, redaction time, and reason, and preserves IDs,
speakers, sequence numbers, timestamps, call links, and conversation state.
The operation is idempotent and never redacts active conversations or creates
domain events. Module 7's read APIs expose the redacted marker and hash but never
the original content.

## Module 7 status and Definition of Done

Complete. Module 7 adds bounded history/transcript retrieval, scoped search,
explicit terminal-conversation retention redaction, an operator CLI, Gateway
proxies, and failure/regression coverage without changing Module 6 live turns.

- [x] Cursor-paginated message history and transcript retrieval
- [x] Speaker filtering and escaped case-insensitive conversation-scoped search
- [x] Redaction metadata and original-content SHA-256 hashes
- [x] Terminal-state and cutoff guards for retention
- [x] Dry-run, bounded batches, and idempotent operator CLI
- [x] Active-conversation protection and redacted-content search exclusion
- [x] Gateway integration and Compose image verification
- [x] Unit, integration, failure, full regression, migration, and deployment tests

## Final Modules 1–7 verification

The final reliability verification passed **143 real-dependency regression tests**
(one separately enabled Compose test skipped in that run) and **6 rebuilt Compose
deployment tests**. It reproduced and corrected replay identity, concurrent
idempotency/finalization, pending-turn admission, definitive rejection, and
structured failure-event gaps. Migration head remains `e8f2a6b3c901` with no drift;
no model or state-machine changes were needed in this verification pass.

Evidence and exact recovery semantics: [`modules-1-7-verification.md`](modules-1-7-verification.md)
and [`testing.md`](testing.md). The next implementation module is **8 — LLM
provider interfaces** (subsequently completed; see below).

## Module 8 status and Definition of Done

Complete. A shared vendor-independent LLM package adds explicit prompt/context,
generation/response, streaming/tool proposal, error, timeout, and cancellation
contracts; a stateless runtime/local test adapter; and a deterministic mock.
No existing services, APIs, models, migrations, scoring, or state graphs change.

- [x] Provider protocol and bounded JSON request/response contracts
- [x] Separate trusted instruction and user/assistant/tool context
- [x] Correlated complete tool proposals and tool-result history
- [x] Validated streaming completion, partial-failure, and cancellation cleanup
- [x] Whole-generation timeout and content-free provider error codes
- [x] Replayable mock with deterministic chunks, tools, and fault fixtures
- [x] 38 contract/mock integration/failure tests
- [x] Modules 1–7 real PostgreSQL/Redis regressions: 181 passed, one opt-in Compose skip
- [x] Ruff lint/format, strict mypy (79 files), dependency and Compose checks
- [x] Project wheel build plus import/mock-generation smoke check from the wheel
- [x] Interface/ownership/local-use documentation in [`module-8.md`](module-8.md)

Real adapters, routers, health, retries, circuit breakers, and failover are
Module 9. Tool execution, domain validation, and runtime/service integration
remain in later modules. Module 8 is a library boundary, not a new service.

## Module 9 status and Definition of Done

Complete. OpenAI and OpenRouter adapters use Module 8's contracts and the existing
HTTPX dependency. OpenRouter defaults to Llama 3.3 70B Instruct; model IDs and
provider preference order are configurable. Mock mode remains credential-free.

- [x] Real text/function HTTP streaming adapters and full context translation
- [x] Fragmented SSE/tool arguments, termination/usage validation, and safe errors
- [x] Capability-aware provider selection and immutable operational health snapshots
- [x] Bounded retries, backoff, per-attempt and original request deadlines
- [x] Closed/open/half-open circuits with one recovery probe and epoch race guards
- [x] Buffered failover discards incomplete attempts; live failover stops after exposed output
- [x] Configuration/key validation and explicit mock/real modes
- [x] 80 new adapter/router/configuration tests; 118 combined LLM tests passed
- [x] Full PostgreSQL/Redis regression and migration gates: 261 passed, one expected opt-in Compose skip
- [x] Interface/configuration/recovery documentation in [`module-9.md`](module-9.md)

Provider integration verification uses HTTPX with deterministic vendor-format
fixtures. Live paid vendor calls were not run. The optional wheel rebuild was
declined and is unverified for this module. No service/migration/HTTP-contract or
scoring changes were introduced. Circuit state is operational process memory;
durable status/UI exposure remains later-module work. Module 10 subsequently adds
speech interfaces below.

## Module 10 status and Definition of Done

Complete. The separate speech package supplies immutable bounded PCM, transcript,
request/completion contracts, closable STT/TTS protocols, stateless streaming
validation/deadline adapters, deterministic mock providers, and PCM fixtures.

- [x] Audio format/alignment, sequence and utterance identity contracts
- [x] Interim replacement, immutable finals, sample offsets and terminal accounting
- [x] Pull-based streaming/backpressure with bounded audio/text/event counts
- [x] EOF validation, controlled errors, timeout, cancellation and early-close cleanup
- [x] Deterministic mock audio/text and concurrent/restarted reuse
- [x] 75 contract/mock integration and failure tests passed
- [x] Existing PostgreSQL/Redis regression gates: 336 passed, one expected Compose skip
- [x] Ruff format/lint, strict mypy (94 source files), dependency/Compose and migration checks
- [x] Runtime ownership, provider preferences, limitations and local-use documentation

No provider adapter, router, Pipecat, service/HTTP/schema/state/scoring change is
introduced. User TTS candidates are OpenAI Realtime or Sarvam/Rumik; final provider
selection and Realtime's compatibility with independently generated LLM text are
Module 11 work. Module 11 subsequently selects/implements adapters below.

## Module 11 status and Definition of Done

Complete. Sarvam realtime STT and Sarvam/Rumik HTTP PCM TTS implement Module 10's
unchanged contracts. The two real paths share one STT provider, preserving the
reduced scope. OpenAI Realtime TTS is deferred because an independently generated
LLM text-to-audio guarantee was not established.

- [x] Request-scoped real STT/TTS adapters and explicit PCM/model/voice capabilities
- [x] Safe errors, wire/completion validation, deadlines and cancellation cleanup
- [x] Bounded pre-output retry/failover; no replay of exposed text/audio
- [x] Bounded STT prefix/EOF replay and interrupted-input protection
- [x] Process-local health/circuits with exclusive probes and epoch race protection
- [x] Credential-free mock mode and safe explicit real configuration
- [x] 88 new adapter/router/configuration tests, including real local WebSocket transport
- [x] Full PostgreSQL/Redis regression gate: 424 passed, one expected Compose skip
- [x] Ruff format/lint, strict mypy (101 files), dependency/Compose and migration gates
- [x] Configuration/ownership/verification documentation in [`module-11.md`](module-11.md)

Live paid vendor access/quality/latency, wheel builds and Compose deployment
rebuilds were not verified in this pass. Domain services, scoring, state graphs,
HTTP contracts and migration head are unchanged. Module 11 is complete; Module 12
is documented and verified below.


## Module 12 — implementation, contracts and local operation

Implemented and verified on 2026-10-05. No standalone module document is created. This plan
and `PROJECT_HANDOFF.md` are the main implementation/continuation notes.
This section records Module 12's media-only boundary; the subsequently approved
Module 13 qualification integration is documented below.

### Approved persistence addition

The user approved an additive agent-output write API because the existing
user-turn endpoint only persisted USER messages and performed qualification
orchestration. The user-turn handler and service method are unchanged.

```text
POST /v1/conversations/{conversation_id}/agent-messages
```

Both Conversation Service and Gateway expose the endpoint. Request contract:

```json
{
  "message_id": "<stable UUID reused for every retry>",
  "call_id": "<call UUID belonging to the conversation>",
  "expected_version": 3,
  "parent_turn_id": null,
  "text": "Hello. How can I help you today?",
  "provider": "voice-runtime",
  "model": "greeting-v1"
}
```

`parent_turn_id` may be null only for a greeting in GREETING. A reply must refer
to the latest APPLIED user turn on the same call. Text is bounded to 20,000
characters, nonblank, valid UTF-8 and NUL-free; provider/model fields are bounded.
Extra fields are rejected. The response is HTTP 200 with `MessageHistoryEntry`
for both creation and identical replay. Internal authentication and request-ID
normalization are the existing service conventions.

New admission requires the current conversation version, a CONNECTED matching
call, an active conversational state and no pending user turn. The existing
conversation lock serializes sequence allocation with user turns. One transaction
writes an AGENT Message (`turn_status=APPLIED`), FINAL TranscriptSegment, and the
content-free `conversation.agent.recorded` outbox event. Existing tables, indexes
and migrations are reused. No Lead request, qualification update, score, state
transition or conversation version advancement occurs on this path.

A durable request fingerprint covers message/call/parent IDs, text, provider and
model; admission version is excluded. Identical replay returns the existing
record even with a stale version or after terminal state/retention. Changed data,
ID collisions, stale admission and invalid parent/call conditions return 409;
missing records return 404; invalid/auth requests retain 422/401. Failed transactions
roll back all three effects. Redacted replay returns the marker and never restores
original content. Metadata `output_kind=generated` explicitly describes generated
text; neither FINAL nor APPLIED asserts that the caller heard the entire response.
The caller is responsible for retaining the same message UUID on retries.

### Runtime and media ownership

`packages/voice-runtime/voice_platform_runtime` contains a typed internal HTTP
backend, durable dialogue coordinator, Pipecat frame processor, SmallWebRTC
signaling/session lifecycle and a minimal browser test page. The Gateway mounts
it at `/voice` only when `VOICE_RUNTIME_ENABLED=1`. It adds no separate service,
database store, Redis consumer, scoring implementation or AI tool execution.

The pipeline is browser WebRTC → Pipecat transport → Silero VAD → existing STT
router → Conversation user-turn persistence → existing LLM router → additive
agent-output persistence → existing TTS router → browser audio. Mock defaults
remain credential-free fixture transcripts and PCM tones, not recognized/spoken
speech. Real mode retains OpenAI/OpenRouter LLM and Sarvam STT + Sarvam/Rumik TTS.

Audio arrives as 16 kHz mono signed-16 PCM. STT streams from a bounded 64-frame
queue with 200 ms pre-roll; utterances are limited to 30 s. Because STT deadlines
include collecting live audio, the runtime sets its STT attempt/total budget to
45 s; TTS uses 24 kHz PCM and the configured speech router attempt budget within
a 30 s request. LLM generation is bounded to 256 tokens/20 s. It consumes streaming
provider events into a complete validated response before persistence/synthesis;
this conservative boundary increases first-audio latency compared with speaking
uncommitted token fragments. The response is limited to 2,000 characters to fit
both configured TTS paths. Tool proposals and incomplete generation are rejected.
Durable history supplies at most 40 recent USER/AGENT messages/40,000 characters;
redacted content is excluded. No facts are extracted: new user turns submit
`facts=[]` through the unchanged backend orchestration boundary.

Automatic Silero turn detection and push-to-talk use the same STT path. Barge-in
cancels generation/synthesis and immediately flushes queued Pipecat output before
waiting for a backend write. A database write
already in progress is shielded and joined before cancellation completes; media
cancellation never abandons an ambiguous accepted mutation. Lost user/agent
responses keep the original payload/UUID for explicit recovery. Backend PENDING
user turns can be recovered from durable history after process reattachment.
Recovery retries persistence only and never automatically replays audio. Provider
and dependency errors are content-free; no fallback score or business decision is
invented. Invalid/overflow audio cancels the whole utterance instead of skipping
samples. Every owned provider/task/connection is closed on cancellation/teardown.

### Signaling and call lifecycle

```text
GET    /voice/                              minimal media test page
POST   /voice/sessions                      {conversation_id, call_id, manual_turns}
POST   /voice/sessions/{session_id}/offer    {sdp, type:"offer", pc_id?, restart_pc?}
POST   /voice/sessions/{session_id}/retry    recover with connected media
DELETE /voice/sessions/{session_id}          end media/call
```

Session creation returns a random session ID and secret bearer capability.
All subsequent signaling/control requests require that capability; tokens stay
in browser memory and are not logged. The page creates synthetic-lead
conversations/calls through the existing Gateway APIs or accepts existing IDs.
The runtime does not create a second call record. An existing active call must
match the supplied conversation/call IDs. Media lifecycle uses the existing
CREATED → CONNECTING → CONNECTED ↔ RECONNECTING → ENDED/FAILED graph; business
conversation lifecycle remains independent. Connection establishment advances
CREATED → CONNECTING → GREETING through the existing guarded API, not direct SQL.

One runtime session owns a conversation within the process; admission reserves
ownership before network I/O. Eight sessions maximum, 30-minute absolute lifetime,
60-second initial/reconnect grace and bounded signaling prevent unbounded runtime
state. Identical SDP retries return the same answer/pipeline. Peer restart creates
a new media peer ID while retaining the durable conversation/call IDs and history.
Lifecycle transitions share a lock; lost replies reconcile against authoritative
backend state rather than repeating effects or closing a different active call.
Call closure does not complete/fail the business conversation automatically.

Closing media remains independent of successful durable closure. If the call-end
request/reconciliation fails, the runtime still cancels/joins the pipeline and
disconnects the peer. The original session/call remain available for an end retry;
new offers and connection callbacks cannot reopen a session already ending.

The deployment is one Gateway process for this local demo. Session capabilities,
media ownership and uncommitted input are process-local. A restarted process can
reattach to the existing durable active call and recover PENDING accepted turns;
unaccepted audio/text cannot survive process loss. There is no TURN/STUN service;
host ICE candidates are intended for localhost/same reachable network. Telephony,
NAT traversal infrastructure, dashboard and AI qualification tools remain deferred.
The pinned Pipecat 1.12 connector's retained renegotiation timer is cancelled/joined
on close to avoid a dangling task. Pipecat content-bearing debug logs are disabled.

### Local launch

Install with `.venv/bin/python -m pip install -e '.[dev,voice]'`. The voice extra
is optional for deployed nonvoice services; dev includes it for type/regression
gates. Browser verification additionally uses `.[browser-test]` and an installed
Chrome executable. New project wheel builds were not run.

Start/migrate PostgreSQL/Redis using the existing instructions. For host processes,
set these variables in each terminal (or export them in a shell before launching):

```bash
export APP_ENV=local
export POSTGRES_HOST=localhost POSTGRES_PORT=55432
export POSTGRES_USER=voice_ai POSTGRES_DB=voice_ai POSTGRES_PASSWORD=voice_ai_dev_password
export LEAD_SERVICE_AUTH_TOKEN=local-lead-service-token
export LEAD_SERVICE_URL=http://127.0.0.1:8001
export CONVERSATION_SERVICE_URL=http://127.0.0.1:8002
export LLM_MODE=mock SPEECH_MODE=mock
export PYTHONPATH=packages/configuration:packages/contracts:packages/database:packages/llm:packages/speech:packages/voice-runtime:services/lead-service/src:services/conversation-service/src:apps/api-gateway/src
```

Run each in a separate terminal, using one Gateway worker:

```bash
.venv/bin/python -m uvicorn lead_service.app:app --host 127.0.0.1 --port 8001
.venv/bin/python -m uvicorn conversation_service.app:app --host 127.0.0.1 --port 8002
VOICE_RUNTIME_ENABLED=1 .venv/bin/python -m uvicorn api_gateway.app:app --host 127.0.0.1 --port 8000
```

Seed synthetic leads through the existing seed command, then open
`http://localhost:8000/voice/`. Enter a lead UUID to create a conversation/call,
or use existing conversation/call IDs. Connect microphone; use push-to-talk or
uncheck it before connecting for automatic VAD. Retry saved operations after a
dependency recovers; reconnect after a transport disconnect; end explicitly.
Microphone permission requires localhost or HTTPS. A previous active conversation
must be reused instead of creating another for that lead.

Compose passes the provider settings to Gateway and installs Pipecat in its
image only when `VOICE_RUNTIME_ENABLED=1` at build time. The continuation gate
rebuilt the voice-enabled image and verified its startup, browser page, signaling
controls, and deployed agent-message API. For a local Docker audio demo, use
host networking/reachable ICE as appropriate;
port publication alone does not guarantee UDP media reachability. The verified
browser path below runs host media in Chrome, not a Docker media network.

### Verification and limits

Codex's original final gate passed **463 tests with one expected opt-in Compose skip** (464
collected), including real PostgreSQL/Redis, a real WebRTC audio roundtrip, and
actual headless Chrome push-to-talk and automatic Silero detection with synthetic
microphone input. No user's physical microphone or paid API was used. Real vendor
recognition/synthesis quality and live latency still require credentials/account
access and a separate paid smoke test.

The new tests cover atomic agent persistence, concurrent/repeated retries,
changed payload/UUID collisions, stale version/call/parent guards, pending-turn
admission, rollback/database failures, redaction-safe terminal replay, Gateway
correlation, durable history, Lead outage recovery, cancellation during a database
mutation/generation/TTS, lost agent/call responses, process reattachment, bounded
queues, invalid media, greeting task replacement, signaling replay, actual Opus
media and reconnect/end. Neither schema nor scoring/user-turn behavior changed.

Reproducible regression/browser commands use a migrated disposable database and
isolated Redis keys, as in the existing testing instructions:

```bash
# Fixture: mono 16 kHz PCM WAV speech with >=2 s leading and >=3 s trailing silence.
# Supply synthetic audio, not the user's microphone.
RUN_BROWSER_VOICE_TESTS=1 VOICE_TEST_WAV=/absolute/path/synthetic-speech.wav \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module12_test \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py
make format-check lint typecheck
.venv/bin/python -m pip check
docker compose config --quiet
```

Without the browser opt-in, the two Chrome cases intentionally skip; the verified
gate used synthetic browser audio. New tools, qualification extraction/provenance,
and live AI-driven scoring are Module 13. Stop before implementing Module 13.

### Continuation verification — 2026-10-05

The takeover inspected `PROJECT_HANDOFF.md`, this plan, Git status/history,
the approved endpoint diff, existing user-turn/state-machine/model boundaries,
and the runtime/tests. Codex had already implemented Module 12 and recorded a
passing gate; that implementation was retained. Module 12 remains uncommitted
on top of `d9db88b` (Modules 8–11). No new architectural conflict was found.

Two meaningful failure tests reproduced remaining lifecycle gaps before fixes:

- A barge-in waited for a shielded mutation before sending the interruption frame,
  allowing queued playback to continue during the write. Cancellation now sends
  the flush immediately, then joins the accepted mutation.
- A failed call-end/reconciliation request left its media connection open. Media
  teardown now runs in `finally`, while the original durable call remains retryable.
  Ending sessions reject offers/connection reactivation.

These fixes are confined to the runtime and preserve the approved additive agent
API, existing user-turn path, models, scoring authority, and state graphs.

The continued full regression gate passed **465 tests, one expected Compose skip**
(466 collected), including both actual Chrome cases, real WebRTC Opus audio and
reconnect, real PostgreSQL/Redis, and both new failure reproductions. The browser
fixture was local synthetic speech generated with macOS `say`, converted to mono
16 kHz signed-16 PCM WAV and padded with three seconds of leading/five seconds of
trailing silence. Neither the user's physical microphone nor paid vendors were
used. The disposable database was `module12_continuation` and was removed after
verification; recreate/migrate a test database before rerunning the commands above.

All Compose images were rebuilt with mock providers and the optional voice runtime
enabled. **6 opt-in deployment tests passed**, including the existing Redis/relay,
PostgreSQL/Lead restart and production configuration cases. The conversation case
now additionally verifies deployed agent-message creation, stale-version replay,
changed-payload conflict, AGENT transcript retrieval/outbox publication, and the
voice-enabled browser page/session capability/end controls. Ending an unattached
media session leaves the business conversation intact. This verifies image/startup
and signaling, not Docker-to-browser UDP audio reachability.

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
VOICE_RUNTIME_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock \
  docker compose up -d --build --wait

POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
VOICE_RUNTIME_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock RUN_EVENT_STACK_TESTS=1 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai \
STACK_REDIS_URL=redis://localhost:56379/0 STACK_GATEWAY_URL=http://localhost:18000 \
  .venv/bin/python -m pytest \
  tests/integration/test_event_stack.py tests/integration/test_compose_conversation.py
```

Ruff formatting/lint, strict mypy (**107 source files**), host and voice-image
`pip check`, Compose configuration, and Git whitespace checks passed. The full
suite reran migration roundtrip/rebuild/preservation checks; deployed Alembic is
`e8f2a6b3c901` with no drift. Restored Gateway health is 200, all application
processes are running, PostgreSQL/Redis are healthy, and outbox unpublished/
exhausted and dead-letter counts are zero. The voice-enabled mock stack remains
running; `http://localhost:18000/voice/` serves its media test page.

Paid-provider recognition/synthesis quality/latency, package wheel builds, and
Docker browser-audio/NAT traversal remain unverified. Host Chrome/WebRTC is the
verified media path. At this Module 12 checkpoint, Module 13 was unstarted;
its subsequent approval and completion are recorded below.

## Module 13 — AI tools, staged qualification and live scoring

Complete on 2026-10-05. The user approved the additive staged-turn resolution
after inspection found that `/turns` freezes facts at admission and applies the
turn immediately. The original `/turns` contract, handler, persistence method,
Lead mutation/scoring rules, schemas and state machines are preserved. No new
model, migration, dependency, service or business consumer is introduced.

### Approved durable flow

```text
STT final text
  → commit staged USER Message + FINAL TranscriptSegment + recorded outbox event
  → LLM proposes facts against the durable current transcript
  → Lead validates values and confirmation evidence (read-only)
  → commit immutable accepted facts/provenance + binding receipt
  → existing Lead qualification update + authoritative score/history/outbox
  → existing guarded Conversation finalization
  → emit live qualification/score/next-action to the browser
  → bounded read-tool dialogue + backend-selected question
  → existing agent-message persistence → TTS/audio
```

The stages reuse the existing PENDING turn guard and retention protection. No
database lock is held over HTTP/LLM calls. Input survives provider failure and
cancellation before extraction; frozen facts survive restart without re-extraction.
Scoring is still Lead-owned `baseline-v1`, profile coverage 0–60, not eligibility.

### Additive contracts

Gateway and Conversation Service expose:

| Method/path | Result |
|---|---|
| `POST /v1/conversations/{id}/staged-turns` | Persist/replay original input; 200 `MessageHistoryEntry` |
| `POST /v1/conversations/{id}/staged-turns/{turn_id}/facts` | Validate and freeze/replay proposals; 200 `MessageHistoryEntry` |
| `POST /v1/conversations/{id}/staged-turns/{turn_id}/apply` | Apply/replay saved facts and finalization; 200 `ConversationTurnResponse` |
| `GET /v1/conversations/{id}/qualification-context` | Current Lead profile and Lead-selected qualification plan |

Lead Service adds authenticated, read-only boundaries:

- `GET /v1/leads/{id}/qualification/plan`: authoritative qualification plus missing,
  provisional and contradictory field lists, next field and next question.
- `POST /v1/leads/{id}/qualification/validate`: validated effective facts and
  evidence provenance, without mutating profile, score or history.

Input body (UUIDs are stable across retries):

```json
{
  "turn_id": "<UUID>",
  "call_id": "<existing connected call UUID>",
  "expected_version": 3,
  "user_text": "I confirm my masters degree."
}
```

Facts body:

```json
{
  "provider": "mock",
  "model": "scenario-v1",
  "proposals": [
    {
      "field_key": "education_level",
      "value": "masters",
      "evidence": "I confirm my masters degree.",
      "resolve_conflict": false
    }
  ]
}
```

Application requires no fact/score body; it consumes the durable binding. An
empty proposal list is supported for a turn with no qualifying facts and creates
no Lead score-history entry. Score is nullable until Lead first calculates it.
Input and facts requests forbid arbitrary extra fields, score/status inputs, duplicate fields,
unsupported field names, unsafe JSON/Unicode/NUL and oversized data. Text is
limited to 20,000 characters; evidence to 2,000 per field; six proposals maximum;
new input bodies are bounded to 64 KiB.

First admission uses existing version, state and connected-call guards. Same-input
replay ignores a stale admission version; changed text/call/identity conflicts.
Recorded staged receipts have a namespaced fingerprint, so sending a staged UUID
to the unchanged `/turns` endpoint returns 409 rather than bypassing facts binding.
Normal `/turns` requests retain their existing behavior.

The facts receipt hashes canonical field-sorted proposals plus provider/model;
identical replay is accepted and changed payload/provenance conflicts. Metadata
`stage=RECORDED|BOUND` describes the input stage; these are not new database or
conversation states. Facts/provenance remain in existing Message JSON metadata.
Binding emits content-free `conversation.turn.facts_bound` with aggregate type
`turn_facts`, version 1, using the existing outbox/envelope. It does not collide
with Message recorded/applied aggregate versions 1/2. Cross-aggregate stream
ordering is not a distributed transaction; PostgreSQL remains the source of truth.

### Confirmation, provenance and dynamic questions

LLM proposals contain a field, value and verbatim current-turn evidence. They
cannot supply `CONFIRMED`, confidence-based scoring or a numeric score. Lead's
new validation policy reuses its existing baseline field validator:

- Evidence must be an exact substring of the persisted current user text and
  contain the claimed value in the supported English grammar.
- Valid assertions are provisional by default. Confirmation requires the current
  utterance and evidence to start with `I confirm`, with quoted/reported,
  conditional, uncertain and common third-party assertions excluded from
  confirmation. Mismatched or uncertain value evidence is rejected. Negation
  is accepted for a matching false boolean, not to confirm a negated positive value.
- Valid false/zero values are retained and score under existing baseline rules
  when confirmed. No positive-eligibility interpretation is added.
- `resolve_conflict=true` requires explicit confirmation and an existing
  contradictory Lead answer; a model cannot use it to silently skip contradiction
  tracking. The original Lead contradiction/resolution implementation is reused.
- Provenance stores evidence, provider/model, confirmation kind, original turn ID,
  `source=USER_TRANSCRIPT` and `validation_policy=evidence-v1` in Message metadata.

Next-question selection is Lead-owned: contradictions first, then existing
provisional/invalid answers, then missing fields. Known confirmed fields are
skipped, multiple facts can arrive in one turn, and urgent callers prioritize
still-missing budget/job-offer context. Confirmation and conflict questions
provide complete example assertions that match the validation grammar. While a
question remains, the runtime persists/speaks that backend-selected question as
`voice-runtime` / `qualification-policy-v1`, rather than accepting an arbitrary
model-selected workflow transition.

This is a conservative demo confirmation protocol, not a general semantic or
identity-proof engine. Caller assertions are not independent factual proof.
Unsupported wording, number words, or multilingual evidence requires clarification;
multilingual semantic confirmation remains outside the reduced scope.

### Tool and runtime boundaries

`voice_platform_runtime.qualified.QualifiedDialogue` is the active Session
coordinator. The original text-only `Dialogue` remains available for compatibility
and legacy pending-turn recovery. The tools are:

- `propose_qualification`: one typed proposal batch; the staged backend validates,
  freezes and applies it. Not a direct Lead CRUD/score API.
- `get_lead_profile`, `get_qualification`, `get_missing_fields`,
  `get_conversation_history`: typed read tools scoped to the owning conversation.
  Arguments must be empty; arbitrary lead/conversation IDs are rejected.

Score updates are automatic inside the existing Lead mutation, not a callable
LLM score calculator. Unknown tools, score-setting requests, duplicate tool IDs,
and malformed arguments are rejected. The read loop is capped at three generation
rounds, four tools per response and 30 seconds total; extraction has a 20-second
generation budget and 1,200 output tokens. Agent response generation remains
bounded and durable before TTS. Workflow/scheduling/handoff/end-call LLM tools
are not registered here; their policies/execution belong to Module 14.

The runtime sends `{type:"qualification", turn_id, qualification, next_action}`
over its existing data channel only after authoritative application succeeds,
including on explicit recovery. The minimal browser page already displays these
notifications. Lead failure does not emit a prior score as a fallback.

### Failures and recovery

- A provider/Lead error leaves recorded or bound input PENDING; another turn,
  agent output, replacement conversation and retention cannot overtake it.
- A rejected model proposal returns 422 without freezing or mutating Lead.
  Corrected extraction of the same durable text may retry; the runtime discards
  that rejected cached proposal. An empty valid batch can finish a no-fact turn.
- After binding, proposal changes return 409. Lost binding replies and process
  restart reuse the stored accepted facts, not a fresh LLM interpretation.
- Lost Lead replies, concurrent application and finalization rollback reuse the
  same turn UUID/Lead receipt and existing locked finalization. No duplicate
  transcript, qualification update, score history or state advancement results.
- A definitive Lead 422 during application records FAILED and frees runtime
  admission for corrected input under a new UUID; rejected receipts are not
  resurrected by retry.
- Accepted pending input may finish after call/conversation termination, without
  reopening terminal business state. Retention waits; subsequent input/binding/
  application replay is redaction-safe and never restores erased text/metadata.
- Explicit recovery completes durable operations only; it does not automatically
  regenerate an agent reply or replay audio. New calls use the same original
  call/history boundaries. No recovery scheduler or new durable store is added.

### Verification and local operation

**48 additional Module 13 cases** (33 real-DB integration, 15 unit) cover complete,
incomplete, contradictory and resolved conversations; explicit false/zero values;
evidence/status/score rejection; dynamic urgent questioning; every stage's
concurrent/lost-response replay; input before extraction; cancellation/restart;
frozen-fact recovery; Lead outage with a prior score; definitive rejection; scoped
tools/loop bounds; Gateway correlation and redaction/terminal recovery.

The final full gate passed **513 tests, one expected Compose skip** (514 collected),
including both actual Chrome push-to-talk/automatic VAD cases, real WebRTC audio,
PostgreSQL/Redis and migration roundtrip/rebuild/preservation. **6 rebuilt
voice-enabled Compose tests passed**; the deployed conversation case additionally
records, freezes and applies a staged assertion twice, checks the score becomes
30 exactly once, and reads the authoritative qualification context.

Run the focused gate on a migrated disposable database:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module13_test \
  .venv/bin/python -m pytest \
  tests/integration/test_staged_qualification.py tests/unit/test_qualification_evidence.py
```

Full browser/deployment commands and host launch variables are in the Module 12
section above; use the current migration head and the same synthetic WAV/Chrome
opt-in. The disposable `module13_test` was removed after verification; recreate
and migrate a test database using `docs/testing.md` before rerunning.

Ruff format/lint, strict mypy (**114 source files**), dependency/configuration and
deployed migration drift checks passed at unchanged head `e8f2a6b3c901`. Restored
health is 200; outbox unpublished/exhausted and dead-letter counts are zero.
All application images were rebuilt. The optional voice stack remains in mock
mode at `http://localhost:18000/voice/`.

Mock speech remains fixture text/tone. The unchanged default mock LLM proposes
no facts; scripted tool providers exercise real qualification in tests and the
staged APIs can demonstrate it without paid credentials. Real LLM mode uses the
existing OpenAI/OpenRouter tool-capable adapters and existing voice providers.
Paid extraction/recognition/synthesis quality and latency, Docker browser-audio
reachability and wheel builds were not verified. Module 13 is complete; stop
before **Module 14 — Lightweight Safety + Handoff + Follow-up**.

## Module 14 — caller policies, durable handoff and follow-up

Complete on 2026-10-05. The user approved an additive workflow-action boundary
after inspection identified two concrete conflicts: incomplete qualification
cannot normally reach DECISION/HUMAN_HANDOFF/FOLLOW_UP/COMPLETED, and new agent
output is rejected once the conversation enters those outcome states.

### Approved exception and ownership

Conversation Service owns the lightweight operations on the existing
`workflow.handoffs`, `workflow.followups` and `workflow.followup_attempts` tables.
The existing graph and normal turn/scoring/transition behavior are unchanged.
Only an evidenced explicit caller request through the new action operation can
route before qualification is complete. It traverses existing edges from the
current conversational state through QUALIFICATION/SCORING/DECISION as necessary,
then reaches HUMAN_HANDOFF, FOLLOW_UP or COMPLETED. SCORING on this exceptional
path is a **routing waypoint**, not a claim of completed qualification or a newly
calculated score. State events carry `reason=explicit_caller_request`; the action
receipt records the original state, `early_routing` and `caller-request-v1`.

Lead remains the sole qualification/scoring authority. This caller-only operation
does not read/mutate Lead, invent eligibility, add flag facts, or fabricate scores.
The active call remains CONNECTED after its business action commits. Media closure
belongs to the runtime and is a separate recoverable operation.

### Transaction and policy

```text
persist and apply USER turn using the existing turn/staged path
  → LLM proposes one scoped request_workflow_action
  → Conversation locks its conversation and the stable action receipt identity
  → validate latest APPLIED, unredacted USER turn and owning CONNECTED call
  → validate complete caller request against explicit English policy
  → persist/flush backend-selected AGENT acknowledgement + transcript + outbox
  → insert handoff/follow-up record when applicable
  → traverse existing business-state edges + record action receipt/outbox
  → COMMIT all effects together, then return success
  → runtime synthesizes acknowledgement, queues ordered EndFrame, closes media/call
```

Admission requires the current version, no pending turn, and no existing agent
reply to that user turn. It is limited to GREETING/DISCOVERY/QUALIFICATION/SCORING/
DECISION. Caller evidence must quote the **entire current utterance**, not a
cherry-picked substring. Supported examples:

- `Can I speak to a human?`
- `Please connect me to a consultant.`
- `Please call me later.`
- `Please call me at 2026-10-07T12:00:00+00:00`
- `Please end this call.`

Quoted, reported, negated, conditional, uncertain and compound requests do not
match the narrowly supported grammar and return 422 without action effects.
Unsupported wording requires a clearer new caller turn; this is conservative
English demo consent validation, not general semantic/identity proof.

`call me later` and an untimed explicit follow-up request select a **24-hour demo
reminder**. The acknowledgement states its exact UTC time and explains that it is
a reminder, not an automatic phone call. A supplied time must match an ISO-8601
timestamp in the caller evidence, include a timezone, and be in the next 90 days.
The LLM cannot silently pick a different time. Handoff records request consultant
review; they do not promise an actual telephony transfer.

The `response-policy-v1` check screens free model responses for unsupported
eligibility/approval guarantees, score/classification claims, bookings and workflow
claims. Blocked text is replaced with a fixed safe clarification before agent
persistence/TTS. Backend-selected qualification questions and action acknowledgements
are already authoritative templates. This is an intentionally conservative lexical
screen, not a general safety classifier. The existing agent-message storage API
keeps its previous contract; the active qualified runtime invokes policy before
persisting free model output.

### Additive APIs

Gateway and authenticated Conversation Service expose:

| Method/path | Result |
|---|---|
| `POST /v1/conversations/{id}/workflow-actions` | 200 `WorkflowActionResponse`; create or identical replay |
| `GET .../workflow-actions/{action_id}` | Current durable action result, acknowledgement and workflow |
| `GET .../workflows` | Up to 100 most recent handoffs and 100 follow-ups for this conversation |
| `POST .../handoffs/{workflow_id}/transitions` | Idempotent, expected-status-guarded operator lifecycle |
| `POST .../follow-ups/{workflow_id}/transitions` | Idempotent completion/cancellation/explicit retry |

Runtime-only authenticated Conversation boundary:
`POST .../output-policy` accepts `{turn_id, call_id, text}` and returns
`{text, allowed, reason, policy_version}`. It verifies the same current caller/call
scope. No new database access is added to Gateway/runtime.

Action body:

```json
{
  "action_id": "<stable UUID>",
  "turn_id": "<latest applied USER turn UUID>",
  "call_id": "<owning connected call UUID>",
  "expected_version": 4,
  "action": "HUMAN_HANDOFF",
  "evidence": "Can I speak to a human?",
  "scheduled_at": null,
  "provider": "mock",
  "model": "workflow-test"
}
```

Other actions are `FOLLOW_UP` and `END_CONVERSATION`. Extra fields, arbitrary
workflow status/score inputs, unsafe UTF-8/NUL and oversized bodies are forbidden;
evidence is bounded to 2,000 characters, provider/model to 64/128, body to 64 KiB.
The response contains the current conversation, durable acknowledgement, relevant
handoff/follow-up record, action ID/policy and `close_media=true`.

The content-free receipt hashes all request fields except admission version,
including caller/call/proposal/provider/model. Identical replay ignores stale
version, terminal state and redaction; changed scope/payload returns 409. An
action's deterministic acknowledgement UUID and its workflow UUID are stable.
Conversation locks and transaction-scoped advisory locks serialize local and
cross-conversation receipt collisions. An existing receipt returns current state,
not a historical response snapshot. Redacted replay returns `[REDACTED]` and never
restores original content. A rollback removes acknowledgement/workflow/transitions/
receipt together; retry the same payload. No lock spans provider/network calls.

### Lifecycle and reminder execution

Handoff: REQUESTED → ASSIGNED → COMPLETED; REQUESTED/ASSIGNED may CANCEL.
Assignment is a demonstrative operator status, not a production assignee identity.
Follow-up: SCHEDULED → RUNNING → READY; READY may COMPLETE or CANCEL, SCHEDULED may
CANCEL, and FAILED may explicitly retry to SCHEDULED or CANCEL. RUNNING is worker-owned;
operators cannot complete/cancel an in-flight attempt. Operator bodies use
`{operation_id, expected_status, target_status}`. Identical operation replay is safe;
changed payload/stale or unsupported transitions return 409. Completion/cancellation
finishes the business conversation only if it is still in that workflow's outcome
state; it never resurrects terminal state or closes audio directly.

The Conversation-owned command runs **one bounded pass**, without sleeping in a
request/runtime or adding an always-on service:

```bash
python -m conversation_service dispatch-follow-ups --batch-size 50
```

It processes only `CONSULTANT_REMINDER` records. Claiming uses `SKIP LOCKED`, durable
RUNNING attempts and token leases in existing metadata. Batches are 1–100; each
attempt has a 10-second limit; the lease covers the bounded sequential batch plus
60 seconds. The local effect makes a reminder READY for dashboard/operator review;
it sends no external message, places no call and performs no calendar booking.
RUNNING attempts survive process cancellation/crash. Expired leases mark the old
attempt FAILED, consume an attempt, and allow another claim; token checks prevent
a stale owner from finalizing a replacement attempt. Three attempts exhaust to
FAILED, with bounded retry delay and content-free errors. Explicit operator retry
grants three more attempts while preserving monotonic unique attempt numbers.
Started/finished attempt events commit with their effects; aggregate versions 1/2
preserve existing outbox ordering. Redis downtime delays publication, not workflow
persistence. There is no automatic poller or external-effect exactly-once claim.

### Runtime and recovery

`request_workflow_action` accepts only a typed action/evidence/schedule proposal,
scoped to the owning call/current turn. It must be the sole tool in its response;
mixed action/read batches, malformed arguments, unknown tools, duplicate IDs and
score setters are rejected. Existing generation round/time bounds remain.
The runtime selects an action UUID from call/turn IDs and caches the exact request
before a shielded write. Ambiguous errors retain it for explicit retry; definitive
404/409/422 clear the rejected proposal. Cancellation joins accepted writes.
Lost responses replay that identity; restart finds the accepted action ID in
durable acknowledgement metadata and reads its scoped receipt. Neither path
re-proposes an accepted action or replays audio.

Successful application emits a `workflow` data-channel notification. The runtime
speaks only the committed backend acknowledgement, queues an ordered EndFrame and
then closes the existing media/call lifecycle. Generated text/audio frames do not
prove the caller heard all speech. If synthesis/transport/closure fails, the durable
action is retained; explicit recovery closes media without speech replay. Existing
call-close reconciliation/finally teardown remain in force.

### Verification and stopping point

**31 new cases** (30 real-DB integration + one real Pipecat pipeline case) cover
early outcomes, QUALIFICATION routing with incomplete answers, caller evidence
rejection, schedule validation, acknowledgement-before-workflow/state ordering,
concurrent/replayed/changed actions, rollback, lost replies, cancellation/join,
runtime restart without audio, handoff/operator lifecycle, terminal/redacted replay,
follow-up concurrency, failure/exhaustion/explicit retry, cancelled/crashed leases,
stale ownership, the actual operator CLI, response policy, Gateway correlation and
internal auth. The pipeline case proves acknowledgement audio frames precede its
ordered EndFrame.

The full gate passed **544 tests with one expected opt-in Compose skip** (545
collected), including real PostgreSQL/Redis, migration roundtrip/preservation,
real WebRTC audio/reconnect and both automated Chrome microphone modes. The
voice-enabled mock stack was rebuilt; **6 deployed tests passed**. The deployed
conversation case adds an incomplete-profile handoff, identical replay, scoped
workflow retrieval, operator cancellation and outbox publication. Host and deployed
CLI smoke checks passed. Ruff format/lint (138 files), strict mypy (119 files),
host/Gateway/Conversation `pip check`, Compose config, whitespace and deployed
migration drift passed. Head remains `e8f2a6b3c901`; no schema/model/dependency changes.
All application processes are restored, health is 200, and unpublished/exhausted
outbox and dead-letter counts are zero.

Focused command (recreate/migrate the disposable DB before running):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module14_test \
  .venv/bin/python -m pytest tests/integration/test_workflow_actions.py tests/unit/test_voice_runtime.py
```

Use Module 12's full browser/Compose commands with `module14_test`. The disposable
database was removed after verification. Paid-provider human-microphone quality/
latency and Docker browser-audio reachability remain unverified; the user deferred
their interactive voice test until the frontend is available. The policies and
local reminder effect are deliberately bounded demo behavior. Modules 12–14 remain
uncommitted on top of `d9db88b` at that checkpoint. The user subsequently committed
Modules 12–14 as `4ce9107` and approved Module 15, recorded below.

## Module 15 — central dashboard and live call interface

Complete on 2026-10-05. Inspection found that existing APIs required known
conversation/call IDs, `/live-state` depended on Lead availability, and no HTTP
boundary exposed conversation-owned event metadata or session router health.
The user explicitly approved bounded additive discovery/call/event reads and a
capability-protected session status endpoint. They also approved leaving failure
simulation and evaluation execution unavailable until Modules 16/17.

### Frontend and service boundaries

`apps/dashboard` is a Node 22 Next.js 16.3.8 / React 19.3.0 TypeScript application,
installed as a root npm workspace with a committed lockfile. It adds one central
product interface, responsive layout, keyboard focus states and reduced-motion
support. Production standalone output includes its static assets and runs as a
non-root Node process in Compose. No external fonts/assets or paid providers are
required to render the interface.

The browser sends same-origin `/api/v1/...` and `/api/voice/sessions/...` requests
to a bounded Next server proxy. Its fixed server-only `GATEWAY_URL` is the sole
upstream; it never accesses PostgreSQL, Redis or internal domain services. Paths
are constrained to public Gateway/runtime namespaces, redirects are refused,
timeouts are explicit, responses are uncached and upstream errors are redacted.
Only the owning voice capability is forwarded for voice operations; service/provider
keys are not exposed. Mutations with a foreign Origin are rejected. Host matching
uses the public Host header because standalone Next internally normalizes URLs to
its bind address. Proxy budgets are 25 seconds, 128 KiB request and 8 MiB response;
browser requests have a 30-second deadline. This preserves the existing local-demo
public Gateway boundary without adding a user-identity service.

Lead/Conversation retain all business state, qualification/scoring and workflow
ownership. The runtime retains Pipecat, provider routers and media lifecycle.
There are no new database tables, migrations, indexes, business workers, Redis
consumers, scoring rules, workflow policies or state-machine changes.

### Approved read contracts

| Method/path | Owner/result |
|---|---|
| `GET /v1/conversations?lead_id=UUID&active_only=false&limit=50&offset=0` | Conversation Service → Gateway; `ConversationListResponse` |
| `GET /v1/conversations/{id}/calls?limit=50&offset=0` | Conversation Service → Gateway; `CallListResponse` including independently queried `active_call` |
| `GET /v1/conversations/{id}/events?limit=50&offset=0` | Conversation Service → Gateway; `ConversationEventsResponse` |
| `GET /voice/sessions/{session_id}/status` | Mounted runtime; owning bearer capability required |

Durable reads require the existing internal service authentication and normalized
Gateway request IDs. Limits are 1–100, offsets 0–10,000; listing conversations
requires a lead UUID and does not call Lead. Unknown lead IDs produce an empty
list; unknown conversation IDs produce 404 for calls/events. Results are newest
first with timestamp/UUID tie-breaks. Existing one-active-conversation and call
invariants remain. These APIs depend on Conversation/PostgreSQL, not Lead/Redis;
the original `/live-state` keeps its original authoritative Lead dependency.

Events are limited to Conversation Service's own conversation-scoped outbox
records, including its call/turn/workflow events. Responses expose ID/type/producer,
aggregate ID/type/version, occurred/published times and attempt count only. They
exclude payloads, receipt hashes, errors, transcript/evidence and service tokens.
This is an outbox metadata view, not a new event bus, cross-service analytics read
model or proof of consumption. New events can shift offset pages; polling is not
a lossless delivery subscription.

Session status exposes owning conversation/call IDs, media state, ready/ending/
closed and pending-operation flags, remaining lifetime, configured mock/real modes
and snapshots from the existing LLM/STT/TTS routers. It omits token/SDP/credentials.
Wrong/missing capabilities and missing sessions retain indistinguishable 404s.
Health is explicitly **session/process-local** operational memory, not global
durable provider health or a production fleet metric.

### Dashboard behavior and recovery

- Paginated synthetic lead list, page-local search, profile and create form.
  Lead creation preserves its synthetic key across ambiguous replies; bounded
  reconciliation (at most 1,000 leads / 10 pages with a 15-second admission budget)
  finds an already committed create before reusing that same key. Field correction
  is allowed after definitive validation rejection. No blind new-key create retry.
- Lead selection discovers the active conversation separately from the latest
  100 historical conversations. URL state contains only lead/conversation IDs;
  no capability, transcript or score is persisted in browser storage. A lead
  without a conversation remains selectable/reloadable.
- Start/resume rechecks active conversation and call records. Lost create replies
  reconcile through those reads instead of repeating unguarded mutations. Existing
  outcome workflows must finish before another qualification flow starts.
- WebRTC controls support microphone permission/error feedback, push-to-talk or
  automatic VAD, reconnect, durable recovery and end. Push-to-talk waits for
  **runtime readiness plus connected media**, not the raw peer connection event.
  Full regression reproduced an early-click race; the readiness gate and a focused
  browser regression now cover it. Replaced peers' late callbacks are ignored.
- Ambiguous SDP replies retry the exact cached peer/offer/capability payload.
  Accepted runtime sessions are never recreated during signaling retry. A lost
  initial session-create response cannot recover an unknown capability; the
  existing attach grace/reaper frees that reservation. UI errors expose recovery.
- Media capabilities live only in memory. Page exit/unmount stops tracks and
  requests closure with keepalive; backend reconnect/expiry remains the recovery
  backstop. Failed end keeps its identity retryable while always stopping local
  media; it cannot reopen through a subsequent reconnect. Missing-session 404
  permits local cleanup. Committed workflow auto-closure is observed and released.
- Real-time data-channel notifications trigger durable reads; a 2.5-second
  non-overlapping poll reconciles conversation, calls, history, qualification,
  workflows, events and the browser's session status. Requests from an obsolete
  selection do not overwrite the current conversation. Transcript pagination
  loads 50-message pages up to a 500-message UI bound; interim text is explicitly
  uncommitted, agent text generated rather than playback-confirmed.
- Lead failures clear the current qualification/score and next question and mark
  them unavailable. There is no cached score substitution. Durable transcript/
  call/workflow/event reads remain independently available. Other failed reads
  label retained last-successful snapshots and disable dependent workflow controls.
- Qualification displays status, values/conflicts, backend next question,
  authoritative classification/rule version/calculation time, and coverage `/60`.
  It explicitly states that coverage is not immigration eligibility.
- Handoff assignment/completion/cancellation and follow-up completion/cancellation/
  explicit retry use the existing Module 14 endpoints. Lost operation responses
  retain the original operation UUID/payload for explicit recovery. Reminder
  dispatch remains the existing operator CLI, not a browser timer or new scheduler.
- Call history, current business state, bounded event metadata and provider
  snapshots are visible. Failure simulation and evaluation panels are disabled
  with their assigned module numbers; their future execution is not implemented.

### Local and container launch

The rebuilt stack is running with mock providers. Open **http://localhost:3000**.
Gateway remains at `http://localhost:18000`; its minimal `/voice/` test page remains.

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 DASHBOARD_PORT=3000 \
VOICE_RUNTIME_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock \
  docker compose up -d --build --wait
```

Use Node 22 and npm 10 for host development:

```bash
npm ci
npm run dashboard:check
npm run dashboard:build
# Use port 3001 alongside the running container frontend.
GATEWAY_URL=http://127.0.0.1:18000 npm run dev --workspace apps/dashboard -- --port 3001
```

For the **verified host-media path**, launch Lead/Conversation/Gateway using
Module 12's host variables/commands, with optional real provider settings from
`.env.example`. Point the same frontend at the host Gateway:

```bash
GATEWAY_URL=http://127.0.0.1:8000 npm run dev --workspace apps/dashboard -- --port 3001
```

The UI does not select/change paid providers or transmit their keys. Mock STT/TTS
produce fixture text/tone and the default mock LLM proposes no facts. Scripted
providers in automated tests demonstrate real Lead validation/scoring/workflows.
Human-microphone paid recognition/synthesis/extraction quality/latency and Docker
browser-to-runtime UDP/NAT reachability remain unverified; host Chrome/WebRTC is
the demonstrated media path. Docker frontend hydration/API proxy and image startup
are separately verified. Production TLS/NAT/identity infrastructure remains deferred.

### Verification and continuation

**12 new Python integration cases** (9 real-DB read-boundary cases and 3 production
Next/Chrome actual-service cases) plus **7 frontend Playwright cases** cover scope,
bounds/auth, Lead outage isolation, sanitized event/health snapshots, discovery/
reload, live score/transcript, identical lost-offer replay, reconnect, workflow
acknowledgement/auto media close/operator lifecycle, lost lead-create recovery,
microphone denial, lost workflow-operation recovery, backend readiness, proxy
constraints and responsive layout. The actual voice case asserts one score-history
effect, five durable messages, terminal business outcome and ended original call.
An inspected synthetic browser screenshot verifies the rendered product layout.

Full Python gate: **556 passed, one expected Compose skip** (557 collected), with
real PostgreSQL/Redis, migrations and all five opt-in Chrome cases (two historical
voice modes plus three new production dashboard cases). Frontend type/format/
production build passed; **7 Playwright tests passed**. **6 rebuilt deployment
tests passed**, extending the conversation case through the container Next proxy;
its additionally enabled Chrome variant verifies deployed frontend hydration,
authoritative score and durable acknowledgement against the running containers.
Ruff format/lint (141 files), strict mypy (123 files), host/Gateway/Conversation
dependency checks, Compose config/whitespace and deployed Alembic drift pass.
Head remains `e8f2a6b3c901`. Processes and health are restored; unpublished/exhausted
outbox and dead-letter counts are zero. The disposable `module15_test` was removed.

Recreate/migrate a dedicated test DB before rerunning:

```bash
npm run dashboard:build
RUN_DASHBOARD_BROWSER_TESTS=1 RUN_BROWSER_VOICE_TESTS=1 \
DASHBOARD_NODE=/absolute/path/to/node22 \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
VOICE_TEST_WAV=/absolute/path/to/synthetic-speech.wav \
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module15_test \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py
npm run dashboard:test
```

Frontend tests default to port 3175 (override `DASHBOARD_TEST_PORT`) and accept
`CHROME_EXECUTABLE`. Production dashboard Python cases explicitly skip without
their opt-in/build; skipped cases do not count as browser verification. For the
deployed Module 12/14 command, also set `STACK_DASHBOARD_URL=http://localhost:3000`
and `RUN_DASHBOARD_BROWSER_TESTS=1` to include the container Chrome read path.

Module 15 is uncommitted on top of `4ce9107`; the user's Modules 12–14 commit is
preserved. Stop here. Next is **Module 16 — Useful Observability + Failure Simulation**,
requiring separate authorization.
