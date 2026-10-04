# Testing

Run formatting, lint, typing, and default tests with `make check`. Integration
tests explicitly skip when their dependency URLs are absent; skipped tests do
not count as an integration gate passing.

## Modules 1–5: real PostgreSQL and Redis

Use a test database on the local Compose PostgreSQL server. The event suite
creates and drops its own randomly named database using `TEST_DATABASE_URL` as
the administrative connection, applies migrations, and uses unique Redis stream
keys. It never flushes a Redis database. The PostgreSQL test user needs CREATEDB.
Earlier module tests use `TEST_DATABASE_URL` directly, so use a dedicated test DB
for a full regression run.

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 docker compose up -d --wait postgres redis
docker compose exec postgres createdb -U voice_ai voice_ai_test
DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test \
  .venv/bin/python -m alembic upgrade head
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest
```

The event tests cover envelope validation; stable bounded backoff; real Lead
Service outbox integration; independent groups; duplicate delivery; concurrent
relay/consumer operation; rollback; ACK loss; stale-owner protection; retries;
dead letters and replay; invalid versions; timeout/cancellation; terminated
PostgreSQL connections; Redis connection refusal; and replay after stream loss.
Retry tests move Redis pending idle timestamps through Redis APIs, avoiding slow
wall-clock waits while exercising actual visibility checks.

## Opt-in Compose deployment tests

`test_event_stack.py` stops **this project's** Redis/relay, PostgreSQL, and Lead
Service in separate scenarios, verifies controlled failures and recovery through
the Gateway, and checks actual image authentication/configuration guards.
It restores processes in cleanup. Start the local stack first:

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 docker compose up -d --build
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
RUN_EVENT_STACK_TESTS=1 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai \
STACK_REDIS_URL=redis://localhost:56379/0 \
STACK_GATEWAY_URL=http://localhost:18000 \
  .venv/bin/python -m pytest tests/integration/test_event_stack.py
```

## Modules 1–5 audit verification record

- 89 tests passed in the library/regression suite with real PostgreSQL and Redis,
  using `--ignore=tests/integration/test_event_stack.py` (no skips).
- All 5 separately enabled Compose deployment tests passed.
- Ruff format/lint, strict mypy (55 source files), Compose config, and `pip check` passed.
- Migration upgrade/downgrade/re-upgrade, row preservation, clean rebuild, and
  drift checking including server defaults passed on isolated databases.
- Gateway → Lead → PostgreSQL tests cover partial updates, conflicts, request-ID
  persistence, query bounds, and a real deferred-COMMIT failure with atomic rollback.
- Configuration tests include database-only seed CLI invocation and idempotency,
  malformed upstream responses, redacted errors, and transport deadlines.
- All application images rebuilt. Deployed seeding ran twice with 0 inserts and
  21 skips; restored health is 200 and outbox/dead-letter backlogs are zero.

Detailed findings and scope: [`modules-1-5-audit.md`](modules-1-5-audit.md).

The original 40-test plus one-outage-test record is superseded by this audit.
