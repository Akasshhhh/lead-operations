# Voice AI Platform

Fault-tolerant AI voice lead qualification platform for an immigration consultancy.

The project is being implemented incrementally. Each module must pass its unit,
integration, failure, and documentation gates before the next module begins.

## Current status

**Modules 1–15 — Backend, browser voice, live qualification, workflows, and central dashboard**

The central Next.js dashboard is implemented: synthetic lead selection/creation,
start/resume voice calls, live transcripts, authoritative qualification/scoring,
call history, handoff/follow-up controls, session-local provider health, and safe
conversation event metadata. It keeps durable history/call state readable during
Lead outages and shows qualification as unavailable rather than substituting a
cached score. Failure simulation and evaluation controls are reserved for Modules
16/17. Latest contracts, verification and limits:
[`docs/implementation-plan.md`](docs/implementation-plan.md).

Latest gates: **556 Python regressions passed** (one expected deployment skip),
**7 frontend browser tests**, and **6 rebuilt Compose deployment tests**. Paid
providers are optional; defaults remain fixture transcripts/tones rather than real
recognition/synthesis. The historical module results below describe earlier gates.

The current implementation establishes the Python/Node version conventions,
shared configuration validation, PostgreSQL persistence, deterministic leads,
the Lead Service, the public API Gateway, an independent Redis Streams outbox
relay with retry/deduplication/dead-letter support, and the Conversation Service
with live turn ingestion and durable call sessions.
Module 7 adds paginated history, transcript search, and explicit terminal-content
retention redaction.
Module 8 adds vendor-independent LLM contracts, a stateless streaming runtime/test
adapter, and a deterministic mock. Interface and local-use documentation:
[`docs/module-8.md`](docs/module-8.md). Its verification passed **181 tests** with
real PostgreSQL/Redis and one expected opt-in Compose skip, including 38 new LLM
contract, streaming/tool, timeout, cancellation, and failure tests.

Module 9 adds OpenAI and OpenRouter (Llama 3.3 by default), bounded retries,
capability-aware routing, circuit recovery, and safe handling of partial output.
Configuration, deterministic adapter tests, and limitations:
[`docs/module-9.md`](docs/module-9.md). Real credentials are optional; local mock
mode remains the default. Verification passed **261 regression tests** with real
PostgreSQL/Redis and one expected Compose skip, including 80 new provider/router
tests. Live paid API calls were not run.

Module 10 adds vendor-independent streaming STT/TTS contracts, validated speech
runtime adapters, cancellation/deadline handling, and deterministic text/PCM
fixtures. Local examples and provider preferences:
[`docs/module-10.md`](docs/module-10.md). Verification passed **75 new speech tests**
and **336 full regression tests**, with one expected Compose skip. Real speech
providers are added by Module 11 below.

Module 11 adds Sarvam realtime STT, Sarvam/Rumik PCM TTS, capability-aware
selection, bounded retries, circuit recovery and safe failover. Verification:
**88 new tests**, **424 full regression tests**, one expected Compose skip.
Mock mode remains the default; live vendor calls were not run. Configuration,
provider limits and deferred integration: [`docs/module-11.md`](docs/module-11.md).

Modules 1–5 have undergone an expanded correctness and failure-path audit.
Findings, fixes, and verification evidence: [`docs/modules-1-5-audit.md`](docs/modules-1-5-audit.md).
Module 6 ownership, state graphs, scoring baseline, live-turn recovery, and
verification are documented in [`docs/module-6.md`](docs/module-6.md).
Module 7 retrieval and retention behavior is documented in
[`docs/module-7.md`](docs/module-7.md).

Final Modules 1–7 verification passed **143 real-dependency regression tests** and
**6 rebuilt Compose deployment tests**. Lead outages preserve pending transcript
input and return 503 without local/stale scoring; identical retries recover
partial success without duplicate effects. Detailed findings and recovery:
[`docs/modules-1-7-verification.md`](docs/modules-1-7-verification.md).

## Remaining product roadmap

The approved reduced plan runs from **Modules 8–18**:

| Modules | Deliverable |
|---|---|
| 8–9 | LLM contracts, deterministic mocks, providers A/B, and failover router |
| 10–11 | Streaming STT/TTS contracts, voice providers A/B, and router |
| 12 | Pipecat browser/WebRTC voice runtime and interruption/call integration |
| 13 | AI tools, dynamic qualification, and live Lead-authoritative scoring |
| 14 | Lightweight safety, durable handoff, and follow-up |
| 15 | One central dashboard and live call interface |
| 16 | Useful observability and demo failure controls |
| 17 | Realistic evaluation, voice E2E, failure recovery, and basic concurrency |
| 18 | Final regression/hardening, configuration, documentation, and demo runbook |

Next is **Module 16 — Useful observability and failure simulation**, currently unstarted. Each module
begins with inspection and a conflict-stop gate and ends with relevant tests and
documentation. Exact scope/gates and deferred infrastructure are in
[`docs/implementation-plan.md`](docs/implementation-plan.md).

## Prerequisites

- Python 3.12
- Node.js 22
- Docker Desktop with Docker Compose

The repository includes `.python-version` and `.nvmrc` for version managers.

## Central dashboard

Run the full mock stack and open **http://localhost:3000**:

```bash
POSTGRES_PORT=55432 REDIS_PORT=56379 API_GATEWAY_PORT=18000 DASHBOARD_PORT=3000 \
VOICE_RUNTIME_ENABLED=1 LLM_MODE=mock SPEECH_MODE=mock \
  docker compose up -d --build --wait
docker compose exec lead-service python -m lead_service.seed
```

Select a synthetic lead, then **Start / resume call**. Push-to-talk is the default;
automatic voice activity detection can be selected before connecting. Durable
business conversations and audio connections have independent lifecycles.

For host frontend development with Node 22/npm 10:

```bash
npm ci
npm run dashboard:check
npm run dashboard:build
GATEWAY_URL=http://127.0.0.1:18000 npm run dev --workspace apps/dashboard -- --port 3001
```

`GATEWAY_URL` is server-only. Frontend requests use the same-origin Next proxy;
no service/provider key is required in the browser. For the verified host WebRTC
audio path, use the host Gateway launch in the Module 12 section of the plan and
point the frontend to `http://127.0.0.1:8000`. Container frontend/API integration is
verified; Docker runtime UDP/NAT audio reachability remains unverified.

## Local setup

To start the implemented services, migrations, Conversation Service, and relay:

```bash
docker compose up -d --build
docker compose exec lead-service python -m lead_service.seed
docker compose exec conversation-service python -c "import conversation_service.app"
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
The deterministic LLM mock, optional OpenAI/OpenRouter adapters, and Sarvam STT
plus Sarvam/Rumik TTS adapters are available. Real mode requires server-side keys
for enabled providers; `.env.example` documents the configuration.

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
