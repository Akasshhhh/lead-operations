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

## Module 8 verification — 2026-10-04

The credential-free contract/mock runtime gate passed **38 tests**:

```bash
.venv/bin/python -m pytest tests/unit/test_llm_contracts.py tests/integration/test_llm_runtime.py
```

The full regression command above, using a newly created/migrated disposable
`module8_verification_*` PostgreSQL database and unique Redis test keys, passed
**181 tests with one expected opt-in Compose skip** (182 collected, event-stack
deployment tests excluded). The disposable database was removed afterward.
This includes Modules 1–7, actual database/Redis failures and recovery, and
upgrade/downgrade/rebuild/preservation/drift checks at `e8f2a6b3c901`.

New coverage includes context/JSON bounds, role/tool correlation, deterministic
streaming and replay after restart, concurrent generation, complete tool proposals
and result context, partial output followed by failure, undeclared/duplicate tools,
premature EOF/trailing output, inconsistent finish reasons, timeout before/during/
after output, cancellation/early-close resource release, vendor error redaction,
and nested JSON snapshot isolation.

Ruff format/lint, strict mypy (**79 files**), `pip check`, and Compose configuration
validation passed. This library-only module changes no deployed service or
migration; the six rebuilt Compose tests remain the historical Modules 1–7
deployment evidence and were not rerun for Module 8. Interfaces, limitations,
and deferred work are documented in [`module-8.md`](implementation-plan.md).

The project wheel built successfully with the declared setuptools backend in an
isolated temporary build environment. Importing `voice_platform_llm` directly
from that wheel and running credential-free mock generation also passed.

## Module 9 verification — 2026-10-04

The credential-free OpenAI/OpenRouter adapter, router, and configuration gate
passed **80 new tests**:

```bash
.venv/bin/python -m pytest tests/unit/test_llm_router.py tests/unit/test_llm_settings.py tests/integration/test_llm_providers.py
```

Together with Module 8, **118 LLM tests passed**. The full regression gate used a
fresh disposable PostgreSQL database and isolated Redis keys and passed
**261 tests with one expected opt-in Compose skip** (262 collected; event-stack
deployment tests excluded). The disposable database was removed afterward.
Database/Redis failure recovery and migration roundtrip/rebuild/preservation/drift
checks passed at unchanged head `e8f2a6b3c901`.

Adapter integration uses real HTTPX streaming with `MockTransport` and fragmented
vendor-format SSE, including context/tool translation, usage, safe status errors,
malformed/truncated output, timeouts, cancellation, and OpenAI-to-OpenRouter
fallback. Router coverage includes partial-output discard for buffered generation,
no replay after live output, bounded retry/deadline behavior, capability filtering,
circuit recovery, exclusive/cancelled probes, epoch races, and concurrent result
attribution. No paid vendor requests were made.

Ruff format (**106 files**)/lint, strict mypy (**87 source files**), `pip check`,
Compose configuration, and Git whitespace checks passed. Existing service,
migration, and business contracts were preserved. The six rebuilt Compose tests
remain historical Modules 1–7 deployment evidence. The optional Module 9 wheel
rebuild was declined and remains unverified; Module 8's wheel result above is
historical. See [`module-9.md`](implementation-plan.md) for contracts and deferred integration.

## Module 10 verification — 2026-10-04

The credential-free speech contract/mock runtime gate passed **75 tests**:

```bash
.venv/bin/python -m pytest tests/unit/test_speech_contracts.py tests/integration/test_speech_runtime.py
```

The full regression gate used a fresh migrated disposable PostgreSQL database
and isolated Redis keys: **336 passed, one expected opt-in Compose skip** (337
collected; event-stack deployment tests excluded). The disposable database was
removed afterward. Migration roundtrip/rebuild/preservation/drift and existing
database/Redis recovery checks passed at `e8f2a6b3c901`.

New coverage includes PCM formats/alignment, Unicode/bounds, deterministic scripts
and audio, silence without fabricated text, incremental audio pulls/backpressure,
interim replacement and final immutability, invalid/truncated/trailing completion,
sample accounting, total audio/text/event limits, concurrency/restart, deadlines
including input reads and consumer pauses, EOF timeout, safe vendor exceptions,
early close and cancellation/resource cleanup. The documented local mock example
also ran successfully. These are library integration tests, not vendor or browser
speech-quality tests.

Ruff format/lint, strict mypy (**94 source files**), `pip check`, Compose
configuration and Git whitespace checks passed. There is no new migration,
dependency, service or HTTP contract. No live speech API, browser audio, wheel
build or Compose deployment rebuild was run. Historical deployment evidence is
unchanged. Runtime ownership, provider preferences and deferred work are in
[`module-10.md`](module-10.md).

## Module 11 verification — 2026-10-05

The new credential-free adapter/router/configuration suites passed **88 tests**
(52 adapter cases, 21 router cases, 15 settings cases):

```bash
.venv/bin/python -m pytest tests/integration/test_speech_providers.py tests/unit/test_speech_router.py tests/unit/test_speech_settings.py
```

The adapter gate uses HTTPX `MockTransport` with fragmented PCM, deterministic
WebSocket wire fixtures, and a real local WebSocket server. The latter requires
local socket permission; it exercises the production connector without a paid
API key. With Module 10, **163 speech cases passed**. Recovery coverage includes
Sarvam-to-Rumik fallback with identical text/identity, no replay after audio or
transcript exposure, safe STT input prefix/EOF replay, interrupted-input rejection,
retry budgets, capability skips, circuits, exclusive probes, cancelled probes,
epoch races and concurrent requests. Adapter cases verify vendor fields, raw PCM
validation, controlled status/errors, timeouts, EOF, and request/task cleanup,
including safe input-close errors when no provider is selected.

The full gate used a fresh migrated disposable PostgreSQL database and isolated
Redis keys: **424 passed, one expected opt-in Compose skip** (425 collected;
event-stack deployment tests excluded). The disposable database was removed
afterward. Database/Redis recovery and migration roundtrip/rebuild/preservation/
drift passed at unchanged head `e8f2a6b3c901`.

Ruff format/lint, strict mypy (**101 source files**), `pip check`, Compose config
and Git whitespace checks passed. The documented explicit mock example ran.
`websockets>=15,<18` is declared directly and available in the verification
environment. No paid vendor smoke, browser/Pipecat, wheel build or Compose
deployment rebuild was run; the Modules 1–7 deployment evidence remains
historical. Limits and deferred runtime work: [`module-11.md`](module-11.md).
