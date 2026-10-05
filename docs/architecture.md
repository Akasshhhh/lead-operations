# Architecture

## Status

Modules 1–18 are implemented, including final hardening and the demo/runbook.
The current contracts, verification and limits are maintained in
[implementation-plan.md](implementation-plan.md). Historical provider-boundary
notes remain in module documents; current repository code takes precedence.

The product is one Next.js dashboard, one public Gateway with optional Pipecat
media runtime, Lead and Conversation domain services, PostgreSQL, Redis Streams
and an independent event relay. No new Workflow/Evaluation/Analytics service is
introduced. Evaluation/demo commands reuse existing test and persistence paths.

## Service boundaries

- **API Gateway:** public REST/WebSocket boundary and administrative controls.
- **Lead Service:** leads, qualification, and deterministic scoring.
- **Conversation Service:** calls, conversations, messages, incremental transcripts,
  live orchestration, state, and summaries.
- **Voice Runtime:** Pipecat real-time pipeline, provider adapters, and routers.
- **Dashboard:** one browser UI for leads, calls, qualification, scores, provider
  health, events, and demo failures, communicating through the Gateway.

Lightweight handoff/follow-up domain operations use the existing Conversation Service
boundary from Module 14. Evaluation is a scenario/test capability in Module 17.
Separate Workflow, Evaluation, and Analytics services are deferred. Provider
routers are embedded in the Gateway-hosted Voice Runtime.

PostgreSQL is the durable source of truth. Redis is used for asynchronous event streams. Provider circuits, fault controls,
capabilities and media sessions are process-local, not Redis-backed. Services own their
tables and communicate through APIs and versioned events.

PostgreSQL currently uses the following logical schemas:

- `lead`
- `conversation`
- `workflow`
- `evaluation`
- `platform`

SQLAlchemy models define the schema and Alembic owns versioned migrations. All
21 foundational tables and their existing schemas remain. The `workflow` and
`evaluation` tables do not imply separate services in the reduced product scope;
Conversation owns implemented workflow actions; the evaluation schema remains a
foundation without a generalized live-call grading service.

The Lead Service commits its mutation and outbox record atomically. The separate
`workers/event-relay` process publishes committed events to Redis Streams. Redis
outages delay publication without blocking the Lead Service transaction.

```text
 Gateway → Lead Service → PostgreSQL (lead + qualification + outbox)
       ↘ Conversation Service → PostgreSQL (conversation + calls + turns)
               │
               └── synchronous qualification/scoring call → Lead Service
                                   ↓
                          Event Relay process
                                   ↓
                        Redis events.domain
                                   ↓
             Consumer groups supplied by future domain workers
```

The shared event package supplies the transport protocol, Redis adapter, retry
policy, relay logic, and consumer runner. PostgreSQL processed-event markers and
handler effects share a transaction; Redis acknowledgement follows commit.
The runnable relay and the reusable consumer runner are verified independently.
See `docs/events.md` and `docs/reliability.md` for precise delivery guarantees.

Lead mutation methods are transaction-neutral. The API adapter and seed command
own transaction boundaries, which keeps service operations composable and
ensures failed requests roll back lead, qualification, and outbox changes
together.

The Conversation Service owns conversation/call state and active-turn persistence.
It does not own qualification or scoring. A meaningful user turn is committed to
the conversation database, then the service synchronously calls the Lead Service
qualification boundary with validated structured facts. The Lead Service commits
qualification, score, score history, and its outbox event atomically and returns
the authoritative result. Conversation orchestration uses that result; the LLM
cannot directly set a lead score.

The existing `conversation.calls` table is exposed as call sessions. A partial
PostgreSQL uniqueness index permits at most one `CREATED`, `CONNECTING`,
`CONNECTED`, or `RECONNECTING` session per conversation while preserving ended
and failed sessions as history.

One active conversation per lead is an intentional business invariant: it avoids
competing live qualification flows for the same authoritative profile. The
PostgreSQL partial unique index enforces it under concurrent creation. A terminal
conversation permits a new one after any ambiguous pending turn is recovered;
service admission checks prevent replacement from overtaking that turn.

During Lead unavailability the accepted transcript remains durable, its turn
stays pending, and qualification-dependent orchestration pauses. Turn/live-state
requests return controlled 503s without a score fallback; conversation/history
reads still work and call-session state stays independent. Identical replay after
recovery applies each durable effect once. A definitive Lead validation rejection
marks the turn `FAILED` so corrected input can use a new turn ID.

The API Gateway is the public boundary on port 8000. It calls the Lead Service
over internal REST on the Compose network, propagates `X-Request-ID`, and sends
`X-Service-Token`. The Lead Service is not published as a host port.

## Real-time path

Module 12 implements media after Modules 8–11 provider boundaries. Module 13
stages durable user input, freezes confirmed tool facts, and applies them through
Lead. Module 14 persists workflow actions; Modules 15–17 implement dashboard,
session diagnostics/faults, and evaluation. Module 18 verifies and hardens these
boundaries without adding business behavior.

```text
Browser WebRTC
  -> Pipecat transport
  -> VAD / turn detection
  -> STT Router
  -> conversation context
  -> LLM Router
  -> tools and domain services
  -> policy validation
  -> TTS Router
  -> browser audio
```

Pipecat owns the media and streaming pipeline. Domain services own durable
business decisions and persistence.

## Reliability principles

- PostgreSQL transactional outbox for event publication.
- Redis Streams consumer groups with retries and dead-letter handling.
- Idempotent consumers and durable idempotency keys.
- Per-provider timeouts, bounded retries, circuit breakers, and failover.
- Incremental transcript persistence.
- Structured logs, metrics, traces, and correlation IDs.

## Runtime, diagnostics and deployment limits

Agent output uses the existing idempotent Conversation message/transcript path
before TTS. Qualification tools propose facts; Lead validates and scores them.
Workflow retries persist intent and preserve acknowledgement gaps for recovery.
Call termination does not implicitly terminate the business conversation.

Next.js proxies a bounded same-origin route set to Gateway. Provider secrets stay
server-side. Diagnostics expose bounded metadata and process-local observations,
not durable vendor truth or transcript content. Demo faults require explicit
local/test enablement plus a live session capability; they do not change domain
state. Voice runtime admission is bounded to one Gateway process; multi-process
routing/distributed media recovery is outside the approved scope.

Python containers/migrations run as UID 10001. Authenticated domain health probes
check database readiness; Gateway health checks Lead and dashboard checks page
serving. Compose dependency health gates apply on startup, not as an automatic
recovery controller. Relay inspection measures durable backlog and stream state.
Paid-provider microphone and Docker media reachability remain unverified.
