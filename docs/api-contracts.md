# API Contracts

## Public API Gateway

Base URL in local development: `http://localhost:8000`.

The public Gateway exposes the lead endpoints below and calls the Lead Service
over the internal network. Browsers do not receive the internal service URL or
the service authentication token.

Handled application responses include `X-Request-ID`. Client IDs must contain
1–120 ASCII letters, digits, `.`, `_`, or `-`; otherwise a UUID is generated.
The Lead Service preserves this ID in mutation outbox rows. Downstream
unavailability is returned as `503` with
the standard error envelope:

```json
{
  "error": {
    "code": "lead_service_unavailable",
    "message": "lead service unavailable",
    "request_id": "uuid"
  },
  "request_id": "uuid"
}
```

Malformed JSON, incompatible response schemas, unexpected statuses, and upstream
internal/authentication errors return a redacted `502` envelope. Downstream
404/409/422/503 retain their status with public-safe messages. Request validation
errors return `422` without echoing submitted values. HTTP transport operations
have both HTTPX phase timeouts and a whole-request deadline (default 5 seconds).

## Lead Service contract

Compose-internal base URL: `http://lead-service:8001`; no host port is published.
Internal calls supply `X-Service-Token` when configured. Production startup
requires a printable ASCII token distinct from the local demo token.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Verify service and PostgreSQL connectivity |
| `GET` | `/v1/leads` | List leads with optional status, limit, and offset |
| `POST` | `/v1/leads` | Create a lead and its qualification profile |
| `GET` | `/v1/leads/{lead_id}` | Retrieve one lead |
| `PATCH` | `/v1/leads/{lead_id}` | Optimistically update lead fields |
| `GET` | `/v1/leads/{lead_id}/qualification` | Retrieve qualification profile and answers |
| `POST` | `/v1/leads/{lead_id}/qualification/updates` | Validate live facts and atomically update profile, authoritative score/history, and outbox |

### Mutation behavior

- `POST /v1/leads` rejects duplicate `synthetic_profile_key` values with `409`.
- `PATCH /v1/leads/{lead_id}` requires `expected_version`.
- A stale version returns `409` and does not mutate the lead.
- Missing UUIDs return `404`; malformed UUIDs return `422`.
- Omitted PATCH fields stay unchanged. Explicit `null` clears `intent` or
  `target_country`; required fields cannot be cleared. Empty mutations are rejected.
- Mutations reject NUL characters, non-finite numbers, invalid Unicode, and
  normalized payloads exceeding 64 KiB. This is a contract-value limit after JSON
  parsing, not an ingress HTTP body-size limit.
- `expected_version` is a strict integer from 1 through 2,147,483,646.
- Lead, profile, answers, and outbox commit before a successful response is sent.
  A deferred COMMIT failure rolls back the entire mutation.
- Database availability failures return `503` on all lead endpoints. The service
  bounds pool acquisition and connection establishment at 2 seconds and individual
  database commands at 3 seconds.
- A transport timeout can leave the caller uncertain whether a commit completed.
  Reconcile using the synthetic profile key or current lead version before retrying;
  the Gateway does not automatically retry mutations.

### Pagination

`GET /v1/leads` supports:

```text
status=<optional status, at most 32 characters, no NUL>
limit=1..100
offset=0..9223372036854775807
```

The response includes `items`, `total`, `limit`, and `offset`.

## Module 7 transcript history contract

The Gateway exposes read-only conversation history routes:

```text
GET /v1/conversations/{conversation_id}/history
GET /v1/conversations/{conversation_id}/transcript
```

Both support `limit=1..100`, one of `before_sequence` or `after_sequence`,
optional `speaker=USER|AGENT|SYSTEM|TOOL`, and an optional search term up to
200 characters. Search is case-insensitive and conversation-scoped. A response
returns entries in ascending sequence order, `has_more`, and the next applicable
cursor. Redacted entries remain visible as `[REDACTED]` with `redacted=true` and
their content hash; original content is never returned.

Retention is an operator CLI operation, not a public Gateway route:

```text
python -m conversation_service retention --before ISO_TIMESTAMP --reason REASON
python -m conversation_service retention --before ISO_TIMESTAMP --dry-run
```

It only redacts content from terminal conversations older than the cutoff with
no pending turns, and is safe to repeat. This preserves ambiguous-operation
recovery input, including when the call/conversation has already ended.

