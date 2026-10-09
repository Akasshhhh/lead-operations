# Vox · Voice Lead Operations

**A browser voice agent that turns immigration intake conversations into durable, caller-confirmed lead data.**

Vox brings live calls, transcripts, qualification, score history, and consultant next steps into one operations dashboard. Its central engineering problem is keeping business state correct when speech providers fail, responses are lost, callers interrupt, or a call reconnects.

This is a local engineering demo with synthetic leads, optional real providers, and deterministic integration scenarios. The backend owns qualification and scoring; models propose facts and wording.

[Architecture](#architecture) · [Voice pipeline](#the-voice-pipeline) · [Reliability](#failure-is-part-of-the-design) · [Demo](#experience-the-product) · [Setup](#run-locally)

## From conversation to an actionable lead

Immigration intake combines changing preferences, incomplete information, and repeated conversations. A transcript alone does not establish which details the caller confirmed, which details changed, or whether a promised next step was actually recorded.

The dashboard connects those pieces:

- **Browser calls:** microphone input, automatic voice activity detection, optional push-to-talk, interruption, reconnect, and saved-operation recovery.
- **Durable context:** conversation history, call records, transcripts, and qualification answers remain available across calls and page reloads.
- **Live qualification:** confirmed answers drive the current score and the backend-selected next question; pending replacements remain distinguishable from authoritative values.
- **Operator follow-through:** recorded human-handoff requests and follow-up reminders, alongside provider status, event metadata, and session diagnostics.

The scope is intake and lead operations. Eligibility decisions belong to a consultant, and a recorded reminder does not place a phone call.

## Architecture

```mermaid
flowchart TB
    Browser["Browser · Next.js dashboard"]
    Proxy["Next.js server proxy"]

    subgraph Gateway["API Gateway · FastAPI"]
        API["Public REST / signaling"]
        Voice["Pipecat voice runtime<br/>STT · LLM · TTS routers"]
    end

    Conversation["Conversation Service<br/>Calls · staged turns · transcripts · workflows"]
    Lead["Lead Service<br/>Evidence · confirmation · qualification · scoring"]
    DB[("PostgreSQL<br/>Domain state · receipts · transactional outbox")]
    Relay["Event relay"]
    Redis[("Redis Streams<br/>At-least-once event delivery")]

    Browser -->|Same-origin HTTP| Proxy
    Proxy --> API
    Browser <-->|WebRTC audio / data channel| Voice
    API --> Conversation
    API --> Lead
    Voice -->|Internal HTTP| Conversation
    Conversation -->|Qualification / scoring API| Lead
    Conversation --> DB
    Lead --> DB
    DB -->|Committed outbox rows| Relay
    Relay --> Redis
```

**Two domain services, explicit ownership.** Lead owns qualification, confirmation, and the authoritative score. Conversation owns calls, transcripts, turn orchestration, and lightweight workflows. They share PostgreSQL infrastructure through separately owned schemas and communicate through APIs; the runtime does not write their tables directly.

**Media and business state have different lifetimes.** A call can reconnect or end while its business conversation persists. PostgreSQL enforces one active conversation per lead and one active call per conversation; pending-turn admission prevents later work from overtaking an unresolved operation.

**Events support asynchronous work without entering the live scoring path.** Domain mutations and outbox records commit together. The independent relay publishes to Redis Streams; the shared consumer runner implements transactional deduplication, retries, and dead letters. Consumer infrastructure is reusable; the Compose stack deploys the relay, not a fleet of downstream business workers.

See the [architecture](docs/architecture.md), [decisions](docs/decisions.md), and [event guarantees](docs/events.md) for the deeper contracts.

## The voice pipeline

1. **Capture and detect a turn.** The browser sends microphone audio over WebRTC to the Gateway-hosted Pipecat runtime. Silero VAD detects speech automatically; push-to-talk provides a manual alternative. The runtime accepts 16 kHz mono PCM input and emits 24 kHz mono PCM audio.
2. **Stream to STT.** Audio reaches the transcription provider while the caller speaks. Finalized text enters the business flow; there is no separate LLM transcript-cleanup pass.
3. **Commit input before extraction.** Conversation records the user turn and transcript with its original turn ID and trusted call identity. A downstream failure can leave recoverable input rather than erase the caller's words.
4. **Propose, validate, and freeze facts.** The LLM proposes supported fields with verbatim evidence. Lead validates values, evidence, and confirmation semantics. Conversation durably binds the resulting facts to the staged turn before application.
5. **Apply authoritative state.** Lead commits answers, the recalculated score, change history, and an outbox receipt atomically. Conversation uses that result to advance orchestration. Rejected qualification proposals can finish without facts while retaining structured rejection diagnostics.
6. **Choose and persist the reply.** Lead supplies the next question. A bounded model pass can add conversational wording or request permitted tools; straightforward accepted intake statements awaiting confirmation use the exact Lead question directly. Policy review checks ordinary output, and the Lead-selected question remains the fallback. Agent text is stored before TTS starts.
7. **Play audio with interruption boundaries.** TTS streams audio back through Pipecat and WebRTC. Interruption cancels media; shielded persistence preserves the outcome of an in-flight write. Recovery retries saved work without replaying old audio.

The two model tasks—fact extraction and optional spoken wording—are separate from domain validation. The current path waits for final transcription and durable qualification application before generating a response; it is not an end-to-end speech-to-speech model.

## Engineering decisions that matter

### Confirmation represents current truth

A statement is provisional until the caller explicitly confirms it under the evidence contract. Within one call, including reconnects, contradictions follow the existing correction protocol. A later call may revise any mutable intake field: the replacement stays pending until confirmed, then atomically becomes authoritative. Previous values remain auditable in score/change history.

Conversation supplies call provenance from admitted records. The LLM cannot fabricate a call ID, set answer status, or supply a score. Operator `Lead PATCH` updates profile metadata; it cannot manufacture caller-confirmed qualification.

### The score is deliberately explainable

`baseline-v1` awards **10 points for each valid, confirmed field**, up to **60**:

| Scoring fields                                                                             | Intake context, outside the score |
| ------------------------------------------------------------------------------------------ | --------------------------------- |
| Education, years of experience, English level, job-offer status, budget readiness, urgency | `target_country`, `visa_type`     |

Classification is **COLD** below 20, **WARM** from 20–39, and **HOT** from 40. A confirmed `false` or zero still contributes: this baseline measures confirmed information coverage, not eligibility, financial suitability, or immigration success. Contradictory and provisional scoring answers do not contribute.

The [scoring implementation](services/lead-service/src/lead_service/scoring.py) is small enough to inspect directly. Scores, reasons, and history are committed by Lead rather than calculated in the browser or inferred by a model.

### Provider recovery respects exposed output

| Layer | Implemented real adapters |
| ----- | ------------------------- |
| LLM   | OpenAI, OpenRouter        |
| STT   | Sarvam realtime           |
| TTS   | Sarvam, Rumik             |

Routers use configured provider order, capability checks, deadlines, bounded retries, and circuit breakers. Half-open recovery admits one probe; epoch guards prevent stale in-flight results from changing a newer circuit state.

Buffered LLM generation can discard an incomplete attempt before failover. Once a live stream exposes text or audio, the router does not transparently retry and repeat it. STT replay is bounded to safely retained input before transcript exposure. Real mode never silently falls back to mocks, and the current STT configuration has one real provider.

Routers run inside the media process to avoid an extra service hop. Their health and circuits are process-local observations, separate from durable business truth.

## Failure is part of the design

| Failure boundary                                         | Preserved behavior                                                                                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Invalid or unsupported LLM qualification proposal        | Preserve the transcript and rejection reason; continue without applying the rejected facts. Authoritative qualification remains unchanged.                      |
| Lead outage or unavailable write                         | Keep staged input pending. Pause qualification-dependent work and recover the original operation after the dependency returns. Do not substitute a stale score. |
| Commit succeeds but its response is lost                 | Replay the original identity and frozen payload. Durable receipts prevent duplicate qualification, score-history, message, and workflow effects.                |
| Conflicting retry payload or stale admission             | Reject the changed operation rather than reinterpret it as a fresh write.                                                                                       |
| Optional spoken draft is invalid, too long, or times out | Use the Lead-selected question after the user turn is applied; uncertain business writes still require exact recovery.                                          |
| Redis is unavailable                                     | Domain transactions still commit their outbox records. Publication retries later; exhausted events remain inspectable and explicitly replayable.                |
| Consumer commits but ACK is lost                         | Redelivery checks the durable processed-event marker. Handler effects and that marker share a PostgreSQL transaction.                                           |

These are local transaction and replay guarantees across service boundaries. Event delivery is **at least once**; duplicates are expected. See [reliability](docs/reliability.md) and the [relay recovery guide](workers/event-relay/README.md).

### Observe and reproduce failures

Structured operation logs carry request, conversation, call, and turn identifiers where applicable. Bounded diagnostics expose operation counts, error counts, timings, recent observations, provider attempts, and recovery outcomes without logging transcript bodies or credentials.

With `DEMO_FAULTS_ENABLED=1`, local/test sessions can inject provider unavailability or latency and a dependency timeout through capability-protected controls. Faults have bounded attempts and expiry; replaying a control ID does not replenish them. They exercise existing boundaries rather than editing business state.

Readiness probes check domain database access, Gateway Lead readiness, and dashboard page serving. Provider quality, media reachability, and event progress require their own checks. Diagnostics are process-local; this repository does not implement a distributed tracing backend or automatic container recovery controller.

## Experience the product

After [local setup](#run-locally), open the dashboard, select or create a synthetic lead, and choose **Start / resume call**. Automatic speech detection is the default; enable **Push-to-talk** before connecting for manual turns. Watch durable history, current qualification, and provider status; reconnect or reload to inspect what survives.

**Mock mode is a transport demo:** STT returns fixture text, TTS produces tones, and the default LLM does not extract qualification facts. Speaking different words will not produce real recognition or intake updates in this mode. Scripted integration scenarios exercise qualification and workflows; real conversation requires configured providers.

The repeatable Chrome demo drives a production Next.js build through a host Gateway, domain APIs, and real PostgreSQL using scripted providers. It checks audio playback, live score updates, a lost signaling response, reconnect, durable handoff, operator completion, and reload:

```bash
make demo
```

This starts an isolated test harness, not a call against the running Compose dashboard. It requires the development/browser prerequisites and a disposable test database described below. Its JSON result is written to `test-results/demo.json`.

## Verification

The tests target the difficult boundaries, including concurrent retries, rejected proposals, later-call corrections, rollback, partial success, lost acknowledgements, provider stream truncation, circuit races, and independent simultaneous calls.

| Verification layer              | What it establishes                                                                                                                                          |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Python contracts and unit tests | Value/evidence rules, state transitions, stream validation, router policy, and configuration guards                                                          |
| PostgreSQL / Redis integration  | Real transactions, uniqueness, receipts, outbox delivery, replay, retention, and migration compatibility                                                     |
| Next.js Playwright tests        | UI behavior, proxy restrictions, recoverable errors, and authoritative data rendering with mocked routes                                                     |
| Chrome / WebRTC integration     | Production dashboard, real media transport, domain persistence, automatic/manual turns, reconnect, and two-call isolation with synthetic audio and providers |
| Opt-in Compose checks           | Deployed service identities, authentication/readiness, process outages, and recovery                                                                         |

After installing development dependencies:

```bash
make check
npm run dashboard:check
npm run dashboard:build
npm run dashboard:test
make evaluate
```

`make check` runs Python formatting, lint, strict typing, pytest, and Compose validation. Integration tests can skip without dependency URLs or browser opt-ins, so a default pytest pass is not a complete integration gate. Frontend browser tests require Chrome and the production dashboard build.

`make evaluate` runs the fixed `core` scenario preset. `make evaluate EVALUATION_SUITE=all` adds browser/media scenarios. Evaluation fails if any selected case skips or fails; reports include Git revision, statuses, and durations while excluding transcripts, audio, captured logs, and exception text. These are regression scenarios, not a speech-quality or performance benchmark.

Disposable-database instructions are in [testing](docs/testing.md); the [implementation plan](docs/implementation-plan.md) records dated verification and the isolated deployment runbook. Use that runbook for tests that stop services: Compose volumes and the network have explicit shared names.

## Run locally

### Credential-free Compose stack

Install **Docker with Compose v2**. Host development additionally uses **Python 3.12**, **Node.js 22**, **npm 10**, and `make`; runtime pins are in [.python-version](.python-version), [.nvmrc](.nvmrc), and the manifests.

From a fresh checkout:

```bash
cp .env.example .env
```

Keep an existing `.env` if you already have one. In `.env`, enable the optional voice runtime and retain mock modes:

```dotenv
APP_ENV=local
VOICE_RUNTIME_ENABLED=1
LLM_MODE=mock
SPEECH_MODE=mock
DEMO_FAULTS_ENABLED=0
```

Then build, migrate, start, and seed:

```bash
docker compose up -d --build --wait --wait-timeout 180
docker compose exec lead-service python -m lead_service.seed
```

Compose runs Alembic before starting the domain services. The seed command is idempotent. With the template ports, open **[localhost:3000](http://localhost:3000)**; Gateway is **[localhost:8000](http://localhost:8000/health)**, PostgreSQL is on `5432`, and Redis on `6379`. Lead and Conversation application ports remain internal to Compose.

If ports are occupied, set `DASHBOARD_PORT`, `API_GATEWAY_PORT`, `POSTGRES_PORT`, and `REDIS_PORT` in `.env`, and match those values in host commands. Compose reads `.env` for substitution; host Python commands do not load it implicitly. Browser-to-container WebRTC UDP/NAT reachability still requires a live smoke test; the verified synthetic browser/media harness runs its Gateway on the host.

Inspect and stop the stack with:

```bash
docker compose ps
docker compose logs -f api-gateway conversation-service lead-service event-relay
make events-inspect
docker compose down
```

`down` preserves the database and Redis volumes. Healthchecks gate startup; unhealthy status alone does not restart an application.

### Real providers

Set `LLM_MODE=real` and `SPEECH_MODE=real` in `.env`, choose enabled providers, and supply their keys **server-side**:

| Example selection                                    | Required keys                          |
| ---------------------------------------------------- | -------------------------------------- |
| `LLM_PROVIDERS=openai`                               | `OPENAI_API_KEY`                       |
| `LLM_PROVIDERS=openai,openrouter`                    | `OPENAI_API_KEY`, `OPENROUTER_API_KEY` |
| `STT_PROVIDERS=sarvam`, `TTS_PROVIDERS=sarvam`       | `SARVAM_API_KEY`                       |
| `STT_PROVIDERS=sarvam`, `TTS_PROVIDERS=sarvam,rumik` | `SARVAM_API_KEY`, `RUMIK_API_KEY`      |

Every enabled provider needs a key. [.env.example](.env.example) documents model names, voices, deadlines, retries, and circuit settings; set model names explicitly rather than relying on fallback defaults. Provider configuration belongs to the Gateway, so apply changes there:

```bash
docker compose up -d --build --force-recreate api-gateway
```

End active calls before recreating the runtime. The Next.js proxy keeps provider keys out of the browser; `GATEWAY_URL` is a server-only setting.

<details>
<summary><strong>Host development and scripted demo prerequisites</strong></summary>

Use Python 3.12 and Node 22, then install and build:

```bash
make install
.venv/bin/python -m pip install -e '.[dev,voice,browser-test]'
npm ci
npm run dashboard:build
```

For a host frontend against the default Compose Gateway:

```bash
GATEWAY_URL=http://127.0.0.1:8000 npm run dev --workspace apps/dashboard -- --port 3001
```

Create a **disposable test database**, separate from the demo database. These commands assume the template database credentials and host ports:

```bash
docker compose exec -T postgres createdb -U voice_ai voice_ai_test
DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai_test make db-upgrade
export TEST_DATABASE_URL=postgresql+asyncpg://voice_ai:voice_ai_dev_password@localhost:5432/voice_ai_test
export TEST_REDIS_URL=redis://localhost:6379/15
make evaluate
make demo
```

The browser harness defaults to macOS Chrome. Set `CHROME_EXECUTABLE` to your installed Chrome executable on other systems; `DASHBOARD_NODE` can select a Node 22 executable. The `all` evaluation preset also requires `VOICE_TEST_WAV` pointing to a WAV containing speech for VAD; tones or silence will not satisfy that scenario.

To retain an optional screenshot from the scripted demo:

```bash
.venv/bin/python -m scripts.demo --report test-results/demo.json --screenshot test-results/demo.png
```

Reports and screenshots are local test artifacts. The host voice launch and isolated deployment procedures are in the [implementation plan](docs/implementation-plan.md).

</details>

## Stack and source map

| Layer                  | Technologies                                                           |
| ---------------------- | ---------------------------------------------------------------------- |
| Backend and media      | Python 3.12, FastAPI, Pydantic, HTTPX, Pipecat                         |
| Browser                | Next.js, React, TypeScript, native WebRTC                              |
| Persistence            | PostgreSQL 16, SQLAlchemy async / asyncpg, Alembic, Redis 7.4 Streams  |
| Build and verification | Docker Compose, pytest / pytest-asyncio, Ruff, strict mypy, Playwright |

```text
apps/           API Gateway and Next.js operations dashboard
services/       Lead and Conversation domain services
packages/       Contracts, database, configuration, events, LLM, speech, voice runtime
workers/        Independent transactional-outbox relay
infrastructure/ PostgreSQL migrations and synthetic lead fixtures
scripts/        Deterministic evaluation and Chrome demo entry points
tests/          Unit, integration, failure, browser, and deployment verification
docs/           Architecture, contracts, decisions, reliability, and engineering record
```

[API contracts](docs/api-contracts.md) · [Data model](docs/domain-model.md) · [Implementation and runbook](docs/implementation-plan.md)

## Current boundaries

- **Live conversation quality remains under refinement.** Evidence matching and explicit confirmation use a conservative English intake contract. Natural corrections, conversational phrasing, and output screening have limits; scripted tests do not establish unrestricted language understanding or comprehensive guardrails.
- **No latency or scale claim.** Paid-provider microphone quality, end-to-end latency, Docker media reachability, and broad load testing are not established by synthetic scenarios. Two-call isolation is the current concurrency check, not a capacity benchmark.
- **One-process media ownership.** Active media sessions, capabilities, circuits, and diagnostic/fault state disappear on Gateway restart. Domain records remain durable; distributed media recovery and multi-worker routing are deferred.
- **Business integrations are bounded.** Handoffs are durable requests for operator review; follow-ups are dashboard reminders. PSTN telephony, external CRM/calendar integration, automatic outbound contact, generalized workflow/evaluation services, and OpenAI Realtime TTS are deferred.
- **Local deployment scope.** Internal shared-token authentication and voice-session capabilities exist; production public/operator identity and workload identity are deferred. Redis streams are not automatically trimmed, and transcript retention is an explicit operator operation.
