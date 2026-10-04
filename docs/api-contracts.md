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
