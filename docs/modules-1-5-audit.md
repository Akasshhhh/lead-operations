# Modules 1–5 audit — 2026-10-04

## Result

The three reopened Gateway defects and the additional defects found during this
audit are corrected. Verification passed: **89 library/API/database regression
tests**, **5 opt-in Compose deployment tests**, Ruff lint/format, strict mypy,
dependency checking, and migration upgrade/downgrade/drift checks.

At the time of this audit Module 6 was unstarted. Module 6 has since been
implemented separately; see [`module-6.md`](module-6.md) and the Module 6 status
section in `implementation-plan.md`. This record remains the audit of the
Modules 1–5 foundation.

## Findings and corrections

| Area | Finding | Correction and verification |
|---|---|---|
| Gateway PATCH | Serialization inserted nulls for omitted fields. | `exclude_unset=True`; omission and explicit nullable fields verified in unit tests, real Gateway → Lead → PostgreSQL tests, and the running stack. |
| Gateway transport | Connection timeouts and other HTTPX errors escaped handling; phase timeouts alone did not bound a request. | Handle `httpx.RequestError` and an overall deadline; controlled 503s tested for connect/read/write/pool timeouts, connection/read/protocol errors, and stalled requests. |
| Internal authentication | Compose's demo token bypassed production's missing-token guard. | Both services reject missing/blank/demo production credentials. Direct Gateway settings construction also validates them. Rebuilt production-mode images reject the demo token. |
| Gateway responses | Invalid JSON/schema/status responses escaped normal handling; errors could echo private values. | Redacted 502s for incompatible upstream responses; safe messages for 404/409/422/503; validation and HTTP-routing error envelopes. |
| Database timestamps | String `server_default="now()"` created fixed migration-time timestamp literals, masked by ORM defaults. | New migration `913be7264a01` corrects 24 defaults. Real raw SQL inserts and all defaults are checked; Alembic now compares server defaults. |
| HTTP transaction lifetime | Dependency teardown could commit after the success response. | Function-scoped sessions commit before transmission; FastAPI minimum 0.121. A real deferred constraint failure returns 503 and rolls back lead/profile/answers/outbox together. |
| Database outages | Availability handling covered health but not all lead endpoints. | Sanitized database errors and bounded pool/connect/command waits. All lead endpoints tested with connection refusal; actual PostgreSQL stop/restart tested through Gateway. |
| Correlation | Request IDs were forwarded but not bounded or stored in outbox rows. | Shared 120-character ASCII contract; replacement UUID for invalid input; create/update events retain normalized IDs. |
| Mutation/query values | JSON could contain values PostgreSQL cannot store; payloads and some query values were unbounded. | Reject NUL, invalid Unicode, non-finite numbers, oversized normalized mutation values, out-of-range versions/offsets, and overlong status filters. |
| Database configuration | Application DSNs hardcoded demo credentials; the seed fallback unnecessarily required Redis settings. | Shared database-only resolver for migrations, Lead, seed CLI, and relay. Compose passes consistent PostgreSQL fields; reserved characters and whitespace in passwords survive both rendered configuration and an actual container. |
| Runtime configuration | `httpx` was only a dev dependency; credentials appeared in settings reprs; invalid Gateway URLs reached request time. | Runtime dependency declared; credentials hidden in reprs; origins/timeouts/log levels validated; client/engine lifespan cleanup uses `finally`. |
| Publication backoff | Retry delay started before a slow failed publication. | Persist deadline after failure; delayed-failure regression verifies a full backoff remains. |
| Publication logging | Success was logged before COMMIT. | Log after commit; injected commit failure verifies no success log, retained unpublished row, and successful republishing. |
| Malformed stream bytes | Redis decoding could crash before malformed UTF-8 reached quarantine. | Read message bodies as bytes and preserve them in dead letters. Verify malformed entries leave the pending list only after quarantine and a later valid event processes. |
| Consumer configuration | Overlong groups exceeded the marker column; non-finite handler deadlines were accepted. | Validate group length and finite positive deadlines at construction. |

## Persistence compatibility

- Previous migration head: `6b30f517c820`; new head: `913be7264a01`.
- Only insertion defaults change; existing row timestamps are preserved.
- Downgrade restores literal-default behavior using the downgrade transaction's
  time. It cannot reconstruct the original migration-time literal.
- The migration test starts at the old head, inserts a row, upgrades, verifies
  preservation, downgrades/re-upgrades, and rebuilds from `base`.
- Drift checks include schemas, types, and server defaults.

## Verification evidence

### Library and application gate

```bash
TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/modules_audit_regression \
TEST_REDIS_URL=redis://localhost:56379/15 \
  .venv/bin/python -m pytest --ignore=tests/integration/test_event_stack.py
```

**89 passed, no skips.** The dedicated regression database was removed after
verification; recreate and migrate a test database using `testing.md` before
rerunning. Migration/event
tests create and clean up additional random databases; event tests use unique
Redis keys and never flush Redis. The seed CLI test uses the documented host
import paths and verifies initial insertion, deterministic identity, one outbox
event, and a duplicate-free rerun.

### Deployed-process gate

Five cases passed in `test_event_stack.py`:

1. Redis outage: Gateway mutations still commit; Redis/relay restart publishes
   the retained event.
2. PostgreSQL outage: Gateway health/read/update calls return controlled 503s;
   restart restores service without applying the failed update. Lead Service
   stop/restart also recovers, and a subsequent partial update publishes its event.
3. Gateway production startup rejects the demo token.
4. Lead Service production startup rejects the demo token.
5. A password containing spaces and reserved URL characters reaches a container
   and resolves to the exact configured password.

Tests restore processes in cleanup. After the seed resolver correction, Lead and
migration images were rebuilt again; the deployed default seed command was run
twice, both reporting `inserted=0 skipped=21`.

### Tooling and restored stack

- Ruff lint/format passed; strict mypy passed for 55 source files.
- Editable installation and `pip check` passed. Host launch commands explicitly
  set `PYTHONPATH`, as do images, rather than relying on the local interpreter to
  activate editable-package `.pth` hooks.
- Docker Compose configuration and application image builds passed.
- Deployed Alembic head is `913be7264a01`; deployed `alembic check` reports no drift.
- Public health: HTTP 200; Gateway/Lead/database `ok`.
- Outbox: 0 unpublished, 0 exhausted. Dead letters: 0. Stream history: 84 entries.
- PostgreSQL/Redis are healthy; Gateway, Lead, and relay are running.
- No consumer groups are expected yet: business consumers belong to later
  modules; the reusable consumer implementation is tested with real dependencies.

## Scope carried forward

Lead APIs implement create/list/read/update and qualification reads; no deletion
operation is advertised. Initial answers are provisional. Dynamic qualification,
scoring, conversation transitions, provider integrations, public user identity,
and broader production hardening retain their planned module ownership.

At-least-once delivery and explicit replay remain the delivery contract.
PostgreSQL effects are deduplicated when staged in the supplied transaction;
external effects need their own idempotency/outbox design. Mutation value limits
apply after JSON parsing; ingress HTTP limits are separate deployment work.

The original 40-test library suite and one outage test missed these cases. Future
completion gates should exercise real cross-service mutations, deferred commit
failures, and documented command entrypoints alongside isolated tests.
