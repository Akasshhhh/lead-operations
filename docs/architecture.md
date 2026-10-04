# Architecture

## Status

This is the approved Phase 0 architecture. Modules 1–5 currently provide
repository tooling, local PostgreSQL/Redis infrastructure, the initial database
schema, the Lead Service, the API Gateway, and a Redis Streams event transport
with an independent outbox relay. Provider integrations are not yet implemented.

## Service boundaries

- **API Gateway:** public REST/WebSocket boundary and administrative controls.
- **Lead Service:** leads, qualification, and deterministic scoring.
- **Conversation Service:** calls, conversations, messages, transcripts, state,
  and summaries.
- **Voice Runtime:** Pipecat real-time pipeline, provider adapters, and routers.
- **Workflow Service:** follow-ups and human handoffs.
- **Evaluation Service:** scenario execution and evaluation results.
- **Analytics Service:** read models and operational aggregates.

PostgreSQL is the durable source of truth. Redis is used for streams, locks,
cache, provider health state, and short-lived runtime state. Services own their
tables and communicate through APIs and versioned events.

PostgreSQL currently uses the following logical schemas:

- `lead`
- `conversation`
- `workflow`
- `evaluation`
- `platform`

SQLAlchemy models define the schema and Alembic owns versioned migrations.

The Lead Service commits its mutation and outbox record atomically. The separate
`workers/event-relay` process publishes committed events to Redis Streams. Redis
outages delay publication without blocking the Lead Service transaction.

```text
Gateway → Lead Service → PostgreSQL (lead + outbox)
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

The API Gateway is the public boundary on port 8000. It calls the Lead Service
over internal REST on the Compose network, propagates `X-Request-ID`, and sends
`X-Service-Token`. The Lead Service is not published as a host port.

## Real-time path

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
