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
implemented and reverified; Modules 8–18 are implemented and verified below.
Module 15 is committed as `3d6eab1` on top of `4ce9107`, Module 16 as `0b1842b`;
Modules 17–18 are committed at `2f153d1`. Subsequent voice/intake fixes remain
uncommitted as documented below. The approved roadmap is complete within
the documented fixture/live-smoke limits; additional scope requires approval.

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
- [x] Interface/ownership/local-use documentation (historical `module-8.md` is
  absent from the current checkout; current contracts and limits are recorded here).

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
- [x] Interface/configuration/recovery documentation (historical `module-9.md` is
  absent from the current checkout; current configuration/recovery is recorded here).

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

Module 15 was subsequently committed as `3d6eab1` on top of `4ce9107`.
The following Module 16 work was explicitly authorized after repository inspection.

## Module 16 — observability and failure simulation

Inspected both main documents in full and reconciled against the clean `master`
checkout at `3d6eab1`. The historical Module 15 “uncommitted” note was stale; its
implementation was present in Git. No material architecture/contract conflict
was found. Completed modules, schema, domain state machines, Lead-owned scoring,
Conversation-owned persistence, outbox ordering and media recovery are preserved.

### Measurements and contracts

- Gateway, Lead and Conversation have bounded request metrics and structured JSON
  operation logs: normalized `X-Request-ID`, method + route template, status,
  outcome and duration. Query strings, concrete URL paths, request/response bodies,
  transcript/audio, credentials and raw exception messages are excluded.
- `GET /v1/observability` on Gateway aggregates its snapshot and authenticated
  domain snapshots. Each unavailable/malformed domain snapshot independently
  becomes `null`; diagnostics do not replace `/health` or prove DB readiness.
  Domain copies of this endpoint require the existing service token. No DB query
  is needed to read their request measurements, including during a DB outage.
- Snapshots have `scope: process_local`, service, uptime, at most 128 operation
  labels, and 32 recent operations. Metrics contain count, errors, total/max
  duration in milliseconds. Dashboard computes mean duration from those counters.
  Counters reset on process restart, are not fleet totals or durable domain truth,
  and ordinary polling contributes to measurements.
- Existing capability-protected `GET /voice/sessions/{sid}/status` adds
  `diagnostics`, `faults_enabled`, `active_fault`. Provider attempts record duration,
  provider, request/conversation/turn UUIDs (speech also has call UUID); runtime
  HTTP spans and media/error/recovery signals are content-free. Existing provider
  health remains authoritative for retry/failover/circuit status.
- Relay and consumer logs retain event/aggregate identities and now include
  request/trace IDs on failure/ACK; relay publication includes duration and still
  logs success only after DB COMMIT. No new exporter, collector or tracing service.

### Session fault contract

`DEMO_FAULTS_ENABLED=0` is the default. Enable with `1` only in `APP_ENV=local`
or `test`; other environments reject enabled fault configuration at startup.
Compose forwards the flag to Gateway. No Docker control is exposed to the browser.

`POST /voice/sessions/{sid}/faults` uses the existing session Bearer capability:

```json
{
  "operation_id": "a-new-uuid-for-this-control-action",
  "target": "llm",
  "mode": "unavailable",
  "attempts": 2,
  "duration_seconds": 60
}
```

Targets `llm`, `stt`, `tts` accept `unavailable` or `latency`; `dependency` accepts
only `timeout`. Attempts are strict integers 1–10, TTL 1–60 seconds; extra fields
are rejected. Unavailable fails before vendor dispatch; latency adds one second
within the existing deadline. Only the first configured provider slot is faulted;
routers retain their existing retries, fallback, partial-output rules and circuit
cooldowns. Mock mode has one provider, so exhausting retries produces an error;
two-slot failover is verified with the real router and fixture providers.

Dependency timeout affects only the session's Conversation staged-turn `/apply`
request, before dispatch, under the existing HTTP deadline. Recorded/bound input
remains durable `PENDING`; no provisional score, schema write or new persistence
path is introduced. After reset, **Recover operation** applies the saved facts once
without replaying audio, re-extracting facts or generating an old agent reply.

One active fault per session; a new operation replaces it. Reservations consume
attempts before awaiting, and unconsumed faults expire. Identical operation-ID
retries do not replenish TTL/attempts or rearm after reset/expiry. Changed reuse
or more than 32 distinct control IDs per session returns 409; unsupported
combinations/bounds return 422. Closed/ending sessions cannot arm. Missing/wrong
capabilities, unknown sessions and disabled controls return 404.

`DELETE /voice/sessions/{sid}/faults` clears active injection idempotently; it
retains control receipts and provider circuit health. It stops new injections,
not an already reserved attempt or an existing circuit cooldown. Dashboard exposes
arm/reset, current fault, operational measurements and local **Disconnect media**;
**Reconnect** preserves durable call/conversation identities. Interrupted speech
is not automatically replayed. Provider/status measurements are ephemeral.

### Failure demonstrations and verification

Real process outages stay operator-controlled through the existing Compose tests:
stop Redis to observe durable unpublished outbox attempts; stop/restart relay and
restore Redis to observe publication; stop PostgreSQL to see correlated 503s,
failed request counters and unchanged versions; stop Lead to see isolated missing
snapshot/live scoring while durable transcript reads continue, then restore.
Use `docker compose logs event-relay` for publication/worker signals and the
dashboard event delivery view for durable delivery state. Fault controls do not
pretend to simulate an actual database/Redis outage.

Verification: **575 Python tests passed**, with one deployment-only case skipped
in that run; existing real PostgreSQL/Redis, migrations, Chrome/WebRTC and new
dashboard fault/reset/recovery/reconnect were included. **7 frontend Playwright
tests passed**. Strict mypy (**122 source files**), Ruff lint/format (**146 files**),
TypeScript/Prettier, production frontend build, host dependency and whitespace
checks passed. Pipecat's existing audioop/importlib deprecation warnings remain.
The old browser reconnect scenario now waits for durable agent output before
reconnecting, since scoring completion precedes agent-output persistence and
reconnect legitimately cancels unfinished output; its message-count assertion is
preserved. The new fault test uses a precise status locator.

Reproduction uses a dedicated migrated test DB, Node 22 and fixture audio, as in
Module 15; replace its DB name with `module16_test`. For deployment verification:

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
VOICE_RUNTIME_ENABLED=1 DEMO_FAULTS_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock \
  docker compose up -d --build --wait

POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
VOICE_RUNTIME_ENABLED=1 DEMO_FAULTS_ENABLED=1 RUN_EVENT_STACK_TESTS=1 \
RUN_DASHBOARD_BROWSER_TESTS=1 STACK_DASHBOARD_URL=http://localhost:3000 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai \
STACK_REDIS_URL=redis://localhost:56379/0 STACK_GATEWAY_URL=http://localhost:18000 \
  .venv/bin/python -m pytest \
  tests/integration/test_event_stack.py tests/integration/test_compose_conversation.py
```

**6 rebuilt Compose deployment tests passed**, including actual Redis/relay,
PostgreSQL and Lead outages, correlated failed-request metrics while DB is down,
independent missing Lead snapshot, voice capability/fault/reset/control-replay
checks and production Next proxy/Chrome reads. Rebuilt Gateway and Conversation
image dependency checks passed; deployed Alembic check reports no schema drift.
All services were restored; Gateway health is 200, PostgreSQL/Redis are healthy,
unpublished/exhausted outbox and dead-letter counts are zero. The disposable
`module16_test` was removed. Local mock dashboard remains on port 3000, Gateway
18000 with demo faults enabled; default configuration remains disabled.
No new migration; head remains `e8f2a6b3c901`. Paid-provider human microphone
quality/latency and Docker-to-browser UDP/NAT audio still require a live smoke
test; fixture success does not establish those claims.

Module 16 was subsequently committed as `0b1842b`. The user then authorized the
next module; Module 17 is recorded below. No Module 18 implementation is included.

## Module 17 — repeatable evaluation, voice E2E and basic concurrency

Inspected both main documents completely, historical verification, current
contracts/models/state graphs/runtime/tests and clean Git state at `0b1842b`.
No material architectural conflict. Existing scoring and qualification authority,
staged/user/agent/workflow contracts, persistence, migrations and state machines
are unchanged. This module organizes and extends the existing verification path.

### Evaluation command and report

`python -m scripts.evaluate --suite core|voice|all --report PATH` runs fixed pytest
presets from the repository root. `make evaluate` defaults to core;
`make evaluate EVALUATION_SUITE=all` includes media/browser verification. Install
the existing dev/voice/browser-test dependencies; no new package/service is added.
The command explicitly selects local deterministic provider modes. It enables
the existing browser opt-ins for voice/all, and clears inherited `PYTEST_ADDOPTS`
filters so an accidental `-k/-m/--lf` cannot silently narrow the preset.

Core covers multi-turn callers, staged qualification/score/next-question/replay,
workflow policies/recovery, dependency fault reset, LLM adapter failover/context
and TTS adapter failover/no partial replay. Voice covers production Next dashboard
with actual Gateway/domain/PostgreSQL, WebRTC audio/reconnect, both Chrome PTT/VAD
modes, fault recovery and two simultaneous dashboard calls. These are deterministic
integration assertions, not a semantic or speech-quality judge.

Default report is `test-results/evaluation.json`, under the already ignored test
artifact directory. JSON format v1 includes suite, revision, dirty-worktree flag,
UTC start/end, duration, provider mode/scope, gate status, pytest exit code,
collection errors, counts and each collected case's status/duration. Parameter
labels are hashed because pytest IDs can contain fixture text. Reports exclude
capture, tracebacks, exceptions, transcripts/audio, credentials, environment values
and assertions' input data. Normal pytest console output retains its diagnostic
behavior; the JSON is the content-free shareable result. Replacement is atomic.

Exit 0 requires a nonempty collected set with every case passing, no collection
errors and pytest exit 0. Any skip/xfail/not-run, fixture error, failure, interruption
or collection error makes the gate `failed_or_incomplete` and exit 1. Missing DB
or browser prerequisites cannot count as verified integration. `core`/`voice` are
explicit partial presets; only `all` is the complete Module 17 evaluation preset.
This command does not provision infrastructure, migrate/drop DBs or stop containers.

The existing central dashboard replaces its disabled Module 17 placeholder with
the available operator command. Evaluation remains a repository CLI/JSON artifact;
the browser does not launch pytest or publish reports. Foundational evaluation
tables remain reserved, with no competing evaluation store/service, new schema,
post-call worker or live-call grading introduced.

### Added coverage

- Multi-turn education provisional → confirmed → contradictory → explicit
  resolution asserts scores 0/10/0/10, answer statuses and backend questions.
- Urgent caller with false budget/job-offer and zero experience asserts scores
  10/20/30/40, preserved values and urgency-aware questions. Each turn exercises
  primary LLM failure/secondary execution in QualifiedDialogue, then simultaneous
  stale-admission/apply retries. Exact messages/transcripts, contiguous sequence,
  score history and unique outbox effects prove no duplicates.
- Two real-DB runtime sessions process concurrently: an injected apply timeout
  leaves one bound PENDING turn while the other completes. Fresh runtime recovery
  finishes the first once without old audio/reply; lead values/scores/call scope
  stay independent.
- Two production-dashboard Chrome peers call concurrently through one Gateway.
  Both receive audio and produce separate durable transcript/score effects. Ending
  one preserves the second's readiness/reconnect. Exact call/message/history
  assertions confirm isolation; this is a two-call check, not a load benchmark.
- Actual subprocess pytest report tests cover pass, skip, assertion/setup/teardown/
  collection failures, hashed fixture IDs, and the CLI missing-DB/inherited-filter
  guard. Frontend verification checks the command and mobile layout.

### Reproduction and verification

Create/migrate a dedicated DB first (as in `docs/testing.md`); keep it separate
from the demo database. Build the production dashboard using Node 22. Example:

```bash
npm run dashboard:build
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module17_test \
DASHBOARD_NODE=/absolute/path/to/node22 \
CHROME_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
VOICE_TEST_WAV=/absolute/path/to/synthetic-speech.wav \
  .venv/bin/python -m scripts.evaluate --suite all --report test-results/evaluation.json
