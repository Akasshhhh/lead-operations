# Voice AI Platform

Fault-tolerant AI voice lead qualification platform for an immigration consultancy.

The project is being implemented incrementally. Each module must pass its unit,
integration, failure, and documentation gates before the next module begins.

## Current status

Modules 1–18 are implemented. Module 18 adds final hardening, verification, and
an operational/demo runbook. Final gates: 590 Python regressions, 12 isolated
Compose checks, 83 evaluation cases, the Chrome demo and 7 frontend browser
checks passed. Current contracts and evidence are in
[implementation-plan.md](docs/implementation-plan.md); the local ignored
`docs/PROJECT_HANDOFF.md` preserves engineering context.

The dashboard provides leads, voice calls, transcripts, live qualification and
score history, handoff/follow-up, safe event metadata, provider health, and opt-in
failure simulation. Evaluation is a repository command with a content-free JSON
report. Modules 1–7 establish persistence, service boundaries, event delivery,
conversation recovery and retention; 8–11 provide LLM/speech adapters and routers;
12–14 add Pipecat, qualification tools and workflows; 15–17 add the dashboard,
observability/fault controls and repeatable evaluation.

Lead owns confirmed qualification and authoritative baseline-v1 scoring.
Conversation owns durable turns/messages, calls and workflows. Gateway hosts the
Pipecat media runtime. PostgreSQL owns business truth; Redis Streams deliver
outbox events at least once. Provider circuits, media sessions and fault controls
are process-local. One active conversation per lead, one active call and one
pending turn per conversation remain intentional. Retry with the original
identity recovers ambiguous durable effects; exposed output is never replayed.

Default speech uses fixture `Hello` transcripts and tones, and the mock LLM
supplies no qualification facts. Scripted providers verify scoring/workflows in
the demo below. Host Chrome/WebRTC is verified; paid-provider human microphone
and Docker UDP/NAT audio still need a live smoke test. Public/operator production
authentication and workload identity remain deferred; this is a local demo stack.

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
  docker compose up -d --build --wait --wait-timeout 180
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
make format-check lint typecheck infra-validate
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

## Readiness and recovery

Python services, the relay, and migrations run as UID 10001; dashboard runs as
`node`. Compose waits for authenticated domain health before starting dependent
apps. Lead/Conversation probes verify PostgreSQL access, Gateway probes Lead
readiness, and dashboard probes page delivery. They do not prove providers,
Redis delivery, or media readiness. Unhealthy status does not automatically
restart a container. Use `docker compose ps`, service logs, and
`docker compose exec -T event-relay python -m event_relay inspect` together.

Use the same port/voice settings on later Compose commands, preferably saved in
`.env`. `docker compose down` preserves data. Do not remove needed volumes.
Named volumes/network are shared across project names unless overridden; the
isolated clean-start/failure recipe is in the Module 18 runbook in the plan.

## Provider modes

Keys are server-only; never use browser environment variables for credentials.
Supply a key for every enabled provider. `.env.example` documents models, voices,
deadlines, retries and circuits. No automatic real-to-mock fallback exists.

| Configuration | Required keys |
|---|---|
| `LLM_MODE=mock`, `SPEECH_MODE=mock` | None |
| `LLM_MODE=real`, `LLM_PROVIDERS=openai,openrouter` | `OPENAI_API_KEY`, `OPENROUTER_API_KEY` |
| `SPEECH_MODE=real`, `STT_PROVIDERS=sarvam`, `TTS_PROVIDERS=sarvam,rumik` | `SARVAM_API_KEY`, `RUMIK_API_KEY` |

A restricted list such as `TTS_PROVIDERS=sarvam` needs only that path's key.
OpenAI Realtime TTS is deferred pending compatibility with the separate LLM-to-TTS
boundary. Live vendor quality/acceptance remains unverified.
`DEMO_FAULTS_ENABLED=1` enables session-capability-protected controls only in
local/test environments; it is off by default and cannot modify business truth.

## Repeatable evaluation and Chrome demo

Use a **separate disposable database**: regression tests exercise migration
rebuilds, outages and retention. Do not point them at your demo database.

```bash
.venv/bin/python -m pip install -e '.[dev,voice]'
npm ci
npm run dashboard:build
docker compose exec -T postgres createdb -U voice_ai voice_ai_test
DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test make db-upgrade
export TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:55432/voice_ai_test
export TEST_REDIS_URL=redis://localhost:56379/15
make evaluate                       # deterministic core scenarios
make demo                           # Chrome voice/score/reconnect/handoff scenario
.venv/bin/python -m scripts.demo --report test-results/demo.json --screenshot test-results/demo.png
```

The demo launches a production Next.js build and host Gateway/domain fixtures
with real PostgreSQL and headless Chrome. It verifies audio, score, lost-offer
retry, reconnect, durable handoff, operator completion and reload, then cleans
its synthetic records. It does not target the Compose dashboard or paid APIs.
Missing prerequisites/skips/errors fail explicitly. JSON reports contain safe
statuses, timings and Git state, without transcripts, captures or exception text.
Concurrent writers publish complete documents atomically; the last finishing
writer wins. Use different report paths to retain separate runs.

Set `CHROME_EXECUTABLE` outside the default macOS Chrome location;
`DASHBOARD_NODE` can select Node 22. For the full evaluation preset:

```bash
export VOICE_TEST_WAV=/absolute/path/to/speech-fixture.wav
make evaluate EVALUATION_SUITE=all
```

The WAV must contain speech for automatic VAD; silence/tones do not satisfy the
check. Full-regression/deployment commands, recovery steps and current evidence
are in the [Module 18 runbook](docs/implementation-plan.md). Pytest without
backend/browser opt-ins can skip integration checks and is not the full gate.
Frontend browser checks: `npm run dashboard:test`.
