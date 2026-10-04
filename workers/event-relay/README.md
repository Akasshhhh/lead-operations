# Event relay — Module 5

This process publishes committed `platform.domain_events` records to Redis
Streams. PostgreSQL remains authoritative. The Lead Service writes only its
database transaction and does not wait for Redis.

## Start and inspect

From the repository root:

```bash
docker compose up -d --build
docker compose exec event-relay python -m event_relay inspect
docker compose logs -f event-relay
```

`inspect` returns unpublished/exhausted outbox counts, stream length, dead-letter
count, and Redis consumer-group lag/pending information. Stream length includes
acknowledged history; it is not the outstanding queue size. An empty group list
is normal until a domain consumer starts. Only the relay is deployed in this
module; the reusable consumer runner is exercised against real dependencies.

Configuration:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | Optional override | PostgreSQL asyncpg URL; otherwise use the `POSTGRES_*` fields below |
| `REDIS_URL` | Required | Redis URL |
| `EVENT_MAX_ATTEMPTS` | `5` | Maximum failed publication attempts before operator retry |
| `EVENT_POLL_SECONDS` | `0.5` | Interruptible idle/dependency-recovery polling interval |

When `DATABASE_URL` is absent, provide `POSTGRES_HOST`, `POSTGRES_PORT`,
`POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD`. The common resolver
escapes credentials. Compose passes the same configured identity to PostgreSQL,
the migration job, Lead Service, seed CLI, and relay.

The worker uses a two-connection PostgreSQL pool with five-second connection,
pool-acquisition, and command deadlines. Redis operations have two-second
connection/socket deadlines. SIGTERM finishes the current operation and exits;
Compose allows 15 seconds before forced shutdown. A forced crash can duplicate
publication; consumer deduplication handles it.

Local host invocation (export the two URLs first):

```bash
PYTHONPATH=packages/configuration:packages/database:packages/contracts:packages/event-bus:workers/event-relay/src \
  .venv/bin/python -m event_relay run
```

## Inspect and recover exhausted outbox events

```bash
docker compose exec postgres psql -U voice_ai -d voice_ai -c \
  "SELECT event_id, publish_attempts, last_error, next_attempt_at FROM platform.domain_events WHERE published_at IS NULL ORDER BY occurred_at;"
docker compose exec event-relay python -m event_relay retry-event EVENT_UUID
```

Replace `EVENT_UUID` with the retained PostgreSQL event ID. The command resets
publication bookkeeping, not payload or identity. It also supports replay of
published events after Redis data loss. Recovery of a range requires retrying
each retained event, preferably in aggregate-version order. There is no automatic
full-history replay. Replaying a historical event can deliver it after newer
events, so consumers must use aggregate versions appropriately.

## Dead letters

```bash
docker compose exec redis redis-cli XRANGE events.domain.dead-letter - + COUNT 10
docker compose exec event-relay python -m event_relay replay-dlq REDIS_ENTRY_ID
```

The ID here is the dead-letter stream entry ID, such as `1728000000000-0`, not
the event UUID. A repeated replay command for that DLQ entry returns the same
new stream ID. If the replayed event fails again, it creates a new DLQ entry.
Replay broadcasts to all groups; successful groups suppress it using their
durable processed-event markers. Invalid payloads must be diagnosed first;
replaying unchanged invalid input will fail again.

These are operator CLI commands requiring Docker/database access. Dead-letter
bodies may contain lead information; worker logs exclude payloads and secrets.

## Consumer integration contract

`Consumer.run_once()` receives one event and invokes an async handler with an
`EventEnvelope` and `AsyncSession`. The handler stages database changes using
that session; the runner commits both the effects and `(event_id, consumer_group)`
marker. Handlers must not commit, open independent write transactions, or perform
non-idempotent external effects. Use an outbox for those effects in later modules.

Each worker process must use a unique consumer name, including after restart
(for example, an instance UUID). A group represents a logical subscriber. Multiple
instances of the same subscriber share the group. Consumer defaults are five
attempts, a ten-second handler/transaction deadline, and a thirty-second Redis
visibility period. Keep visibility longer than handler and commit deadlines.
Cancellation is propagated and leaves the delivery pending.

Construct the Redis adapter with an async Redis client configured with
`decode_responses=True` and bounded connection/socket timeouts. The adapter
validates the decoding mode at construction.
The adapter overrides decoding for message bodies to preserve malformed bytes
for dead-letter inspection. Consumer group names are limited to 120 characters
by the durable deduplication schema; handler deadlines must be finite and positive.

Consumer handlers are supplied by their owning modules. Database marker writes
are shared platform infrastructure; domain updates remain owned by the handler's
service. There is no generic distributed-lock package: the relay uses PostgreSQL
row locks and consumers use the processed-event unique key, both actually needed
by this module.