```

Core verification passed **75 cases with no skips**; complete evaluation passed
**83 cases with no skips**. **7 frontend Playwright tests passed**. Ruff checks
(**150 files**), strict mypy including scripts/Alembic (**132 source files**),
TypeScript/Prettier, production build, dependency and Compose/whitespace checks
passed. Full regression passed **586 tests** with one separate deployment-only
skip. The **8-case report suite** passed separately after adding the final CLI
missing-DB/inherited-filter guard (seven of these cases were already included in
the full run). Existing Pipecat audioop/importlib deprecation warnings remain.
**6 deployment tests passed** against the rebuilt dashboard and existing verified
voice-enabled application images, including production Next proxy/Chrome reads
and real Redis/relay/PostgreSQL/Lead outage recovery. Deployed Alembic reports no
drift. Final exact-type assertions for scenario values also passed in the focused
three-case integration rerun. Restored Gateway health is 200; unpublished/exhausted
outbox and dead-letter counts are zero. The disposable `module17_test` was removed;
the mock dashboard remains on 3000/Gateway on 18000 with demo faults enabled.
Reports retained locally: `test-results/module17-core.json` and
`test-results/module17-all.json` (75 and 83 passed, no skips); both record the
uncommitted working tree on top of `0b1842b` and remain ignored artifacts.

No migration; head remains `e8f2a6b3c901`. Paid-provider human microphone semantic/
voice quality and latency, Docker browser-to-runtime UDP/NAT audio and broad load
testing remain unverified. Host synthetic Chrome/WebRTC proves transport/control
and backend behavior, not recognition or pronunciation quality. The CLI report
is local test evidence, not a durable grade of an arbitrary live conversation.

Stop after Module 17. Next is **Module 18 — Final hardening / demo runbook**,
requiring separate authorization after the module report.


## Module 18 — final hardening and operational/demo runbook

Inspection started from `0b1842b` plus the completed, uncommitted Module 17 work.
The actual repository matched the latest handoff contracts; README/architecture
status text was stale. No material architectural conflict was found. Module 17
was preserved rather than repeated. Module 18 changes no domain schema, graph,
score ownership, provider behavior, or service boundary.

### Implemented hardening

Python application, relay and migration images run as dedicated UID/GID 10001
with a writable home, while installed code stays root-owned/readable. Dashboard
remains `node`. Compose startup waits for authenticated Lead/Conversation health,
then Gateway and dashboard health. Domain probes read the service token from the
container environment, never embed its value in the probe command/output.

Health scopes are deliberately precise: Lead and Conversation probe PostgreSQL;
Gateway probes Lead/DB readiness; dashboard probes page serving. Conversation
health does not depend on Lead, preserving history access during its outage.
Relay has no synthetic health endpoint: inspect its backlog, stream/group state,
and dependency logs to distinguish an alive process from delivery progress.
Docker unhealthy status does not restart applications automatically.

The evaluation writer now uses unique temporary files in the destination
folder before atomic replacement. Concurrent writers cannot remove each other's
temporary file; last completed replacement wins. A synchronized concurrency
case verifies complete reports and temporary cleanup.

`make demo` / `python -m scripts.demo` reuses the verified production-dashboard
Chrome scenario, without a new fixture runtime mode or business API. It checks
WebRTC audio, authoritative score 10, lost-offer retry, reconnect, handoff,
operator completion and reload through Gateway and domain persistence. It runs
with deterministic provider fixtures against a disposable database, not paid
providers or the Compose media path. Missing DB/build/browser prerequisites,
skips, errors or empty results fail. `--report` selects JSON output;
`--screenshot` captures the existing browser scenario's scored-call view.
Reports/screenshots default under ignored `test-results`; screenshots contain
synthetic fixture content and are not included in content-free JSON reports.

README, architecture, reliability and ADRs now describe the completed boundaries,
configuration/provider modes, current limitations and operating commands. Existing
historical missing Module 8/9 doc links point to the main plan. No new per-module
document was created; PROJECT_HANDOFF remains ignored/untracked.

### Repeatable regression and demo gate

Install Python 3.12 and Node 22 dependencies, optional voice extras, Chrome, and
build the production frontend as documented in README. Use a disposable migrated
PostgreSQL database and a dedicated Redis test DB. Integration tests can rebuild
schema, interrupt connections, and redact fixture rows: never use operator data.
Keep CLI runs sequential when sharing one test database.

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test
export TEST_REDIS_URL=redis://localhost:56379/15
export DASHBOARD_NODE=/absolute/path/to/node22
export CHROME_EXECUTABLE=/absolute/path/to/chrome
export VOICE_TEST_WAV=/absolute/path/to/speech-fixture.wav
make format-check lint typecheck infra-validate
.venv/bin/python -m pip check
npm run dashboard:check
npm run dashboard:build
npm run dashboard:test
RUN_DASHBOARD_BROWSER_TESTS=1 RUN_BROWSER_VOICE_TESTS=1 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py \
    --ignore=tests/integration/test_compose_hardening.py -q
make evaluate EVALUATION_SUITE=all
.venv/bin/python -m scripts.demo --report test-results/demo.json --screenshot test-results/demo.png
```

The full pytest gate has one expected deployment-only skip from
`test_compose_conversation.py`; that case must pass in the separate deployment
gate below. The complete evaluation preset must have no skips. Automatic VAD
needs speech audio in `VOICE_TEST_WAV`; an arbitrary tone WAV is insufficient.
On this macOS host Node 22 is `/Users/akash/.nvm/versions/node/v22.16.0/bin/node`,
Chrome is `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, and the
speech fixture used for verification is `/private/tmp/module12-speech.wav`.
That temporary WAV is not a portable repository asset: provide your own speech
fixture on another machine. The single scripted demo uses its existing generated
microphone fixture and does not require that external VAD WAV.

### Isolated clean-start and deployment gate

Compose names its network/volumes explicitly. A different `-p` alone does **not**
isolate data. Use an override and unused host ports; choose unique resource names
for each audit. This recipe creates disposable audit resources only:

```bash
cat > /tmp/voice-audit.override.yml <<'YAML'
networks:
  voice_ai:
    name: voice_audit_network
volumes:
  postgres_data:
    name: voice_audit_postgres_data
  redis_data:
    name: voice_audit_redis_data
YAML
export COMPOSE_PROJECT_NAME=voice-audit
export COMPOSE_FILE="$PWD/docker-compose.yml:/tmp/voice-audit.override.yml"
export POSTGRES_PORT=55433 REDIS_PORT=56380 API_GATEWAY_PORT=18001 DASHBOARD_PORT=3002
export VOICE_RUNTIME_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock DEMO_FAULTS_ENABLED=1
# These credentials must match the disposable stack and its test URLs.
export POSTGRES_USER=voice_ai POSTGRES_DB=voice_ai POSTGRES_PASSWORD=voice_ai_dev_password
export APP_ENV=local LEAD_SERVICE_AUTH_TOKEN=local-lead-service-token
 docker compose up -d --build --wait --wait-timeout 180
 docker compose exec -T lead-service python -m lead_service.seed
 docker compose exec -T lead-service python -m lead_service.seed
