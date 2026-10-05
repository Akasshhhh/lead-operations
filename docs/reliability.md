# Reliability — implemented boundaries through Module 11

## Speech library boundary

Module 10 validates bounded utterance audio, transcript revisions/finals, output
ordering and sample counts. Missing/trailing completion or unresolved interims
fail with content-free errors. One total deadline includes audio/provider reads
and consumer pauses. Timeout, cancellation and early close release owned input
and output streams; terminal success is exposed only after EOF and closure.
Partial text/audio is not replayed. No persistence or call/scoring state changes
occur here. Module 11 adds real adapters and recovery below. Transport,
playback-buffer interruption and durable transcript mapping remain Module 12.
See [`module-10.md`](module-10.md).

## Speech provider recovery

Sarvam STT and Sarvam/Rumik TTS use explicit PCM capabilities and controlled
vendor errors. Retry/failover is bounded by both per-attempt and original request
deadlines and stops after any transcript/audio event is exposed. STT preserves
bounded request-local consumed audio before exposure; interrupted input reads
prevent unsafe replay. Invalid input does not count as a provider health failure.
HTTP responses and STT sender/receiver/socket resources close on failure,
cancellation and early close. Process-local circuits have exclusive recovery
probes, cancellation-safe admission and epoch guards against stale successes.
No health-table writes, periodic paid probes or automatic real-to-mock fallback
are added. Real STT currently has one vendor; its recovery retries Sarvam, while
TTS can switch vendors. Wire fixtures/local sockets verify behavior; paid vendor
access/latency remains unverified. See [`module-11.md`](module-11.md).

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

Provider, browser transport, and Pipecat failure boundaries will
be specified and tested when their modules are implemented.

## Module 6 live-turn path

The active-turn sequence is:

```text
validated turn → conversation message/transcript commit
              → synchronous Lead Service qualification/scoring update
              → authoritative qualification + score
              → guarded conversation orchestration decision
```

Turn IDs and Lead Service qualification idempotency keys make client/runtime
retries safe within their durable receipt lifetime. New recorded-turn event
fingerprints include call, text, and field-sorted facts. Lead receipts bind lead,
conversation, and facts; their check occurs after the qualification profile lock.
Same-input replay returns the current authoritative result, not an exact cached
copy of the first HTTP reply. Applied turns do not resubmit qualification facts.

| Failure / race | Durable result | Recovery / API behavior |
|---|---|---|
| Lead read or update unavailable / timed out | Recorded message, transcript, facts, and event survive; turn PENDING; conversation/call versions unchanged | 503, no local or stale score fallback; retry original UUID/call/text/facts after recovery |
| Lead committed but reply lost | Qualification, score/history, and Lead receipt survive; Conversation turn PENDING | Same key verifies the receipt without another Lead effect, then finalizes Conversation |
| Conversation finalization fails before COMMIT | Lead result survives; recorded turn remains PENDING, finalization effects roll back | Replay finishes state/action and applied event once |
| Two concurrent identical retries | Profile and Conversation/Message locks serialize writes; refreshed ORM rows observe committed state | Both can succeed; one qualification history/event and one finalization effect |
| Late duplicate request fails | APPLIED or definitively rejected FAILED turn is preserved | Cannot reset durable outcome to PENDING |
| New turn or replacement overtakes pending work | Pending turn is retained, including in a terminal conversation | Controlled 409; finish original operation first |
| Lead qualification reply is invalid or for another lead / mutation omits score | No unvalidated result is used for orchestration | Controlled 503 and pending recovery; never calculate or substitute score locally |
| Lead definitively rejects invalid confirmed facts | Lead mutation rolls back; transcript retained as FAILED turn | 422; corrected facts use a new turn UUID, without blocking the conversation forever |
| Late result after conversation/call terminates | Accepted pending input can apply, but terminal conversation state is retained | Original replay may complete after call ends; no resurrection |

Live-state reads require Lead and return 503 on its outage. Conversation,
history, and transcript reads remain available while PostgreSQL and Conversation
Service are available. Runtime/dashboard clients must pause qualification-dependent
actions and show unavailable status; call connection does not implicitly change.

The one-active-conversation-per-lead invariant is intentional and PostgreSQL-
enforced, including concurrent creation. New conversations are allowed after a
terminal predecessor has no pending work. Failure transitions require a reason,
valid graph edge, and matching version; structured reason/context and timestamps
commit with their outbox event. The call and conversation graphs are unchanged.

Legacy receipts remain compatible and are not rewritten. Older Conversation
events without a fact fingerprint retain text/call checks, using a content hash
after redaction; they cannot reconstruct an original unstored fact payload.
No background pending-turn scheduler or distributed transaction is added.

## Module 7 retention boundary

History reads are bounded by page size and sequence cursors; search is scoped to a
conversation and excludes redacted content. Retention is an operator-run,
batch-bounded operation. It locks only eligible terminal message/segment rows
from conversations without pending turns, so active/recovering input is not
redacted. Repeating a completed batch finds no already
redacted rows and is safe. A failed batch transaction rolls back its redaction
metadata/content changes; the operator can rerun the command.

The recorded-turn fingerprint remains available after content redaction;
identical replay cannot restore the original text/metadata or duplicate score
history. Verification: [`modules-1-7-verification.md`](modules-1-7-verification.md).

## Modules 8–9 LLM boundary

Generation requests carry explicit context and stable correlation UUIDs; providers
never execute backend tools or write business state. Router attempts share an
original deadline and are validated through the Module 8 runtime adapter.

| Failure | Preserved result and recovery |
|---|---|
| Provider fails before exposed output | Bounded retry for transient errors, then eligible fallback with unchanged context |
| Buffered generation fails after partial text/tools | Discard the incomplete attempt; expose only the fallback's complete response |
| Live stream fails after exposed output | Close and propagate the controlled failure; no automatic replay or fallback |
| Repeated provider failures | Open circuit; after cooldown admit one recovery probe; other requests skip it |
| Probe cancelled or caller closes stream early | Close provider resources, release probe slot, and preserve failure counters |
| Older in-flight result returns after a newer circuit opens | Epoch guard prevents stale results from changing the newer circuit |
| All providers fail or lack required capability | Explicit error; no automatic mock, score, or qualification fallback |

Health is process-local operational memory, not durable business truth. Live
vendor generation and vendor billing cancellation were not verified by fixture
tests. The existing Conversation/Lead idempotency and durable-before-processing
requirements remain. See [`module-9.md`](module-9.md) for exact limits.
