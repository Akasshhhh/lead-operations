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
  .venv/bin/python -m pytest tests/integration/test_event_stack.py tests/integration/test_compose_conversation.py
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

## Module 6 verification

Use the same disposable PostgreSQL/Redis setup above with the current migration
head. The Module 6 integration tests use real PostgreSQL and an in-process Lead
Service HTTP boundary:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/module6_regression \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest \
  tests/unit/test_conversation_state.py \
  tests/unit/test_conversation_contracts.py \
  tests/integration/test_conversation_service.py
```

The historical Module 6 library/regression command passed **99 tests with one expected opt-in
Compose skip**. Its initial coverage includes guarded lifecycle transitions,
active-session uniqueness, score ownership, validated facts, contradiction and
resolution, idempotent retries, transcript counts, pending-turn recovery after
Lead outage, and existing Modules 1–5 regressions.

The opt-in deployment gate passed **6 tests** after rebuilding all images:

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
RUN_EVENT_STACK_TESTS=1 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai \
STACK_REDIS_URL=redis://localhost:56379/0 \
STACK_GATEWAY_URL=http://localhost:18000 \
  .venv/bin/python -m pytest \
  tests/integration/test_event_stack.py \
  tests/integration/test_compose_conversation.py
```

This verifies the public Gateway conversation routes, synchronous Lead scoring,
live-state reads, dependency outages/recovery, production auth guards, and
special-character database credentials. No dashboard or Pipecat runtime is part
of Module 6.

## Module 7 verification

The historical Module 7 run collected **105 tests: 104 passed and one expected
opt-in Compose skip**.
Coverage includes cursor ordering in both directions, speaker/search filters,
redaction visibility and search exclusion, terminal/cutoff guards, batch
idempotency, dry-run behavior, database failures, and Modules 1–6 live-turn
regression behavior. The retention CLI was exercised in dry-run, redaction, and
repeat-idempotency modes. The CLI is intentionally operator-run; no background
scheduler is introduced.

The rebuilt deployment gate passed **6 tests**, including public Gateway history
and transcript search. The deployed migration head is `e8f2a6b3c901` and
`alembic check` reports no drift.

The original 40-test plus one-outage-test record is superseded by this audit.

## Final Modules 1–7 reliability verification — 2026-10-04

The final run passed **143 tests with real PostgreSQL/Redis**, with one expected
opt-in Compose skip (144 collected):

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py
```

Create/migrate the dedicated test database using the setup above first. The
verification used disposable `modules_1_7_verification`; its data is not needed
to rerun these commands. Compared with the initial Module 7 gate, **39 additional
regression cases** now cover:

- retry input immutability, pending admission, late duplicate errors;
- Lead read/update failures, actual Lead commit followed by an injected lost
  reply, and injected Conversation rollback before COMMIT;
- simultaneous duplicate turns and concurrent active-conversation creation,
  including direct PostgreSQL uniqueness enforcement;
- every conversation failure source state and all six call statuses, reason and
  version guards, terminal protection, and durable structured failure events;
- malformed/mismatched authoritative responses and missing score;
- connection refusal, read timeout, and whole-request deadline with an existing
  persisted score: no stale/local fallback, history available, state unchanged;
- terminal pending-turn recovery, replacement/retention protection, replay after
  redaction, and historical Lead receipt compatibility;
- definitive validation rejection that permits corrected input under a new UUID.

After rebuilding all application images, **6 opt-in Compose tests passed** with
the deployment command above. The conversation case now stops the actual Lead
container after a successful score update, checks 503s for new/applied turns and
live-state, verifies durable APPLIED/PENDING history and unchanged state, restarts
Lead, and replays the turn twice with one additional score-history effect.

Ruff format/lint, strict mypy (**72 source files**, including Conversation),
`pip check`, Compose validation, migration upgrade/downgrade/rebuild/preservation,
and deployed Alembic drift checks passed. Head remains `e8f2a6b3c901`. Restored
Gateway health is 200; PostgreSQL/Redis and all application processes are running;
outbox unpublished/exhausted and dead letters are all zero.

The pre-fix failures, minimal corrections, and remaining module boundaries are
recorded in [`modules-1-7-verification.md`](modules-1-7-verification.md).
