# Final Modules 1–7 reliability verification — 2026-10-04

## Result

The requested pre-Module-8 verification is complete. **143 real-dependency
regression tests passed** (one opt-in Compose skip, 144 collected), followed by
**6 passing rebuilt Compose deployment tests**. Ruff format/lint, strict mypy
(72 source files), dependency/config checks, migration recovery/preservation, and
deployed drift checks passed. The database head remains `e8f2a6b3c901`.

This pass makes targeted recovery fixes within existing metadata, outbox payloads,
and transactions. It introduces no models, migrations, service boundaries,
state-machine changes, or provider implementation. The approved reduced future
scope is Modules 8–18 in [`implementation-plan.md`](implementation-plan.md);
Module 8 is unstarted.

## Requested checks and evidence

| Check | Verified behavior | Evidence |
|---|---|---|
| Lead unavailable during live qualification/scoring | Accepted transcript/facts stay durable and PENDING. Qualification-dependent orchestration pauses. Turn/live-state return 503 without score fallback; history remains readable, existing Lead score is unchanged, and call connection is independent. | Real PostgreSQL/Lead-boundary read/update failures, connection refusal/read timeout/overall deadline, malformed/mismatched replies; deployed Lead container stop/restart after an existing score. |
| Partial success and idempotency | Original replay recovers after Lead commit/lost reply or Conversation rollback. Concurrent identical requests produce one qualification/history/event effect and one state advancement. Changed text/call/facts conflict. Late errors cannot reset APPLIED/FAILED. | Injected lost reply after an actual Lead COMMIT, injected rollback before Conversation COMMIT, synchronized concurrent turn requests, repeated replay and database counts. |
| One active conversation per lead | Intentional business invariant, enforced by PostgreSQL partial uniqueness under concurrency. Historical terminal conversations permit replacements once pending work is recovered. | Six concurrent creations yield one 201/five 409s; direct duplicate insertion violates `uq_conversation_one_active_per_lead`; terminal recovery/replacement tests. |
| Failure transitions and reasons | Existing graph/version guards remain. Nonblank reason required. Conversation reason/context/time and call reason/failure/end times commit with structured outbox context. Terminal records cannot be overwritten. | All nine nonterminal conversation states; all six call statuses, including forbidden CREATED/terminal failures; missing-reason/stale-version checks and persisted event inspection. |
| Coverage and minimal gaps | Earlier passing tests did not cover input identity, late errors, concurrent retry receipt/finalization, pending admission, structured call failure events, or terminal recovery/retention. Coverage now exercises those paths rather than relying on completion claims. | 39 additional regression cases since the initial Module 7 gate; the existing Compose conversation case extended with a real Lead outage and repeated recovery. |

## Concrete gaps and corrections

The first four new reproduction tests failed before fixes: changed retry facts
were accepted, a late duplicate error reset an applied turn, a new pending turn
raised an uncontrolled uniqueness error, and a call failure event omitted its
reason. A later reproduction also showed a definitive Lead 422 rejection stayed
PENDING and permanently blocked corrected input.

| Gap | Minimal correction |
|---|---|
| Replay compared only text | Store call/text/canonical-facts fingerprint in the existing recorded-turn event and original structured facts in message metadata. Reject changed input before Lead mutation. |
| Pending uniqueness was surfaced as a database error | Under the existing conversation lock, return controlled 409 before admitting a different pending turn; block replacement while a predecessor's turn is pending. Retain existing unique indexes. |
| Late errors / stale ORM snapshots | Refresh locked Conversation/Call/Message rows. Preserve APPLIED and definitively rejected FAILED outcomes when a duplicate request fails. |
| Lead receipt check raced a competing commit | Acquire/refresh the qualification profile lock before checking the receipt. Bind new fingerprint to lead/conversation/facts and retain scoped legacy-hash compatibility. |
| Applied replay could repeat Lead mutation | Fetch current authoritative qualification but skip resubmitting facts for APPLIED turns. |
| Invalid authoritative reply escaped validation | Convert incompatible JSON/schema, wrong lead identity, and missing post-mutation score to controlled unavailability. Never calculate or substitute score locally. |
| Call failure event lacked structured reason | Add reason, previous status, and runtime/reconnect context to its existing outbox payload; add submitted context/previous state to conversation failure payloads. |
| Retention could erase pending recovery facts | Exclude every terminal conversation with PENDING input until recovery completes; retain existing bounded/idempotent redaction implementation. |
| Definitive invalid facts blocked all future turns | Lead 422 records the existing FAILED turn status. The transcript remains auditable; corrected input uses a new UUID. Ambiguous failures stay PENDING. |
| Default typing omitted Conversation | Include its existing source path in Makefile and mypy defaults. |

