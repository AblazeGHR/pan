# P6 multi-entry per-entry CAS and lifecycle candidate

Baseline: `c1257f5a6f8f62f9871c71278e873df7a0d5f165`.
Branch: `implement/action-unification-p6-20261106`; isolated worktree. This
candidate remains partial P6 work.

## Per-entry CAS behavior

For a migrated multi-entry task, the binding now keeps `entryRevisions`,
`entryCursors`, `entryPaused` and the task-level revision. HTTP and MCP accept
additive `entryId`, `expectedEntryRevision`, `entryEnabled`, `entryPaused`,
`entryMisfirePolicy` and `deleteEntry` fields. The operation uses the existing
FinalService operation receipt and owner transaction:

* stale task or entry revisions reject before Creator/Job mutation;
* same requestKey replays the original operation without a second update;
* pause/disable clears the canonical entry cursor but retains the previous
  cursor for an explicit resume;
* resume restores only that retained cursor; missing cursor evidence remains
  blocked;
* deleting one entry removes only its rule/cursor and preserves the remaining
  order and task-level earliest `nextFireAt`; deleting the final entry is left to
  whole-task lifecycle delete;
* schedule changes retain the entry ID and recalculate only that entry’s
  canonical rule/cursor; existing Job Run/Attempt/effect plans are untouched;
* whole-task pause/resume/delete continues through the existing Creator/Job
  lifecycle operation and its owner/state fences.

Dispatch still uses the original entry `ruleId` in the frozen dispatch key.
Cold projection reads the canonical cursor set and returns the remaining
legacy entry list. No second entry counter or scheduler was introduced.

The canonical TimeProvider currently stores `misfirePolicy` and grace at the
Creator configuration level. A request to change only one entry to a policy
different from the shared policy is therefore explicitly blocked rather than
silently changing every other entry. This is a concrete schema/model gap for a
future per-rule policy contract.

## Provider timing, cancellation and cold evidence

The previous stable owned evidence remains valid and was not replaced:

* 4 notification pending/CAS/cold/lost-response tests passed in
  `notification-cold-selected.xml`.
* 2 parameterized owned OneBot/QQ accepted-unknown cold-query/no-resend tests
  passed in `qq-cold-existing.xml`.
* 1 canonical cancellation/unknown-capacity test passed in
  `cancel-cold-selected.xml`.

These exercise existing provider/outbox/Run identity and receipt paths. A
temporary additional QQ probe was not used as clean evidence because its
fixture teardown closed a pooled client after the wrong event loop; the stable
repository runner above is the accepted clean transport evidence. No real QQ
account or Worker was used.

## Verification

| Layer | Result | Evidence |
| --- | --- | --- |
| JSON receipts, scheduler, config, HTTP API and multi-entry | **76 passed**, 0 failure/error/skip | `per-entry-final-combination.xml` |
| Per-entry pause/resume/delete/CAS | Included in the 76; direct focused run passed | `test_P6_CONFIG_07_multi_entry_identity_cursor_and_dispatch_key` |
| Notifications | **4 passed** | `notification-cold-selected.xml` |
| QQ cold query/no replay | **2 passed** | `qq-cold-existing.xml` |
| Cancellation | **1 passed** | `cancel-cold-selected.xml` |
| Source/static | AST, final contracts and diff checks passed | command log |
| Production build/Chromium/remote CI/Linux/macOS/real migration/deployment | Not run | unverified |

Unknown or missing durable receipt continues to preserve original bytes and
block adoption. No push, deployment, protected-port action or real data/profile
operation occurred. `main` and `practical` remain unchanged.
