# Architecture

## Status

This is the approved reduced architecture and roadmap. Modules 1–5 provide
repository tooling, local PostgreSQL/Redis infrastructure, the initial database
schema, the Lead Service, the API Gateway, and a Redis Streams event transport
with an independent outbox relay. Module 6 adds the Conversation Service and
durable call-session/turn boundary; Module 7 adds history, search, and operator
retention. Their final reliability verification is recorded in
[`modules-1-7-verification.md`](modules-1-7-verification.md).

Modules 8–18 build one browser voice qualification product and central dashboard;
see [`implementation-plan.md`](implementation-plan.md). Provider integrations and
Pipecat are planned. Module 8 supplies the vendor-independent LLM contracts,
stateless runtime/local test adapter, and deterministic mock in
`packages/llm/voice_platform_llm`; see [`module-8.md`](module-8.md).
Module 9 adds OpenAI/OpenRouter HTTP adapters, capability selection, bounded
retries/deadlines, and runtime-local circuit recovery; see
[`module-9.md`](module-9.md). The package
owns no durable state and does not execute business tools or change the existing
live-turn path. Buffered generation may fail over after discarding an incomplete
attempt; live streams propagate failures after any exposed output without replay.

Module 10 adds `packages/speech/voice_platform_speech`: bounded mono PCM,
interim/final transcript and synthesis streams, closable STT/TTS protocols,
stateless deadline/validation adapters, and deterministic mocks. It owns no
transcript store, playback, VAD, or call state. Sample offsets are utterance-local;
future runtime integration uses the existing Conversation persistence boundary.
See [`module-10.md`](module-10.md) for speech contracts.

Module 11 adds request-scoped Sarvam realtime STT and Sarvam/Rumik HTTP PCM TTS
adapters. Separate STT/TTS routers implement capability selection, bounded
recovery and process-local health. Both real voice paths share Sarvam STT;
TTS failover selects Sarvam/Rumik only before exposed audio. STT retries retain
bounded request-local audio and stop after any transcript output or an unsafe
interrupted input read. No service/domain ownership changes. Module 12
(Pipecat/browser runtime) is next; see [`module-11.md`](module-11.md).

## Service boundaries

- **API Gateway:** public REST/WebSocket boundary and administrative controls.
- **Lead Service:** leads, qualification, and deterministic scoring.
- **Conversation Service:** calls, conversations, messages, incremental transcripts,
  live orchestration, state, and summaries.
- **Voice Runtime:** Pipecat real-time pipeline, provider adapters, and routers.
- **Dashboard:** one browser UI for leads, calls, qualification, scores, provider
  health, events, and demo failures, communicating through the Gateway.

Lightweight handoff/follow-up domain operations use existing service/worker
boundaries in Module 14. Evaluation is a scenario/test capability in Module 17.
Separate Workflow, Evaluation, and Analytics services are deferred. Provider
routers are embedded in the planned Voice Runtime.

PostgreSQL is the durable source of truth. Redis is used for streams, locks,
cache, provider health state, and short-lived runtime state. Services own their
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
future domain ownership is resolved before those operations are implemented.

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

Module 12 implements this path after the LLM and voice contracts/adapters/routers
in Modules 8–11. Module 13 integrates qualification tools and live Lead scoring;
Modules 14–18 complete policies, one dashboard, observability, E2E verification,
and hardening.

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
