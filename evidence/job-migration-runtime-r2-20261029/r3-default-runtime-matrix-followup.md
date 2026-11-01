# R3 default runtime matrix follow-up

The MA-approved default matrix is an implementation contract, not a new user
decision. This follow-up corrects the earlier claim that the Binding enum
prevents reuse: existing `input` bindings already express pre-admission target
and text changes. The pending/reserved acceptance fence remains an owner task.

## Schema constraint

At baseline `b288613e8a1862ea82d16a27d27992b715d3e0ba`:

- `packages/jobs/schemas/final/common.schema.json` already accepts `input`
  bindings such as `{"from":"input","path":"delivery.sessionId"}` and
  `{"from":"input","path":"delivery.text"}`. No new Binding source is needed.
- `packages/jobs/schemas/final/job.schema.json` and `action.schema.json` also
  reject undeclared top-level fields.
- There is no product-schema field for runtime owner, allowed phase,
  runtimeRevision/CAS fence, per-target acceptance receipt, text revision, or
  `runtimeBeforeAcceptance`.

The concrete seam is `FinalService._admit`: it copies input into a frozen Run
plan. `admit_event` can read current Job arguments before admission, while
dispatch resolves from that frozen input. The existing input path therefore
does not alone prove manager-owned revision/reservation/per-target receipts
through the approved pre-acceptance phase. Annotation/API/receipt wiring belongs
to the product owners; it is not a request for another user decision.

## Result for the default matrix

The pre-fix mapper was unsafe for a clean scheduled record that contains
`targetSessionId(s)` and `text`: it writes those values as frozen Action
`literal` bindings before the approved Creator/Delivery Manager reservation
boundary. A source record with explicit pending/uncertain/undelivered reasons
already follows the existing fail-closed preservation path, but a clean
scheduled record has no source receipt proving that it is post-acceptance.

This candidate now guards that path in both inventory and Mapper. It classifies
affected message, broadcast and scheduled assign/send_session records as
`incompatible-preserved` with `runtime_binding_unavailable`; an ordinary process
can still migrate beside them through the existing explicit partial package.
The source hashes, IDs and raw bytes remain intact, with no allocated target
IDs for affected records. This is preservation pending implementation, not
functional parity or a disabled replacement for the source manager.

The product follow-up is already authorized:

1. retain the default target owner/phase matrix; require the Action release to
   explicitly declare `runtimeBeforeAcceptance` for mutable text and QQ fields;
2. emit owner, allowed phase, revision/CAS fence and per-target receipt data
   through that product contract;
3. preserve the original record with a precise reason when those bindings or
   acceptance facts are unavailable; and
4. keep Event backlog, accepted/success/unknown/partial identities and
   known-no-attempt distinct.

The proposed binding paths and owner/phase/receipt requirements are emitted as
migration review metadata, not executable canonical fields. The mapper guard
also rejects a stale or manually relabelled simple item before object allocation.
No `packages/**`, second ledger or dispatcher was added. Shell and undeclared QQ
runtime behavior are not inferred from a field name.

Event backlog is present in the b288 product path and is not reported as
missing. The open gap is the migration representation of its pre-acceptance
runtime target/text state, not backlog implementation.