Failure context is additive inside the existing version 1 event envelope.
Database models, state graph edges, endpoint shapes, score ownership, and Redis
Streams/outbox delivery semantics are preserved.

## Exact recovery contract

1. Initial turn admission requires the current conversation version and a
   connected call. The conversation transaction persists text/facts/transcript
   and the recorded event before calling Lead.
2. A read/update timeout or unavailable/incompatible reply returns 503. The
   original input is retained as PENDING; conversation/call state is not
   implicitly failed or advanced. Do not issue qualification-dependent actions
   using a locally synthesized score or the last score as a fallback.
3. Replay with the same conversation/turn/call IDs, text, and facts. Expected
   version guards first admission and can be stale on identical replay. New
   fingerprints are insensitive to fact order. Pending input blocks overtaking
   turns or replacement conversations with 409.
4. Lead serializes qualification writes and checks the durable receipt inside
   that lock. A previously committed update returns current authoritative data
   without another score-history/event effect. Conversation then applies its
   state/action and applied event in a separate transaction.
5. No distributed transaction or lock is held over HTTP. If finalization rolls
   back, retry the original request. If it already applied, replay does not
   resubmit facts or advance state again. A late duplicate failure cannot undo it.
6. A definitive Lead validation rejection returns 422 and records FAILED, allowing
   a corrected request under a new UUID. Other ambiguous errors remain pending.
7. An accepted pending operation can complete after the call ends or conversation
   becomes terminal. It does not reopen terminal state. Retention waits, then may
   redact content; original replay does not restore it.

Conversation/history/transcript reads depend on Conversation/PostgreSQL, while
live-state additionally depends on Lead. Future runtime/UI modules must represent
the latter's 503 as unavailable and pause dependent decisions. This pass verifies
durable call state and HTTP behavior; actual audio/UI behavior belongs to the
planned Pipecat/dashboard modules.

## Verification commands and restored state

Create and migrate a disposable database first as shown in [`testing.md`](testing.md).
The full regression command used:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/modules_1_7_verification \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py
```

**143 passed, 1 expected skip.** `test_conversation_service.py` has 45 real-DB
cases; `test_conversation_retention_cli.py` verifies the actual operator command.
The migration/event suites create isolated databases and unique Redis keys; they
do not flush shared Redis state. The disposable verification database was removed
after the gate; recreate/migrate one before rerunning.

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
  docker compose up -d --build --wait

POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 \
RUN_EVENT_STACK_TESTS=1 \
STACK_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai \
STACK_REDIS_URL=redis://localhost:56379/0 \
STACK_GATEWAY_URL=http://localhost:18000 \
  .venv/bin/python -m pytest \
  tests/integration/test_event_stack.py tests/integration/test_compose_conversation.py
```

**6 passed, no skips.** Includes Redis/relay, PostgreSQL and Lead process recovery,
existing production token/image and database credential tests, and the public
Gateway → Conversation → Lead outage/replay path. Processes are restored during
test cleanup; synthetic seed data and unrelated containers are preserved.

- Ruff format/lint and strict mypy: passed, including Conversation Service.
- `pip check`, Compose configuration, and `git diff --check`: passed.
- Migration upgrade/downgrade/re-upgrade, preserved rows, empty rebuild, and drift:
  passed in isolated tests.
- Deployed Alembic: head `e8f2a6b3c901`, no new upgrade operations detected.
- Gateway health: HTTP 200; PostgreSQL/Redis healthy; all four application/worker
  processes running.
- Outbox: 0 unpublished, 0 exhausted; dead letters: 0; retained stream: 87 entries;
  no business consumer groups yet.

## Remaining boundaries

- Same-input replay returns **current** authoritative qualification and state,
  not a historical response snapshot.
- Historical Conversation events without fingerprints retain their previous
  text/call/content-hash check; they cannot prove an original fact payload that
  was never stored. Historical Lead receipts keep their legacy fact hash and
  recorded lead/conversation scope checks; events are not rewritten.
- Current `CONFIRMED` status is an explicit caller assertion, with Lead validating
  the six baseline field values. The caller remains responsible for collecting
  confirmation evidence. Automatic evidence/provenance policy and
  tool-driven dynamic questions are Module 13 work.
- No background turn recovery, automatic retention, providers, Pipecat audio,
  dashboard, or new service platform is introduced by this verification.
