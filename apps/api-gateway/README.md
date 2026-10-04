# API Gateway

The API Gateway is the public HTTP boundary. It validates public requests,
adds correlation IDs, standardizes errors, and calls the Lead Service through
internal REST using `httpx`.

The gateway never accesses PostgreSQL directly.

The Lead Service is intentionally not exposed on a host port in Compose. Only
the Gateway is public. Internal requests include `X-Request-ID` and
`X-Service-Token`; missing or invalid service credentials receive `401`.

## Local configuration

```text
LEAD_SERVICE_URL=http://lead-service:8001
LEAD_SERVICE_AUTH_TOKEN=local-lead-service-token
LEAD_SERVICE_TIMEOUT_SECONDS=5
```

The token is sent only on the internal network as `X-Service-Token`. In
production, both the Gateway and Lead Service require a configured token.
Workload identity using mTLS or JWT can replace this adapter later.

## Public endpoints

The gateway exposes the Lead Service contract under `/v1/leads` and proxies the
service health under `/health`.