```

The migration job exits successfully, so `exec db-migrate` is unavailable after
startup. Inspect its logs and run the head/drift CLI in the live Lead image:

```bash
docker compose logs db-migrate
docker compose exec -T lead-service alembic current
docker compose exec -T lead-service alembic check
RUN_EVENT_STACK_TESTS=1 RUN_DASHBOARD_BROWSER_TESTS=1 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55433/voice_ai \
STACK_REDIS_URL=redis://localhost:56380/0 STACK_GATEWAY_URL=http://localhost:18001 \
STACK_DASHBOARD_URL=http://localhost:3002 \
 .venv/bin/python -m pytest tests/integration/test_event_stack.py \
 tests/integration/test_compose_conversation.py tests/integration/test_compose_hardening.py -q
docker compose ps
docker compose exec -T event-relay python -m event_relay inspect
```

Expect migration head `e8f2a6b3c901`, no new upgrade operations, seed counts
21/0 then 0/21 on a fresh DB, healthy HTTP apps, zero unpublished/exhausted
outbox and no DLQ entries after recovery. Twelve deployment checks verify Redis,
relay, PostgreSQL and Lead stop/restart, actual dashboard REST integration,
production token guards, reserved-character credentials, four non-root Python
identities and both authenticated domain health contracts. They restore stopped
processes in cleanup. Run only on the isolated stack; they interrupt dependencies.

After retaining needed artifacts, remove **only those disposable resources** with
`docker compose down --volumes` while the audit project/override variables remain
set. Then `unset COMPOSE_PROJECT_NAME COMPOSE_FILE` and restore local port/mode
settings before operating the ordinary stack. Do not run this cleanup against
existing demo volumes. Drop the dedicated test DB only after all test processes
exit. Reports/screenshots survive because they are host artifacts.

### Operator recovery and demonstration

1. Start the full mock stack and seed leads. Select a lead, connect a call and
   inspect transcripts/diagnostics. Fixture speech does not understand natural
   answers; run the scripted demo for confirmed-fact/scoring/workflow evidence.
2. With explicit local fault enablement, select a supported session fault in the
   dashboard. Observe controlled errors, provider attempts and durable input.
   Reset the fault and recover the original pending turn. Verify no duplicate
   message, qualification, score event, workflow action or old audio replay.
3. For a real dependency outage, inspect `docker compose ps`, logs, and relay
   inspection. Restore PostgreSQL first, then Lead/Conversation, Gateway and
   dashboard if needed. Use `up -d --wait` with the same configuration; startup
   health dependencies now refuse admission until ready. History can stay
   available during a Lead outage; scores must show unavailable.
4. After Redis/relay recovery, inspect the outbox. Exhausted rows require explicit
   `docker compose exec -T event-relay python -m event_relay retry-event EVENT_UUID`.
   For DLQ use the bounded `replay-dlq` CLI documented in the relay README.
   Retain the original event identity; consumers deduplicate after DB commit.
5. Reconnect/reload and verify durable history. A Gateway restart loses process
   diagnostics, capabilities and media; use the existing supported recovery flow,
   not an assumption of session/circuit persistence or old reply replay.
6. Run the scripted demo/evaluation and retain JSON/screenshot artifacts with
   their Git revision/dirty marker. Fixtures demonstrate boundary correctness,
   not vendor acceptance, speech quality, live-call grading or load capacity.

Known limits remain: one Gateway process, bounded session admission, explicit
retention/retry operators, local shared-secret service authentication, no public
production auth/telephony/business integrations. Paid-provider microphone and
Docker browser UDP/audio reachability need a live smoke test with suitable
credentials/network. OpenAI Realtime TTS remains deferred. No new roadmap module
or additional product scope is authorized after Module 18.


### Module 18 verification — 2026-10-05

- **590 full Python regressions passed**, one deployment-only skip, eight existing
  Pipecat/Python deprecation warnings; 591 collected with event-stack/hardening
  deployment files excluded. The skipped Conversation deployment case passed in
  the separate gate. This includes migration roundtrip, rebuild, data preservation
  and drift, dependency recovery, live WebRTC, manual/VAD Chrome, and concurrency.
- **12 rebuilt isolated Compose deployment checks passed**, with voice-enabled
  mock/fault configuration and production Next dashboard; six existing outage/
  integration cases plus four non-root identities and two authenticated health
  probes. Fresh uniquely named volumes/network on 55433/56380/18001/3002 reached
  healthy readiness. Two seed runs inserted 21 then 0 leads. Migration head
  `e8f2a6b3c901`, no new upgrade operations; outbox unpublished/exhausted 0/0 and
  DLQ 0 after recovery.
- **83/83 complete evaluation cases passed**, no skips/errors/not-run cases,
  retained at `test-results/module18-all.json`. **Dedicated demo passed (1/1)**
  with `test-results/module18-demo.json` and inspected synthetic screenshot
  `test-results/module18-demo.png`. Reports record revision `0b1842b` and dirty
  working tree; they are evidence of this uncommitted work, not a later commit.
- **7 frontend browser checks passed**. TypeScript/Prettier, production dashboard
  image build, Ruff formatting (**152 files**)/lint, strict mypy (**134 source
  files**), `pip check`, Compose validation and Git whitespace checks passed.
- Existing local stack redeployed with non-root images/readiness without replacing
  volumes; healthy dashboard at 3000, Gateway at 18000, PostgreSQL at 55432 and
  Redis at 56379. Mock voice/fault modes enabled for local demonstration. The
  isolated audit stack/volumes and disposable `module18_test` DB are removed after
  final gates; ignored reports/screenshots retained.

No material architecture conflict or domain schema/behavior change. Module 17's
completed changes are still present and uncommitted together with Module 18 on
`0b1842b`; PROJECT_HANDOFF remains ignored/untracked. Paid-provider microphone
quality/acceptance and Docker browser audio/UDP reachability remain unverified,
not concealed by fixture successes. No additional module is approved.

For a combined Modules 17–18 commit after reviewing `git diff` and untracked
source files (the handoff and test artifacts stay ignored):

```bash
git add Makefile README.md apps docker-compose.yml docs pyproject.toml services workers scripts tests
git commit -m "feat: complete evaluation and final hardening" \
  -m "Add deterministic evaluation and Chrome demo gates, authenticated Compose readiness, and non-root Python containers. Verify recovery and concurrency; document current architecture, configuration, runbook, and live-smoke limits."
