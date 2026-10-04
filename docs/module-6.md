# Module 6 — implementation specification

Approved ownership: Conversation Service owns conversation/call state, turn
ingestion and live orchestration. Lead Service alone validates qualification,
calculates scores, and owns score/history persistence. Pipecat is planned in Module 12.
Module 7 expands these incremental transcripts with bounded history/search and
explicit retention redaction; Module 13 integrates AI tools and dynamic
qualification with the same Lead-owned scoring contract. Existing Lead APIs and
Module 5 envelopes remain compatible. Final verification evidence is in
[`modules-1-7-verification.md`](modules-1-7-verification.md).

## Exact baseline before implementation

`baseline-v1` measures confirmed profile coverage, not visa eligibility. Each
field has weight **10**:

| Field | Valid known values |
|---|---|
| education_level | none, high-school, diploma, bachelors, masters, doctorate |
| years_experience | integer 0–80 (not boolean) |
| english_level | beginner, intermediate, advanced |
| has_job_offer | boolean |
| budget_ready | boolean |
| urgency | low, medium, high |

Start at zero; sum weights only for confirmed, valid, noncontradictory fields.
Known false/zero values count as confirmed coverage. Null/`unknown`, missing,
provisional and contradictory values earn zero. Maximum baseline score: 60.
Classification: COLD 0–19, WARM 20–39, HOT 40–60. Classification alone never
authorizes eligibility or completion. `human_requested` and `follow_up_required`
have weight zero; orchestration uses them only when their persisted value is
boolean `true` and status is `CONFIRMED`.

New facts default to provisional; a caller explicitly supplies `CONFIRMED` to
confirm a value and is responsible for collecting confirmation evidence before
that assertion. Lead validates confirmed values for the six baseline fields.
There is no evidence/provenance proof field in the current contract; collection
and confirmation policy at the AI-tool boundary belong to Module 13. A different
value from an existing confirmed answer marks it CONTRADICTORY, retaining the
original and candidate value. A contradictory answer stays so until an update
with `resolve_conflict=true`; it earns points only if then confirmed and valid.
Initial API/seed answers stay provisional. Invalid confirmed baseline values
reject the Lead operation atomically. Score history retains contributing
fields/values/weights and rule version; qualification answers retain statuses and
conflicting values. Conversation Service has no scoring calculator. Before any
scoring update the qualification read may contain `score=null`.

## Guarded lifecycles

Conversation: CREATED → CONNECTING → GREETING → DISCOVERY ↔ QUALIFICATION →
SCORING → DECISION, with SCORING → QUALIFICATION for revalidation.
DECISION → QUALIFICATION/FOLLOW_UP/HUMAN_HANDOFF/COMPLETED;
FOLLOW_UP/HUMAN_HANDOFF → COMPLETED. Every nonterminal state can explicitly fail
with reason/context. COMPLETED and FAILED are final. Scoring/decision transitions
are backend operations, not arbitrary client-settable state.

Call transitions are exactly:

| Current | Allowed next statuses |
|---|---|
| CREATED | CONNECTING |
| CONNECTING | CONNECTED, FAILED |
| CONNECTED | RECONNECTING, ENDED, FAILED |
| RECONNECTING | CONNECTED, FAILED |
| ENDED, FAILED | none |

Connection transitions do not change conversation business state or terminate
media implicitly. Partial unique indexes enforce one active conversation per
lead and one active call per conversation. One active conversation per lead is
an intentional business invariant, preventing competing live qualification
flows; concurrent creation yields one success and controlled conflicts. A terminal
conversation may still have a connected call during a closing utterance;
explicitly end that call. Historical sessions remain stored.

Failures require a nonblank reason and matching version. Conversation reason,
submitted context, terminal time, and structured outbox event commit together.
Call reason, failure/end times, and structured outbox context (previous status,
runtime instance, reconnect attempts) commit together. Invalid/stale transitions
cannot overwrite terminal failures. Calls do not accept a separate caller-supplied
context field.

## Durable live flow and recovery

1. Submit a stable turn UUID, call ID, text, structured facts and expected
   conversation version. User text is never parsed for state commands.
2. Under a conversation row lock, allocate a sequence and commit the turn,
   Message, final TranscriptSegment, structured facts in message metadata, and
   outbox event. The event fingerprint covers call, text, and field-sorted facts;
   expected version guards first admission, not the identity of a replay.
3. Fetch the current authoritative qualification. If an unapplied turn has facts,
   synchronously POST the same UUID to Lead Service's qualification operation.
   Lead serializes updates and stores the request fingerprint in its durable
   idempotent outbox event atomically with answers, score, and history. Reusing
   the UUID with different input conflicts; a same-input retry returns the
   current durable qualification result, not a snapshot of the original reply.
   The profile lock precedes the receipt check, serializing concurrent retries.
4. Finalize the turn in a separate Conversation transaction. Lead's result drives
   clarification, next missing question, requested handoff/follow-up, or readiness
   to complete. Backend-generated SCORING/DECISION transitions create durable
   events. Conversation stores references/action, not a score copy. Call status
   remains independent.

There is no distributed transaction or database lock held across HTTP. A lost
response/crash leaves a PENDING turn; replaying its original request recovers it.
One pending turn per lead prevents later turns, including those in replacement
conversations, from overtaking an ambiguous operation. Terminal conversations
are never resurrected by late results; accepted turns can finish processing.
Clients retry pending turns after recovery; no background business scheduler is
introduced. Retention also waits for pending turns, including on terminal
conversations. Applied replay fetches current qualification without resubmitting
facts. Locked rows are refreshed during finalization, and a late duplicate error
cannot regress an applied/rejected turn to pending.

Lead transport errors, deadline expiry, invalid/mismatched qualification replies,
and a missing score after a mutation return 503. Pending input is preserved;
neither a previous score nor a locally calculated score is returned as a fallback.
Live-state reads also return 503. Conversation/history reads remain available and
the call does not implicitly disconnect or fail. Runtime/UI consumers must pause
qualification-dependent actions on this error and retry the original turn after
recovery.

A definitive Lead 422 validation rejection returns 422 and records `FAILED` on
the turn; corrected input must use a new turn UUID. Other ambiguous errors remain
pending. A finalization transaction failure leaves the recorded turn pending,
even if Lead already committed. Replaying completes orchestration without another
score/history/event effect.

Historical turn events without fingerprints retain their previous text/call
check (content hash after redaction). They cannot prove the original fact payload
if it was never stored. Historical Lead receipts retain legacy hash support and
still verify lead/conversation identity. Existing events are not rewritten.

Read APIs expose turns, sessions and conversation state. A live view fetches
qualification/score directly from Lead Service with its version/calculation time.
Polling these APIs and the existing durable event stream lets the future dashboard
observe calls live without adding a dashboard or media pipeline in Module 6.
