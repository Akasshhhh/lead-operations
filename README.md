# Voice AI Platform

Fault-tolerant AI voice lead qualification platform for an immigration consultancy.

The project is being implemented incrementally. Each module must pass its unit,
integration, failure, and documentation gates before the next module begins.

## Current status

**Modules 1–5 — Foundation, persistence, Lead Service, Gateway, and events**

The current implementation establishes the Python/Node version conventions,
shared configuration validation, PostgreSQL persistence, deterministic leads,
the Lead Service, the public API Gateway, and an independent Redis Streams
outbox relay with retry, deduplication, and dead-letter support.

Modules 1–5 have undergone an expanded correctness and failure-path audit.
Findings, fixes, and verification evidence: [`docs/modules-1-5-audit.md`](docs/modules-1-5-audit.md).

## Prerequisites

- Python 3.12
- Node.js 22
- Docker Desktop with Docker Compose

The repository includes `.python-version` and `.nvmrc` for version managers.

## Local setup

To start the implemented services, migrations, and relay:

```bash
docker compose up -d --build
docker compose exec lead-service python -m lead_service.seed
docker compose exec event-relay python -m event_relay inspect
```

For host-side Python development:

```bash
cp .env.example .env
make install
make infra-up
DATABASE_URL='postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai' make db-upgrade
DATABASE_URL='postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai' make seed-leads
make check
```

Application code does not load `.env` implicitly. Compose reads it for variable
substitution; host commands use explicit environment variables and the import
paths provided by the Makefile. If `make` is unavailable, its `.venv/bin/python`
commands can be run directly with the same environment and `PYTHONPATH`.

PostgreSQL is available on `localhost:5432`, Redis on `localhost:6379`, and the
API Gateway on `localhost:8000`.

If either default port is already in use, override the host ports without
changing the container ports:

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 docker compose up -d
```

The same values should be used by host-side clients when connecting to the
overridden services.

Compose uses `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` consistently
for its database clients, including passwords containing reserved URL characters.
These variables initialize PostgreSQL only on an empty volume; changing them
does not rename existing roles or rotate passwords in an initialized database.

Stop local infrastructure with:

```bash
make infra-down
```

Local development and tests do not require external LLM, STT, or TTS credentials.
Deterministic provider adapters will be introduced before real provider adapters.

## Development commands

```bash
make install          # Create .venv and install development dependencies
make format           # Format Python files
make format-check     # Verify formatting without changing files
make lint             # Run Ruff lint checks
make typecheck        # Run mypy
make test             # Run pytest
make check            # Run all checks and validate Compose configuration
make infra-up         # Start PostgreSQL and Redis
make infra-down       # Stop local infrastructure
make db-upgrade      # Apply Alembic migrations; requires DATABASE_URL
make db-downgrade    # Roll back one migration; requires DATABASE_URL
make db-current      # Show the current migration; requires DATABASE_URL
make seed-leads      # Insert deterministic synthetic leads; requires DATABASE_URL
make events-inspect  # Show outbox backlog, exhausted rows, stream and group state
make test-events     # Event unit/integration tests (dependency URLs required)
```

Event recovery commands: `workers/event-relay/README.md`.
Real-dependency and Compose failure-test commands: `docs/testing.md`.

## Architecture

The approved architecture and incremental implementation plan are documented in:

- `docs/architecture.md`
- `docs/implementation-plan.md`
- `docs/decisions.md`
