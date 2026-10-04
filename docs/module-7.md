# Module 7 — transcript history, search, and retention

Module 7 extends the Module 6 Conversation Service. It does not change live-turn
ownership, Lead Service qualification/scoring, call state, or Pipecat boundaries.

## Retrieval contract

The Gateway proxies:

```text
GET /v1/conversations/{conversation_id}/history
GET /v1/conversations/{conversation_id}/transcript
```

Both endpoints support:

- `limit`: 1–100, default 50;
- one cursor: `before_sequence` or `after_sequence`;
- optional `speaker`: `USER`, `AGENT`, `SYSTEM`, or `TOOL`;
- optional case-insensitive text search up to 200 characters.

The default page is the newest entries in ascending sequence order. A
`before_sequence` request walks backward through older entries; an
`after_sequence` request polls forward for newer entries. Responses include
`has_more` and the applicable next cursor. Search is conversation-scoped,
escapes SQL wildcard characters, and excludes redacted content.

`history` reads durable `Message` rows. `transcript` reads durable
`TranscriptSegment` rows. Existing Module 6 turn writes remain the source of
these rows; Module 7 adds no duplicate transcript store.

## Retention and redaction

Retention is an explicit operator command:

```bash
docker compose exec conversation-service \
  python -m conversation_service retention \
  --before 2027-01-01T00:00:00+00:00 \
  --reason retention-policy

docker compose exec conversation-service \
  python -m conversation_service retention \
  --before 2027-01-01T00:00:00+00:00 \
  --dry-run
```

The command uses `DATABASE_URL` when supplied, otherwise shared PostgreSQL
settings. It processes bounded batches and reports message/segment counts.

Only conversations in `COMPLETED` or `FAILED` state with `completed_at` at or
before the cutoff and **no pending turns** qualify. Active, incomplete, and
terminal-but-recovering conversations are never touched. Pending transcript and
fact metadata must survive ambiguous Lead/finalization failures; identical replay
can finish after the call ends without reopening the terminal conversation.
Redaction is idempotent and does not publish domain events. It replaces text with
`[REDACTED]`, clears JSON metadata, stores a SHA-256 hash of the original text,
and records the redaction timestamp/reason. IDs, speaker, sequence, timestamps,
call links, provider identifiers, and conversation state remain available for
audit and debugging. APIs expose the marker/hash, never the original content.

After recovery, retention may redact the content. Replay still validates the
fingerprint in the recorded-turn outbox event and does not restore redacted text
or metadata. Legacy turns without that fingerprint use the stored content hash.

The operation is intentionally not scheduled automatically. Retention policy,
cadence, and operational alerting remain deployment responsibilities. Final
recovery/retention verification: [`modules-1-7-verification.md`](modules-1-7-verification.md).
