# Event Contracts

## Current publication model

Module 3 writes lead events to `platform.domain_events` in the same transaction
as each mutation. Module 5's `event-relay` publishes committed rows to
`events.domain`. Each Redis entry has a `body` field containing the versioned
JSON envelope from `packages/contracts/voice_platform_contracts/events.py`.

```text
Lead transaction → PostgreSQL outbox → relay → Redis Stream
                                                ↓
                       consumer group → handler + processed marker → commit → ACK
```

Delivery is **at least once**. Publication, database commit, and Redis ACK are
separate operations, so duplicates are expected. PostgreSQL's
`(event_id, consumer_group)` primary key makes database effects idempotent within
the consumer transaction. Different groups process the same event independently.

## Envelope

Each event contains:

```text
event_id
event_type
event_version
producer
aggregate_type
aggregate_id
aggregate_version
correlation_id
causation_id
request_id
trace_id
idempotency_key
payload
occurred_at
```

The wire contract accepts version 1, timezone-aware timestamps, positive aggregate
versions, UUID identifiers, and JSON object payloads. Envelopes are capped at
64 KiB. Publication bookkeeping (`published_at`, `publish_attempts`,
`next_attempt_at`, `last_error`) stays in PostgreSQL and is not sent to consumers.
Existing lead payloads are unchanged. Unknown envelope versions and malformed
input are dead-lettered; each domain handler validates its specific payload.
Non-finite JSON numbers are rejected. Stream bodies are read as bytes so invalid
UTF-8 can be retained in dead letters without crashing the consumer's Redis decoder.

## Ordering, retries, and recovery

- Initial publication respects aggregate version: a lower unpublished event
  blocks higher versions for that aggregate, even during backoff or exhaustion.
  Independent aggregates can proceed through other relay instances using
  `FOR UPDATE SKIP LOCKED`.
- Parallel consumer execution and explicit replay do **not** guarantee ordered
  completion. Projections must compare versions; ordered workflows must enforce
  their sequencing in the owning domain module.
- Publication retries use persisted UTC deadlines and exponential backoff with
  stable jitter (base 1 second, cap 30 seconds, jitter 50–100% of that cap).
  Each deadline starts after the failed publication attempt finishes.
  Five failed attempts exhaust an event. Invalid envelopes exhaust immediately.
  Exhausted rows remain unpublished in PostgreSQL for operator intervention.
- Consumers use Redis pending entries and delivery counts. `XPENDING` and
  visibility-checked `XCLAIM` reclaim abandoned work; the required idle time is
  visibility plus exponential backoff. Crashed deliveries count toward attempts.
- Handler failures roll back both effects and deduplication markers. After five
  attempts, an ownership-checked Lua operation appends a dead letter and ACKs the
  original. If appending fails, the original stays pending.
- ACK failure after a successful database commit leaves a pending delivery.
  Redelivery checks the durable marker before invoking the handler, even when the
  attempt budget has been exceeded.
- `outbox.published` is logged only after the PostgreSQL publication mark commits.

## Retention and replay

Current streams are `events.domain` and `events.domain.dead-letter`. Groups are
created at `0-0` when actual consumers subscribe, allowing retained backlog to
be read. Additional stream categories will be added only when needed.

As approved for Module 5, there is **no automatic trimming**. This avoids deleting
pending work; stream size must be monitored. PostgreSQL keeps event history for
recovery. Redis AOF with the current default `appendfsync everysec` is not a
zero-data-loss guarantee. Following Redis data loss, operators explicitly replay
retained PostgreSQL events. Consumer database markers prevent repeated effects.
Malformed direct Redis entries without an outbox source are not recoverable from
PostgreSQL if Redis loses them.

See `workers/event-relay/README.md` for inspection, replay, and consumer contracts.

## Lead events

### `lead.created`

Produced after a lead, qualification profile, initial answers, and outbox row
are committed in one transaction.

Payload:

```json
{
  "lead_id": "uuid",
  "synthetic_profile_key": "canada-pr-high-intent"
}
```

### `lead.updated`

Produced after an optimistic lead update commits.

Payload:

```json
{
  "lead_id": "uuid",
  "changed_fields": ["status"]
}
```

The aggregate version is incremented for each update. Event IDs and
idempotency keys are deterministic for a lead and aggregate version.
