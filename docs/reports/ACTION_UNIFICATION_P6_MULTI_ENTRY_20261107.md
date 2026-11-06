# P6 multi-entry schedule identity and provider cold-query candidate

Baseline: `3d106ef5a476ad1112e491d6a0a1d3f55be3c04f`.
Branch: `implement/action-unification-p6-20261106`; isolated worktree. This is
an incremental candidate and does not claim overall P6 completion.

## Multi-entry handoff

The JSON handoff now accepts up to the canonical scheduler rule bound when each
old entry has a unique non-empty `id`, known fields, and a coherent cursor.
Each entry is converted through the existing time provider into its own fixed
Creator rule and cursor. Entries with differing policy/grace semantics remain
blocked rather than being flattened. Disabled entries must not retain an active
cursor. The old task-level `nextFireAt` must equal the earliest enabled entry.

The canonical Creator keeps every original rule ID. Binding metadata records
`originalEntryIds` and the original entry cursor map. Projection returns a
schedule list for multi-entry records and derives task `nextFireAt` from the
enabled entry cursors. A canonical event’s `ruleId` is used in the dispatch key
(`taskId:entryId:timestamp`); if an old occurrence has no unique entry identity,
the handoff blocks. The same Job aggregate and Run/Attempt/effect ledger remain
the execution authority, so entry identity is not copied into a second counter
or scheduler.

Configuration updates that would rewrite a multi-entry schedule, enable state,
pause state, or policy are explicitly blocked pending a per-entry revision/CAS
adapter. Text/name/description and other non-cursor changes do not collapse the
entry list. This protects old entry identities while leaving a precise follow-up
for full multi-entry editing.

## Provider notification, QQ and cancellation evidence

The provider-specific return projection from the previous candidate remains in
place. Existing provider paths are exercised with owned isolated fixtures:

* `test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt` is a
  real owned OneBot/QQ flow. It proves an accepted-but-unknown response is
  queried after cold recovery, keeps one Attempt/effect, and never sends again:
  [qq-cold-existing.xml](D:/project/pan-worktrees/action-unification-p6-20261106/evidence/action-unification-p6/qq-cold-existing.xml).
* Four owned notification tests cover pending notice, same outbox enqueue once,
  null target cold retarget, concurrent CAS reservation, and accepted notice
  lost-response remaining unknown/no-resend:
  [notification-cold-selected.xml](D:/project/pan-worktrees/action-unification-p6-20261106/evidence/action-unification-p6/notification-cold-selected.xml).
* Canonical cancellation keeps an unknown effect occupied while a queued tail
  can cancel through the original operation/Run identity:
  [cancel-cold-selected.xml](D:/project/pan-worktrees/action-unification-p6-20261106/evidence/action-unification-p6/cancel-cold-selected.xml).

The earlier temporary QQ probe reached all owned HTTP business assertions but
had a fixture teardown error caused by closing a pooled client after its event
loop. It is not used as clean evidence. The stable existing QQ test above uses
the repository’s runner ordering and is the accepted transport evidence for
this slice; no global dependency or real account was touched.

## Verification

| Layer | Result | Evidence |
| --- | --- | --- |
| JSON receipts, scheduler, HTTP API and configuration | **76 passed**, 0 failure/error/skip | `multi-entry-final-combination.xml` |
| Multi-entry identity/cursor/dispatch key | **1 passed** inside the 76; direct focused run also passed | test `P6_CONFIG_07_multi_entry_identity_cursor_and_dispatch_key` |
| Notification cold/CAS/lost response | **4 passed** | `notification-cold-selected.xml` |
| QQ accepted unknown cold query/no resend | **1 passed** | `qq-cold-existing.xml` |
| Canonical cancellation/unknown capacity | **1 passed** | `cancel-cold-selected.xml` |
| Source/static | AST, final contracts and diff checks passed | command log |
| Build/Chromium/remote CI/Linux/macOS/real data/deployment | Not run | unverified |

## Remaining gaps

Pure legacy logs still cannot create a missing canonical Run/Attempt/effect.
Pending dispatch, pending notification, live/abandoned writer and active
resource without the complete durable receipt/fence remain unknown/preserve.
Per-entry multi-entry update/delete/lifecycle CAS is deliberately blocked until
its complete old field and cursor contract is wired. Provider-specific full
notification timing matrices, real CLI/QQ Worker consumption, production build,
browser, remote CI, cross-platform, real-data migration, deployment and manual
acceptance remain unverified.

No push, merge, deployment, protected-port operation or real data/profile
operation occurred. `main` and `practical` remain unchanged.
