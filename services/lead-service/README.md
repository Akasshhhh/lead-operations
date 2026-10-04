# Lead Service

The Lead Service owns synthetic leads, qualification profiles, qualification
answers, and lead-created/updated outbox records.

## Local run

Apply migrations first, then run:

```bash
PYTHONPATH='services/lead-service/src:packages/configuration:packages/database' \
DATABASE_URL='postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai' \
  .venv/bin/python -m lead_service.seed

PYTHONPATH='services/lead-service/src:packages/configuration:packages/database' \
DATABASE_URL='postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai' \
  .venv/bin/uvicorn lead_service.app:app --reload --port 8001
```

The seed command is deterministic and safe to run repeatedly. Existing leads
with the same synthetic profile key are skipped.

Lead mutations write the lead change and its versioned `platform.domain_events`
outbox record in the same PostgreSQL transaction. Redis publication is deferred
to the Event Bus module.

The application layer owns the transaction boundary: each request dependency
opens one transaction, the service stages changes, and the dependency commits
only after the handler succeeds. Direct service callers must use an explicit
`session.begin()` block.

When `LEAD_SERVICE_AUTH_TOKEN` is configured, all endpoints require the same
value in `X-Service-Token`. Production startup rejects an empty token.

## Endpoints

- `GET /health`
- `GET /v1/leads`
- `POST /v1/leads`
- `GET /v1/leads/{lead_id}`
- `PATCH /v1/leads/{lead_id}`
- `GET /v1/leads/{lead_id}/qualification`

When PostgreSQL is unavailable, `/health` returns HTTP `503` and lead mutations
fail without partially committing lead-owned state.