```


## Live OpenAI GPT-5 mini compatibility fix

After Modules 17–18 were committed as `2f153d1`, a live user turn reached Sarvam
STT and persisted as PENDING, but the OpenAI step failed. The greeting is a
separate deterministic runtime output, so its successful Sarvam TTS playback did
not prove the LLM request worked. The configured model was `gpt-5-mini`.

A minimal synthetic diagnostic using the running Gateway confirmed HTTP 400,
`unsupported_value`, parameter `temperature`: the adapter sent the contract's
`temperature=0` to a model that rejects that override. Credentials and vendor
error bodies were not logged. The OpenAI adapter now omits temperature and sets
`reasoning_effort=minimal` only for `gpt-5-mini` and its `2025-08-07` snapshot.
Other OpenAI models and OpenRouter keep their prior sampling/request payloads;
the default model, API endpoint, domain tools, idempotency, pending-turn recovery,
scoring, state machines and schema are unchanged.

Official compatibility guidance:
https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2
(GPT-5.2 parameter compatibility explicitly describes the older GPT-5 mini
restriction). Minimal reasoning is supported by the original GPT-5 family and
fits this voice workload's bounded generation budget. This does not promise
sampling determinism; business validation/scoring remains deterministic in Lead.

Verification: **85 adapter/router/configuration cases passed**, including a wire
fixture that rejects temperature and verifies a complete tool stream, snapshot
support and unchanged other-provider parameters. **33 staged qualification/
recovery cases passed** against a disposable migrated PostgreSQL DB. Ruff
format/lint, strict mypy134 files and Compose validation passed. Two small live
synthetic requests through the edited adapter completed successfully: plain text
in 3.455s (10 output tokens), qualification tool proposal in 2.436s (46 tokens).
These are live API compatibility checks, not a full human microphone, scoring or
Docker audio smoke certification. The four-second configured attempt deadline
has little latency margin; if a live attempt times out, set
`LLM_ATTEMPT_TIMEOUT_SECONDS=12` in the root .env and recreate Gateway, keeping
existing request/whole-loop deadlines. No automatic timeout/config change made.

For code updates rebuild Gateway, not dashboard:
`docker compose up -d --build --no-deps --force-recreate api-gateway`.
A restart disconnects media and loses process-local session diagnostics but
preserves durable conversations/transcripts. Hard refresh, start/resume the same
lead, recover its original pending turn, then speak a new turn. Recovery finishes
saved business work without replaying old speech. Do not delete pending input or
invent scores to bypass the failure. The user's separate .env.example model
change is preserved. PROJECT_HANDOFF stays ignored/untracked.


Deployment follow-up: the normal Gateway Dockerfile rebuild failed while PyPI
was downloading voice dependencies (`ReadTimeoutError` from files.pythonhosted.org).
The running image was retained, and an adapter-only image was built from that
local base with the edited openai.py, preserving all tested installed dependencies
and UID10001. No repository Dockerfile changed. Local tags:
`lead-operations-platform-api-gateway:gpt5-mini-fix` and rollback
`lead-operations-platform-api-gateway:before-gpt5-mini-fix`; latest points to the
adapter-only fix. Gateway was recreated with --no-build and reached healthy status.
A future normal --build will include the source fix but still requires a working
PyPI connection. This temporary deployment method is not a dependency rebuild gate.

Aggregate inspection found three PENDING user turns attached to terminal calls
(two ENDED, one FAILED). Permission to collect their identities for recovery was
declined; no pending input was changed or deleted and no operator recovery ran.
Do not claim a restart clears them or that replayed audio will be delivered.
Existing QualifiedDialogue.recover supports durable replay under the original
call identity, but a fresh media call uses a different call identity and cannot
silently adopt the old pending turn. Those three need a separately authorized
recovery using existing guarded persistence, before their conversations can
continue normally. No state-machine or recovery admission changes were added.
For a fresh live test after deployment, hard-refresh the dashboard, create/select
a lead without pending work, start a call and speak. Confirm the new user turn
becomes APPLIED and OpenAI health reports success; the two synthetic live checks
alone are not evidence of this full browser interaction.


Final deployed-image verification confirmed model gpt-5-mini, real LLM/voice
modes, reasoning_effort minimal, no temperature field and UID 10001. Gateway is
healthy and still published on localhost:18000; dashboard remains localhost:3000.
The disposable live_fix_test database was dropped. Fix, tests and main-doc notes
are uncommitted on `2f153d1`; the user's .env.example edit is separate and preserved.


### Approved conversational voice presentation (2026-10-07)

The user approved replacing the exact-question presentation rule after inspection
showed that QualifiedDialogue discarded generated speech whenever Lead returned
next_question. Suitable model speech now reaches the existing durable agent-write
and TTS path. The prompt asks for brief, warm responses grounded in the latest
caller turn/history, help with uncertainty, natural transitions and at most one
primary question, guided by Lead's authoritative next_field and known answers.
This approved exception supersedes earlier notes requiring all incomplete-profile
speech to equal the raw Lead question; it does not move qualification ownership.

Lead still validates proposals, confirms facts, prioritizes conflicts, selects the
next qualification field and computes the score. For a provisional or conflicting
selected field, generated speech must include Lead's complete next_question
verbatim, preserving the existing explicit English `I confirm` protocol. A missing
protocol or more than one question mark uses the Lead question unchanged. Suitable
model candidates also pass the existing Conversation output safety policy;
rejected candidates use the Lead question when available, otherwise the existing
policy replacement. Policy outages fail without persisting unreviewed model text.
Empty, truncated or otherwise invalid generations retain existing failure behavior.

Accepted model speech records its actual provider/model; Lead-question fallback
retains voice-runtime / qualification-policy-v1 attribution. Existing contracts,
tables, tools, workflow acknowledgements, request routing, staged user persistence,
stable agent IDs, persistence-before-speech and recovery remain unchanged.
Presentation guards are deliberately narrow: question-mark counting is not a
semantic question/field classifier, and prompt guidance is not a guarantee of
vendor response quality. Confirmation remains explicit rather than accepting yes/no.

New database-backed tests cover contextual uncertainty/cost/background replies,
confirmed history and all six fields through score 60, verbatim confirmation,
conflict priority, safety fallback, multiple-question fallback, policy outage and
lost agent-write acknowledgement recovery without another generation. Existing
qualification/evaluation/browser fixtures now exercise conversational prefixes
while preserving the authoritative next question and all prior business assertions.

Verification: focused conversation/qualification/workflow/evaluation suites:
**78 passed**. Ruff formatting/lint and strict mypy: **135 files passed**.
Full regression with Chrome dashboard and voice opt-ins: **607 passed, 1 skipped**
in 144.02s. The skipped test is the opt-in Compose conversation test;
process-level event-stack outage and Compose hardening files were excluded to
avoid restarting the live stack. Browser speech/LLM providers were fixtures;
this run does not certify paid-provider microphone quality. Existing third-party
Python deprecation warnings remain. The disposable conversation_style_test
database was removed after verification.
PROJECT_HANDOFF remains ignored/untracked. No live lead or pending-turn recovery
was performed. Deploy this runtime source by rebuilding/recreating api-gateway;
rebuilding dashboard alone does not change the backend dialogue. These source
changes do not certify a paid-provider microphone session.


### Live turn detection and Sarvam continuation fix (2026-10-07)

The user requested fixes for frequent invalid_output, calls waiting for the Stop
button, and text-only responses to microphone checks. Recent provider logs place
the observed invalid_output in sarvam-stt before qualification/LLM processing.
A synthetic 28-second live Sarvam probe reproduced it: the provider finalized
segment 0, then emitted transcript.partial with utterance_idx 1. The adapter
previously rejected all segments after the first final. A single manually bounded
application turn can therefore contain multiple vendor segments. This observation
supersedes the earlier one-vendor-segment assumption, without changing the speech
contracts or business turn identity.

The adapter now maps contiguous vendor segments to distinct immutable transcript
segments, keeps stable partial/final identities and consumed-audio coverage, and
reports the actual final count. It receives continuation events while audio is
still being sent, rather than waiting for input EOF at the first segment final.
Session completion still joins/validates the sender. Duplicate finals, index gaps,
unresolved partials, malformed frames and bounded-stream violations remain errors.
VoiceProcessor already joins final segment text into one staged user turn; no
separate qualification update is created for each vendor segment. The same live
synthetic probe using the edited adapter completed with two final segments.
Only event types/indices/counts were retained, not vendor transcripts or keys.

Dashboard defaults to automatic Silero detection (existing 0.4s stop-silence
threshold). Explicit Push-to-talk remains available before connecting and still
requires Start/Stop by design. Existing PTT browser cases now select it explicitly.
A failed STT task releases its active feed; nonpending errors allow a fresh
utterance and give a speak-again instruction rather than misleading durable
recovery advice. Pending business operations retain existing recovery gating.
The qualification dialogue prompt identifies a live browser voice call and
microphone-derived user transcripts: acknowledge received words for audibility
checks, without inventing volume/noise/microphone-quality assessments.

Lead ownership, confirmation/conflict/scoring, safety policy, staged persistence,
idempotency, workflow acknowledgements, provider routing, schema and state graphs
are unchanged. No persistent vendor session, speculative LLM processing or
uncommitted speech streaming is introduced. Existing unrelated uncommitted
changes and the user-owned .env.example edit are preserved; handoff stays ignored.

Verification results are recorded after the final gates below. Live Sarvam probes
used synthetic audio and no business persistence; physical-microphone quality and
all vendor failure shapes remain outside what those probes establish.

Verification: **613 full Python cases passed, 1 deployment-only skip**, with
process-level event-stack/hardening files excluded and Chrome voice/dashboard
opt-ins enabled. **8 frontend Chrome cases passed** for defaults, explicit PTT,
readiness, nonpending speech retries and pending-operation recovery gating.
Ruff formatting/lint, strict mypy (**136 files**), TypeScript/Prettier and the
production dashboard build passed. The additional real dashboard automatic-VAD
case passed separately: synthetic microphone → silence endpoint → staged user
persistence → Lead score 10 → durable agent reply, with no Start/Stop clicks.
Its STT fixture now honors the runtime's reserved 45-second live-input deadline;
previously the mock replacement inadvertently used four seconds. Existing PTT,
reconnect, workflow, fault and concurrent-call cases are rechecked below.

The live edited-parser test accepted two immutable final segments from the same
28-second synthetic turn and a consistent session completion. Original parser
probes reproduced the rejection at continuation index 1. A separate short
synthetic request succeeded with the original parser. This is a targeted vendor
compatibility fix, not a promise that every invalid_output cause is eliminated.

Activate source updates with:
`docker compose up -d --build --no-deps --force-recreate api-gateway dashboard`.
End the call first, hard-refresh the dashboard, and leave Push-to-talk unchecked
before starting/resuming a call. Gateway/frontend sources are not automatically
loaded into already-running containers. No live business input was modified,
no terminal-call pending turn recovered, and no schema migration added.

Final dashboard regression after the fixture correction: **6/6 passed** (54.17s),
including the new default automatic-VAD case and existing lost-offer/reconnect,
workflow, fault/reset/recovery and concurrent-call checks. Final static/whitespace
checks passed. The dedicated voice_turn_fix_test database was removed afterward.
Changes remain uncommitted. An unrelated untracked docs/ponytail-audit.md appeared
during verification and was left untouched; this task only updated the two main
project documents.


### Turn admission, internal-output guard and microphone threshold fix (2026-10-07)

The user reported intermittent turn_busy, internal tool names in spoken next-step
answers, and background disturbances interrupting playback. Inspection found that
begin_utterance cancelled/flushed the current response before checking for active
STT or pending business work. This could strand an accepted staged turn and create
a recovery requirement merely from a second speech onset. No material conflict
with the completed architecture was found; the fix preserves its boundaries.

Turn admission now checks active STT and accepted pending input/agent/workflow
writes before cancelling media. An onset while those operations are progressing
is ignored without a false dependency error, audio flush, duplicate turn or
cancellation. Caller audio during that busy interval is not queued as another
business turn. Once durable work is finished, normal barge-in can still cancel
uncommitted generation or playback. A failed saved operation still reports
turn_busy with retry_required and must use the existing recovery protocol.
Cancellation is rechecked for an ambiguous pending acknowledgement after joining
shielded writes. No pending live turn was deleted or recovered by this task.

The Conversation Service output screen additionally rejects the six current
internal tool names and three workflow identifiers, case-insensitively, including
names embedded in JSON/backticks. It retains existing unsupported-claim decisions
and the response contract; this remains a conservative lexical screen rather than
a general semantic safety classifier. QualifiedDialogue still uses the exact
Lead-selected question as the fallback and persists reviewed output before TTS.
Its prompt forbids exposing functions, arguments or execution plans, answers
next-step questions in everyday language, and permits a relevant answer without
forcing another qualification question. Existing exact confirmation/conflict
protocols, qualification/scoring ownership and workflow acknowledgements remain.
Model presentation is prompt-guided; fixture tests do not certify every live model
response or guarantee that arbitrary paraphrases of internal details are caught.

Runtime uses native Silero/Pipecat volume gating with VOICE_VAD_MIN_VOLUME, validated
as a finite number between 0 and 1 (default 0.65, previously native 0.60). Compose
passes it to api-gateway. Invalid values fail runtime startup. This is Pipecat's
normalized smoothed loudness scale, not a raw amplitude percentage or a fixed dB
value. Existing confidence 0.7, onset 0.2s and silence endpoint 0.4s are retained.
Raise gradually (for example 0.70) if quiet disturbances still trigger interruptions;
lower if your own quiet speech is missed. Native speech confidence and sustained
onset must also pass. Loud background voices can still count as speech, so room
and microphone calibration remains a physical-microphone smoke-test requirement.
No extra noise-filter package, speech contract, schema or state transition was added.

Verification: broad regression with Chrome voice/dashboard opt-ins yielded
627 passed, 1 deployment-only skip, and one new test assertion failure: it required
exactly one question even for the newly permitted zero-question next-step reply.
Updated that assertion to the existing at-most-one-question contract. All 78
affected dialogue/workflow/runtime/VAD cases then passed, including an additional
database-backed test: pause staged qualification binding, deliver another speech
onset, resume, and verify one user/agent pair APPLIED and Lead-owned score 10.
Tests also cover active STT/workflow admission, saved-turn recovery gating, genuine
barge-in, tool leakage replacement before persistence, invalid threshold settings,
and native volume/confidence/onset/silence gating. All browser cases passed in the
broad run. Process-level event-stack outage and Compose hardening tests were
excluded to avoid restarting the live stack. Ruff formatting/lint and strict mypy
passed (131 source files). Existing third-party deprecation warnings remain.

Changes are uncommitted and not automatically deployed. End the call, then run:
`docker compose up -d --build --no-deps --force-recreate conversation-service api-gateway dashboard`
Hard-refresh and leave Push-to-talk unchecked for automatic endpointing. Updating
only dashboard cannot activate the runtime or server-side output guard changes.
PROJECT_HANDOFF remains ignored/untracked; the user-owned .env.example changes and
unrelated untracked docs/ponytail-audit.md are preserved. Only the two existing main
project documents receive this implementation note.


### Approved immigration intake: Step 1 rejection semantics (2026-10-07)

The user approved two sequential steps: first prevent rejected LLM qualification
proposals from stalling voice conversations; after verification extend non-scoring
immigration intake with target_country and visa_type. Read both main project files
completely and reconciled code/Git at 2f153d1 with earlier uncommitted voice fixes.
Those changes, the user-owned .env.example edit and unrelated ponytail-audit.md
are preserved. No material conflict was found for Step 1's additive resolution.

Step 1 reuses the existing RECORDED -> BOUND -> APPLIED staged path and empty-fact
application; no new schema/state graph or qualification/scoring ownership change.
ProposedFacts adds on_rejection (default reject; optional continue_without_facts)
and a bounded extraction_rejection descriptor for empty continuation batches.
The model's tool schema does not expose these policy switches. Existing API callers
retain strict 422 rejection with no bound receipt and may correct before binding.
Historical default-request binding hashes exclude the new default fields, preserving
receipt replay. Opt-in continuation settings/diagnostics are covered by its immutable
request fingerprint; changed reuse still conflicts.

The runtime opts into continuation. Malformed/unsupported extraction shapes and
invalid-output extraction streams produce an empty batch with structured diagnostics;
other provider failures retain pending work and recovery. Lead still independently
validates the entire supported batch and never partially accepts it. Only its typed
422 proposal rejection can be bound as an empty batch for the opt-in voice path.
Unclassified rejections, identity/version conflicts, outages, timeouts and uncertain
writes do not become silent success. The caller transcript remains intact, reviewed
agent output can follow, and a subsequent new turn is admitted. Empty application
reads authoritative qualification without mutating answers/score/history.

Lead validation now supplies content-free source/code/field diagnostics: invalid_value,
invalid_evidence, confirmation_required or conflict_not_present. Confirmation and
conflict checks themselves are unchanged. Conversation retains proposal_rejection
(source, code, field, provider/model) in existing Message metadata and emits bounded
correlated qualification.proposal telemetry after committing binding. Rejected raw
values/evidence/transcripts are not copied into diagnostics/logs. Structured extraction
failures use invalid_proposal. A data-channel qualification_rejected notice explains
that words were saved without changing qualification; it is not a dependency error.

DependencyError status 404/409/422 maps to operation_not_found/operation_conflict/
operation_rejected across response, recovery and media lifecycle handling. Actual
unavailable dependencies/uncertain writes retain dependency_unavailable. The dashboard
uses dependency-return advice only for that code; controlled rejections have clearer
clarification/recovery instructions. Recovery still completes original durable work
without regenerating old speech. Existing definitive application-rejection FAILED
handling and legacy /turns behavior remain intact.

The real incident transcript was 'I want to go to USA and I need H1B visa.' Lead
validation returned 422 for its proposed facts, and input stayed RECORDED/PENDING.
The original rejected field/value was not retained, so earlier diagnosis could not
identify the exact model proposal. Step 1 preserves rejection classification for
future calls; it does not rewrite or recover that live historical turn.

Focused Step 1 gate: 90 tests passed across rejection/recovery, staged qualification,
conversational persistence, Lead evidence and Pipecat runtime. Includes unsupported
fields, extra score inputs, invalid values/evidence/conflict flags, all-or-nothing
mixed batches, next-turn admission, no Lead effects, no extraction on recovery, lost
binding replies, malformed extraction streams and genuine provider outages. Strict
caller 422 and immutable binding behavior remain tested. All 8 frontend Chrome tests
passed against a rebuilt production dashboard; an initial stale-build run failed its
new notice assertions and was corrected by rebuilding, without weakening assertions.
Ruff format/lint, strict mypy (132 sources), TypeScript/Prettier, production build
and whitespace pass. Full regression result is appended after completion below.

Step 2 inspection identified a country-write consistency conflict before its changes:
LeadUpdate/PATCH currently permits directly replacing or clearing target_country
using Lead.version, without qualification evidence/status. New caller-confirmed
country intake plus mirroring that existing field would introduce competing values
or stale confirmation if the existing PATCH writer remained independent. Changing
that completed PATCH behavior is a separate contract decision; no Step 2 fields,
scoring rules, schema or profile-write semantics have been changed yet.

Smallest proposed resolution to confirm: keep all country writes within Lead and
reuse current tables/optimistic concurrency. Voice-confirmed country updates mirror
the existing target_country field atomically. Existing profile-country changes
reconcile the current intake status rather than inheriting the old confirmation;
a changed confirmed destination records a conflict for explicit caller resolution,
and clearing the preference invalidates current intake confirmation while retaining
historical transcript/provenance. Visa type uses existing qualification-answer
storage with explicit evidence/confirmation semantics. Both remain non-scoring;
existing six-field completeness/classification/range must remain unchanged.
This requires agreement on the existing profile PATCH behavior before Step 2.

Source changes remain uncommitted and are not deployed automatically. Once ready,
end the live call and rebuild/recreate lead-service, conversation-service, api-gateway
and dashboard together: the typed Lead rejection response is required by the new
Conversation continuation policy. PROJECT_HANDOFF remains ignored/untracked.


Step 1 final regression gate: 637 passed, 1 deployment-only skip, 10 existing
third-party deprecation warnings (152.54s). Chrome dashboard/voice opt-ins and real
WebRTC, PostgreSQL/Redis, migration recovery/preservation/drift were included.
Process-outage event-stack and Compose hardening files were excluded to preserve
the running live stack. No paid-provider microphone or deployment rebuild gate
was performed. Step 1 is verified; Step 2 remains stopped pending the country PATCH
consistency decision above. Handoff is still ignored; no historical live turn was
modified, recovered or discarded.


### Approved current-truth qualification and immigration intake (2026-10-07)

This supersedes the rejected rigid country-conflict proposal above. Confirmed means
currently confirmed by the caller, not immutable. The user explicitly approved the
CALL boundary: reconnections retain the same call/protocol; a new call may revise
any of the eight mutable qualification/intake fields, even within the same business
conversation. Lead remains authoritative. No scoring weights, eligibility decisions,
workflow ownership, provider routing or overall staged architecture were redesigned.

Contract and persistence:
- QualificationUpdate and internal ValidateProposals add optional call_id. The LLM
  FactProposal/ProposedFacts/tool arguments cannot contain call IDs or statuses.
  Conversation supplies identity from its admitted durable Message.call_id, never
  from model output. Binding adds call-v1 provenance and the original call/turn to
  evidence. Application/recovery reuse that frozen identity.
- Existing qualification answer rows add call_id (current confirmed/conflicting
  state origin), pending_value and pending_call_id. Within the same call, differing
  confirmed information still follows the existing contradiction/explicit-resolution
  protocol. Same-value provisional repetitions do not downgrade confirmed state.
- On a later call, a provisional replacement is stored separately. Current confirmed
  value/status/score remain authoritative; Lead's question asks to confirm the pending
  value. Existing unresolved older-call conflicts also allow a normal later-call
  candidate/confirmation without requiring the old conflict flag. A later explicitly
  confirmed replacement atomically becomes current, clears pending/conflicting values,
  updates call provenance and recalculates the existing baseline score.
- The existing lead_score_history table adds answer_changes: before/after snapshots,
  including values/status/conflict/pending and call/conversation identity, with the
  originating turn. Snapshots are committed with the answer, score and normal outbox
  receipt, including non-scoring intake updates. Raw values are not added to event
  payloads, operational logs or rejection diagnostics. Existing transcripts/provenance
  remain subject to their existing retention policy.
- Additive migration f1a7c9e2b604 follows e8f2a6b3c901; no new tables. A one-time backfill
  reads committed Lead receipts and Conversation's admitted message identity to recover
  old call provenance where available. Seeded/redacted/unmatched historical rows retain
  unknown origin rather than fabricated IDs. Runtime service ownership remains separate.
  Pre-upgrade BOUND turns lack the call-v1 marker and retain the old update hash/semantics;
  old receipt hashes/events are never rewritten. New call-scoped receipts include call_id
  in their identity, preventing replay under another call.

Intake and PATCH semantics:
- target_country and visa_type are explicitly supported proposal fields with bounded
  1–80 character named values and verbatim caller evidence. US/USA/U.S./United States
  normalize to united states; UK aliases normalize to united kingdom. Other destinations
  retain the named text. H1B/H-1B normalize to h-1b (similarly F1/f-1); named immigration
  routes are intake preferences, not proof of eligibility or a visa taxonomy.
- The conservative evidence/confirmation protocol remains: unmatched, negated or uncertain
  assertions are rejected; normal assertions are provisional; explicit current caller
  'I confirm ...' statements are required for confirmation. They must actually contain the
  proposed destination/visa and an appropriate destination/visa/route assertion.
- Both intake fields remain outside FIELD_WEIGHTS, six-field completeness, classification
  and the 0–60 score. Existing intake candidates/conflicts receive backend confirmation
  questions, but absent optional intake fields do not expand the six-field questionnaire.
  The Lead-selected question remains the response fallback.
- Caller-confirmed country is mirrored atomically to existing Lead.target_country with
  its existing version check/outbox pattern. Lead PATCH stays an operator/profile metadata
  update: it has no caller evidence, does not create or mutate caller-confirmed answers,
  and cannot change their score. Profile metadata may differ after an operator edit;
  qualification answers remain authoritative for caller-confirmed information. Extraction
  and spoken-response instructions explicitly distinguish these sources.

Verification and activation:
- Focused tests cover all eight old→candidate→confirmed revisions in later calls within
  the same conversation, false/zero values, reconnection/same-call conflict resolution,
  old conflicts revised on new calls, history snapshots, frozen receipt recovery, duplicate
  application, lost acknowledgement, transaction rollback, PATCH non-confirmation and
  the original 'I want to go to USA and I need H1B visa' two-field provisional intake.
- Evidence tests cover aliases, invented/mismatched/uncertain/negative/oversized intake
  values and attempts to inject call identity. Migration checks include trusted historical
  call backfill, preserved receipt hash/value, reversibility and metadata/default drift.
- Source changes remain uncommitted. Live data, historical pending turns and running
  services have not been changed. PROJECT_HANDOFF.md remains ignored/untracked.
- To activate after ending live calls: build db-migrate, lead-service, conversation-service,
  api-gateway and dashboard; run `docker compose run --rm db-migrate`; recreate those four
  serving services and refresh the browser. Running new ORM code against the old schema
  will fail, so migration is required before serving the new code. Paid-provider microphone
  smoke testing remains a separate live verification.


Final current-truth/intake gate: **664 Python tests passed, 1 deployment-only skip**
(151.50s), including all 8 opt-in Chrome dashboard/voice cases, real WebRTC with
synthetic audio, PostgreSQL/Redis, and migration backfill/preservation/round-trip/drift.
Ruff lint and format (158 files), strict mypy (133 sources), single Alembic head and
Git whitespace checks pass. Ten existing third-party deprecation warnings remain.
An earlier push-to-talk browser timing timeout passed on isolated retry and in the
final full gate. The added migration fixture was corrected for SQL JSON construction
and the existing required preferred_language column; no schema defaults were weakened.
Live-stack process-outage/Compose-hardening files were excluded, and no paid-provider
microphone or deployment rebuild was performed. The isolated qualification test DB
was removed after verification. Existing .env.example and concurrently added .gitignore
changes were preserved. Handoff remains ignored/untracked; changes are uncommitted.