## Module 6 Conversation Service contract

The Gateway exposes Conversation Service routes while the service remains
internal to Compose. Conversation and call state changes require an explicit
expected version. Invalid transitions return `409`; terminal conversation and
call states cannot be changed. Failure transitions require a nonblank reason;
missing/blank reason returns 422. Conversation failures retain submitted context,
and call failure events retain runtime/reconnect context. Reasons/timestamps and
outbox events commit atomically with matching-version state changes.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/conversations` | Create a lead's active conversation |
| `GET` | `/v1/conversations/{conversation_id}` | Read durable conversation state |
| `POST` | `/v1/conversations/{conversation_id}/transitions` | Guarded business-state transition |
| `POST` | `/v1/conversations/{conversation_id}/calls` | Create an independent call session |
| `POST` | `/v1/conversations/{conversation_id}/calls/{call_id}/transitions` | Guarded call transition |
| `POST` | `/v1/conversations/{conversation_id}/turns` | Persist input and synchronously apply authoritative qualification |
| `GET` | `/v1/conversations/{conversation_id}/live-state` | Read conversation/call plus current Lead qualification/score |

Conversation states are:

```text
CREATED → CONNECTING → GREETING → DISCOVERY ↔ QUALIFICATION → SCORING → DECISION
```

`SCORING` may return to `QUALIFICATION` for revalidation. `DECISION` may return to
`QUALIFICATION`, enter `FOLLOW_UP`, enter
`HUMAN_HANDOFF`, or enter `COMPLETED`. `FOLLOW_UP` and `HUMAN_HANDOFF` may
complete. Any nonterminal conversation state may enter `FAILED` through the
failure operation. Conversation Service does not accept an arbitrary requested
state from an LLM.

Call sessions use an independent lifecycle:

```text
CREATED → CONNECTING → CONNECTED ↔ RECONNECTING
             │            │           │
             └→ FAILED    ├→ FAILED   └→ FAILED
                          └→ ENDED
```

Only one nonterminal conversation exists per lead and only one active call session
exists per conversation. The one-active-per-lead rule is intentional and enforced
by a PostgreSQL partial unique index; concurrent creation returns one 201 and
409s for competitors. Ended and failed sessions remain historical. A terminal
conversation allows a replacement when it has no pending turns. CREATED calls
cannot fail directly, and RECONNECTING calls cannot end directly; follow the
existing graph rather than inventing a lifecycle shortcut.

`POST /v1/conversations/{conversation_id}/turns` accepts a stable `turn_id`, a
connected `call_id`, user text, and optional validated structured qualification
facts. It persists the message and final transcript segment before synchronously
calling Lead Service. The response includes the authoritative qualification and
score (nullable before its first calculation) plus the backend-selected next
action. The LLM cannot set the score. `expected_version` guards initial admission.

### Turn failures and replay

- Retry ambiguous failure with the **same** `turn_id`, conversation/call IDs,
  user text, and facts. New turns fingerprint these inputs; changing them returns
  409. Fact order is immaterial. Stale expected version does not invalidate an
  otherwise identical replay.
- Lead transport/deadline errors, invalid qualification schema/lead identity, or
  missing score after a qualification mutation return controlled 503s. The
  message/transcript/facts remain durable and the turn remains PENDING. No score
  or qualification payload is included as a fallback.
- Live-state returns 503 during Lead outage; conversation/history/transcript reads
  remain available. Call connection and business state do not change implicitly.
- A pending turn blocks a different turn and a replacement conversation with 409.
  A definitive Lead 422 validation rejection records FAILED and returns 422;
  corrected input uses a new UUID. Other ambiguous errors remain retryable.
- Lead-committed/lost-reply and finalization-rollback recovery produce no duplicate
  transcript, score history, qualification event, or state advancement.
- Applied replay reads the current authoritative qualification without resubmitting
  facts; it need not reproduce the original HTTP response. Late duplicate failures
  cannot turn APPLIED/FAILED back into PENDING.
- Accepted pending turns may finalize after call/conversation termination without
  reopening terminal business state. Retention waits for recovery; replay after
  redaction never restores text/metadata.
- Historical turn events without fingerprints retain legacy text/call validation;
  historical Lead receipts retain their existing hash format and scope checks.

Internal Conversation Service errors use `detail`; the public Gateway wraps
them in its existing safe error envelope with `X-Request-ID`.
