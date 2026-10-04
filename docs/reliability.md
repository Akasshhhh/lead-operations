# Reliability — implemented boundaries through Module 5

## Event path

| Failure | Detection / deadline | Preserved state and recovery | Operator signal |
|---|---|---|---|
| Redis unavailable during publication | Connection/socket failure, 2s worker deadlines | Lead and outbox remain in PostgreSQL. Persist backoff, retry up to five attempts, then require operator retry. Lead API can still commit. | `outbox.publish_failed`, attempts/error, exhausted count |
| Relay crashes after Redis accepts event | Restart finds row still unpublished if DB commit did not finish | May publish twice. Consumers deduplicate by event UUID and group. No blind exactly-once claim. | Duplicate event IDs, relay start/stop logs |
| PostgreSQL unavailable to relay | 5s connection/pool/command deadlines | No trustworthy publication bookkeeping; process polls for recovery. Outbox rows are retained. Commit ambiguity may lead to redelivery. | `relay.dependency_failed` |
| Consumer worker crashes | Pending-entry idle exceeds 30s visibility plus retry delay | Another uniquely named worker claims the entry. No ACK until database commit. | Group pending count and delivery attempts |
| Consumer handler fails or times out | Handler/transaction deadline, default 10s | Database transaction rolls back including processed marker. Retry after visibility/backoff; then DLQ. | `consumer.failed`, error class, attempt |
| Consumer database connection drops | Database exception | Uncommitted effects and marker roll back. Delivery stays pending; retry is idempotent. | `consumer.failed`, pending count |
| ACK fails after consumer commit | Redis error | Database effect and marker survive; reclaimer skips handler and retries ACK. | Redis error, pending count |
| Malformed event or unsupported envelope | Pydantic validation before handler | Direct DLQ transfer; handler is never invoked. | Dead-letter count and `invalid_envelope` reason |
| Dead-letter append fails | Redis error | Original message remains pending; no ACK. | Pending entries remain; transport error |
| Old worker completes after lease was reclaimed | Atomic Redis ownership check | Old worker cannot ACK or dead-letter the current owner's delivery. PostgreSQL marker serializes concurrent duplicate effects. | Pending owner, marker already processed |
| Aggregate-head publication exhausts retries | Attempt count reaches configured limit | Later versions of that aggregate wait. Other aggregates continue. Operator repairs cause and retries the head event. | Unpublished/exhausted counts and oldest row |
| Redis loses stream/group data | Operational detection / stream inspection | Groups recreated from retained stream start. Lost entries need explicit replay from retained PostgreSQL records. Durable markers survive. | Stream length/group state changes; recovery runbook |

Consumer callbacks must use the supplied database transaction and must honor
cancellation. External side effects need their own idempotency contract/outbox;
the processed-event marker cannot make an independent HTTP action exactly once.
Worker polling waits are interruptible infrastructure loops, not follow-up timers.

## Failure isolation

The new relay is an independent Compose process. Redis is not added to the Lead
Service request path. A new unpublished-outbox index is the only Module 5 schema
change. Existing lead mutations, tables, and event payloads remain compatible.

No stream retention or deduplication-marker expiry is automatic in this stage.
Deleting markers makes historical replay capable of repeating database effects.
Replay is an operator action; the commands are documented in the relay README.

Provider, browser transport, conversation, and Pipecat failure boundaries will
be specified and tested when their modules are implemented.
