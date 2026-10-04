# Implementation Plan

Implementation is strictly sequential. A module is complete only after its
implementation, unit tests, integration tests, failure tests, existing-system
integration checks, and documentation are complete.

## Module order

1. Repository and development infrastructure
2. PostgreSQL, migrations, and base domain models
3. Lead Service and synthetic data
4. API Gateway
5. Redis infrastructure and Redis Streams Event Bus
6. Conversation Service and state machine
7. Transcript persistence and conversation history
8. LLM provider interfaces
9. LLM Provider A
10. LLM Provider B
11. LLM Router and failover
12. STT/TTS provider interfaces
13. Voice Provider A
14. Voice Provider B
15. Voice Router and failover
16. Pipecat Voice Runtime
17. AI tools
18. Dynamic Qualification Engine
19. Deterministic Scoring Engine
20. Policy/Safety Engine
21. Follow-up Workflow
22. Human Handoff
23. Observability
24. Dashboard foundation
25. Live call interface
26. Provider health and failure simulation UI
27. Evaluation Framework
28. Evaluation Dashboard
29. End-to-End Integration
30. Failure testing
31. Concurrency/load testing
32. Final production hardening

## Module 1 status

Complete. Repository tooling, configuration validation, Compose infrastructure,
and development documentation are implemented and verified.

## Module 2 status

Complete. SQLAlchemy async persistence, Alembic migrations, the initial logical
schemas, core domain tables, session helpers, and PostgreSQL integration tests
are implemented and verified. The audit added migration `913be7264a01` to correct
24 timestamp defaults and enabled server-default drift detection.

## Module 3 status

Complete. The Lead Service provides validated lead APIs, qualification profile
persistence, deterministic idempotent synthetic seeding, optimistic compare-
and-swap updates, transactional lead outbox records, dependency failure
handling, and a verified container image.

## Module 4 status

Complete after the Modules 1–5 audit and expanded regression coverage:

- [x] Preserve omitted PATCH fields and explicit nullable values.
- [x] Map HTTP transport failures and whole-request deadlines to controlled 503s.
- [x] Reject missing, blank, and demo production tokens in both API services.
- [x] Handle malformed downstream responses with redacted 502 error envelopes.
- [x] Bound request IDs and persist them with lead outbox events.
- [x] Verify the real Gateway → Lead → PostgreSQL mutation/rollback path.
- [x] Verify rebuilt images during actual dependency outages and recovery.

The earlier passing suite missed these cases. The audit record is
[`modules-1-5-audit.md`](modules-1-5-audit.md).

## Module 1 scope

Module 1 contains only version conventions, root tooling, environment
validation, Compose infrastructure, and development documentation. It does not
create placeholder business services or provider integrations.

## Module 5 status and Definition of Done

Complete. Verified against real PostgreSQL/Redis and the running Compose stack.
The audit expanded coverage for slow publication failures, failed COMMIT logging,
malformed UTF-8, configuration, and process recovery. Current verification totals
are recorded in [`testing.md`](testing.md).

- [x] Version 1 event envelope and `EventBus` protocol
- [x] Redis Streams publish/subscribe, groups, pending recovery, and owned ACK
- [x] Independently deployable PostgreSQL outbox relay and polling index migration
- [x] Bounded retries/backoff, exhausted outbox inspection, dead letters/replay
- [x] Transactional processed-event deduplication and callback effects
- [x] Unit tests and real Redis/PostgreSQL integration tests
- [x] Crash-window, timeout, duplicate, Redis-loss, and database-failure tests
- [x] Existing-module regression suite
- [x] Compose Redis outage + relay restart test
- [x] Final documentation and deployment command review

Module 6 (Conversation Service/state machine) is not started.
