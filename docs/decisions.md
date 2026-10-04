# Architecture Decisions

## ADR-001: Pipecat for real-time voice orchestration

Pipecat owns audio frames, turn detection, streaming STT/LLM/TTS integration,
transport lifecycle, interruption, and cancellation. Business logic remains in
domain services so the voice runtime stays thin.

## ADR-002: Redis Streams initially

Redis is already required for short-lived runtime state, locks, health state,
and cache. Redis Streams provide consumer groups, replay within retention, and
simple local operations. An `EventBus` abstraction keeps Kafka as a future
option if throughput or replay requirements change.

## ADR-003: PostgreSQL as durable source of truth

PostgreSQL owns business state, transcripts, event outbox records, and
persisted evaluation results when used. Redis is never the authoritative store.

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
[`implementation-plan.md`](implementation-plan.md). Module 8 is unstarted.
