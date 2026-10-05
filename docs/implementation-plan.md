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
implemented and reverified; Modules 8–11 are implemented and verified. Modules
12–18 are planned. The next module is 12 — Pipecat browser/WebRTC voice runtime.

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

Live paid vendor access/quality/latency, browser audio, wheel builds and Compose
deployment rebuilds were not verified in this pass. Domain services, scoring,
state graphs, HTTP contracts and migration head are unchanged. Module 12 is next;
stop before Pipecat, playback/VAD, call integration or business tool execution.
