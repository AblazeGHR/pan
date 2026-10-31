# Runtime mapping implementation handoff

Status: stage guard candidate `ff255ccb3f1ddd036e129d111d5ac5377d251d4a`
received by MA. Migration completeness and original-function parity are not
accepted. Fourteen new guard cases passed; the 17 fresh original-assertion reds
remain open. Evidence is bound to the source hashes in
`evidence/job-migration-runtime-r2-20261029/R3_RUNTIME_BINDING_REPORT.md`.
This document changes no runtime schema, Binding, receipt or product behavior.

## Meaning of the temporary classification

The reason `runtime_binding_unavailable` means an approved manager contract has
not yet been wired to this mapper. The existing `incompatible-preserved`
classification is its current inventory container, not a D6 decision of
permanent incompatibility. It grants no deletion, permanent archival substitute,
source-owner takeover or manager retirement. Raw preservation cannot prove
backlog, retarget, mutable text or notification lifecycle equivalence.

Keep original source bytes, identities, manager, receipts, red assertions and
candidate history. In particular, successful partial import of an unrelated BG
record cannot retire or fence a preserved record's original manager. Event
admission backlog exists in b288; it is not a missing feature in this handoff.

## Field comparison for the product implementation

The following is a correspondence to the approved 447424c2/bff3e59c matrix,
not a definition of new product fields. The actual schema paths and version
will come from the owning implementation.

| Legacy surface | Approved owner and phase | Existing mapping shape to reuse | Evidence needed before executable migration |
| --- | --- | --- | --- |
| scheduled/message targetSessionId or target_session_id | Creator/Delivery Manager; published Event, pending/reserved before queue/external acceptance | Existing input binding; `delivery.sessionId` is a review proposal | Original Event/dispatch identity, runtimeRevision, reservation fence and selected-target receipt |
| broadcast targetSessionIds | Same manager and phase, independently per target | Existing input binding; `delivery.sessionIds` is a review proposal | Per-target acceptance, original order/identity, partial-success and unresolved-target behavior; de-duplication alone is insufficient |
| scheduled message/assign text | Same phase; Action release explicitly declares runtimeBeforeAcceptance | Existing input binding; `delivery.text` is a review proposal | Action declaration/pin, text revision and receipt effective stage; accepted text remains frozen |
| completion target and display payload | Notification Manager; terminal persisted, pending and provably not queued/sent | Existing notification/outbox boundary, owned by that implementation | Original outboxId/transitionSeq, target revision/CAS, not-sent or accepted receipt and payload digest |
| QQ bot/target/text | Channel Delivery Manager, only if Action release explicitly declares runtimeBeforeAcceptance | Consume the pinned Action contract; infer no capability from field names | Original effectKey/Attempt and provider phase/receipt; unknown is queried by original bot/effect |

Accepted/success/unknown/partial targets keep their original identity. A target
or text update cannot authorize blind resend. Known-no-attempt must remain
distinct from unknown; ack/not_applicable notifications do not automatically
revive. Frozen execution identity, Action version, inputs/authorization and
effects are outside runtime retarget updates.

## Resume input from the owning implementation

Resume actual mapping when MA supplies the precise consumable product SHA and
the following implementation evidence, rather than another model decision:

1. Exact schema/API/receipt paths and Action release declarations for the
   approved owner/phase/revision/fence contract. Reuse `input` and the existing
   Event/admission/delivery path; no second dispatcher or ledger.
2. The reservation/acceptance integration around `admit_event`, `_admit` and
   `_drive_run`, including what may change before acceptance and what remains
   frozen after acceptance. Current b288 input binding already works before
   admission, while `_admit` copies input to the frozen Run plan.
3. Focused proof of backlog recovery, explicit retarget and mutable text at the
   permitted phase; per-target accepted/unknown/partial rejection; CAS/race and
   original-key reconciliation; source and receipt retention. Notification and
   QQ evidence belongs to the respective managers, not to mapper assumptions.

Then, within MA-authorized integration/source scope, verify that exact product
contract, re-inventory the unchanged original hashes, connect only the matching
input mappings, and exercise the affected old lifecycle assertions. Preserve
the existing 17 red results as predecessors. A contract mismatch is an
implementation handoff issue unless it exposes a concrete conflict with the
approved old behavior; do not repeatedly ask the user to approve missing code.

Until those inputs arrive, keep the guard and source manager intact. Do not
repeat the unchanged protection matrix, invent unknown fields, or promote this
stage candidate to migration/retirement acceptance. Independent documentation
comparison can continue. No product change or test rerun accompanies this
documentation clarification.
