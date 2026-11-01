# R3 target/text runtime semantics review

Reviewed against amendment `target-runtime-fields-approved-20261031` and the
revision2 candidate source `74d19f86b7b5b9babc9921acd7c85a6de4fc152a`.
This is a migration-readiness classification only. No `packages/**` or
existing migration product code was changed.

## Exact source behavior

- `scripts/job_final_migration/mapping.py:57` `_literal()` wraps every mapped
  argument as `{from: "literal", value: ...}`.
- `mapping.py:257-263` maps message `text` and `targetSessionId(s)` into
  `session.send`/`session.send-many` literal arguments.
- `mapping.py:275-278` maps scheduled `assign`/`send_session` target and text,
  and `send_qq` text, into literal arguments.
- `inventory.py:249-281` records only a source-to-action field path in
  `field_map`; it does not record runtime owner, allowed state, concurrency or
  receipt phase.
- `inventory.py:1177-1203` rejects missing message targets/text before mapping.
- `inventory.py:1250-1379` extracts scheduled targets/text, rejects missing or
  invalid values, and only emits `field_map` when no reasons remain.

## Classification

| Source field | Current mapping | Required amendment classification | Readiness result |
| --- | --- | --- | --- |
| `targetSessionId` / `target_session_id` | canonical literal `sessionId` | Candidate runtime field before reservation/queue acceptance; frozen original delivery identity after queue/external acceptance, success, unknown or partial effect | **Ambiguous without old manager state/receipt. Preserve the source record; do not advertise a lossless Action mapping.** |
| `targetSessionIds` | canonical literal `sessionIds` after ordered de-duplication | Same pre-acceptance backlog versus post-acceptance identity split, per recipient | **Ambiguous. Ordered de-duplication is data normalization, not evidence of a valid retarget or acknowledgement.** |
| `text` | canonical literal `text` | Candidate mutable runtime field only while the old delivery manager permits edits before reservation/acceptance; frozen input once Run admission/effect identity exists | **Ambiguous without old reservation/receipt phase. Preserve source text and evidence; do not claim runtime edit parity.** |

For all three fields the required owner is the legacy delivery/scheduled-task
manager until a reservation/fence boundary, then the canonical delivery
manager for any explicitly declared runtime target/text policy. The migration
mapper has no proof of that owner or phase. The canonical Job Action literal
therefore freezes a value earlier than the amendment permits and cannot safely
stand in for the old manager's backlog/retarget behavior.

## Boundary and follow-up

The existing Event admission/backlog path is present in baseline `b288613e`; it
is not treated as missing by this review. The migration package does not need a
second backlog or dispatcher. It must retain the original target/text bytes and
their status/receipt evidence for records whose acceptance phase is unknown.
Known-no-attempt, pending-before-queue, accepted/success, and unknown/partial
effects must remain distinguishable by the owning manager; no runtime target
change may replay an accepted or unknown effect.

Current result: **not losslessly mappable with the present mapper**. The safe
candidate behavior is the existing fail-closed preservation path with precise
classification (`target_missing`, `pending_notice`, `uncertain_effect`, or
`unsupported_complex_instance` as applicable). A follow-up implementation
needs MA/user direction on the old scheduled-task owner/status matrix and a
receipt/reservation proof before adding any field metadata or conversion.

This review does not claim that Event backlog, target switching, text edits,
notification retry, or old scheduled-task lifecycle are migrated. It also does
not claim those features are absent from the product.
