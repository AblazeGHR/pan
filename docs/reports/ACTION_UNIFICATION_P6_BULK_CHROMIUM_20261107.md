# P6 Chromium and multi-entry completion slice

Baseline: `f2045961aba8719742c188b3752bcd5774d4b616`.
Branch: `implement/action-unification-p6-20261106`.
Worktree: `D:/project/pan-worktrees/action-unification-p6-20261106`.
The exact delivered SHA is the commit containing this report (also supplied in
TA delivery). This report covers the named slice, not overall P6 acceptance.

## Scope and compatibility contract

The implementation continues through FinalService transactions, the existing
OwnerFence, final_operations receipts, one Creator and the original Job/Trigger.
No scheduler, execution engine, effect ledger or notification ledger was added.
The cooperative synthetic predecessor is enrolled before data creation. Existing
unknown writers/leases and missing original execution evidence are never given
synthetic migration eligibility.

| Named scope | Successor and acceptance |
| --- | --- |
| Chromium visual-order-round | Recognize durable terminal DOM metadata; verify original eventId/status as well as unchanged order/geometry/content assertions. Full production Chromium chain passes. |
| bulk replacement | Reuse old `_spec_to_entry` calculation. Like old `replace_task_schedule`, replacement creates new entry IDs; it does not relabel them historical IDs. Same task/Job/Creator/Trigger and receipt remain. CONFIG_11/14/15/05. |
| mixed atomic editing | Metadata uses original pure `store.plan_update`; entry CAS/config/state and metadata commit together in the existing transaction. A stale CAS rolls back every field; an edit preserves an independently paused live clock. CONFIG_10/12/14/20/05. |
| new entry insertion | `appendSchedule` plus optional `insertIndex` (MCP append_schedule/insert_index); existing IDs/order/cursors retained, new specs receive new IDs. Real time tick, Event ruleId, Run/Attempt/effect and duplicate admission verified. CONFIG_11/14/17/05. |
| final-entry deletion | Existing task retirement primitive, preserving tombstone metadata and original execution identity. Empty time Creators are not invented. Unsettled Run/publication rejects the whole edit atomically. CONFIG_11/14/16/05. |
| native/gateway stdio | Real subprocess and IPC through existing core binding. Native uses --view native --core; gateway exposes pan.scheduler_* through api_discover(plugin=pan)/api_call under --view gateway --core. CONFIG_05, parametrized core/native/gateway. |
| concurrent lease/owner | Real TCP concurrent task/entry CAS, one winner; original-key replay; separate OS owner lock refused; legacy lease admission fenced; stale owner token rolls back. CONFIG_12 plus original JSON ownership/lease suite. |
| notifications | Real Session notice enqueue through existing outbox sender; WS and Session each once; frozen Run retained across bulk editing; lost Session receipt remains uncertain and cold recovery never redelivers. CONFIG_13, known/lost receipt. |

Old flat MCP create/update/run-now/history shapes remain. HTTP normalizes list
entries as before for scalar schedules, preserves task aliases, entry policies,
grace, cursor/revision/pause and lifecycle permissions. Gateway checks core
selection on every call and rejects stale contract digests. It grants no new
object permissions. Pan core's manifest binding remains standalone; native and
gateway here refer to the generated stdio connection projection, following the
existing core-sharing contract, not an invented Pan binding mode.

The original `/api/jobs/{jobId}` and facade `/api/legacy/jobs/{jobId}` bulk PATCH are transferred for an already
adopted scheduled-task binding. It authenticates first, delegates validation and
mutation to the same scheduler adapter, and returns the old job envelope and
original jobId. It never restores a JSON writer. Unbound records keep their
existing guarded path. Unknown edit fields reject without mutation.

Bulk operations require task revision; per-entry operations require entry
revision. Missing entry IDs, insertion index without specs, conflicting delete
and replacement, stale revisions, revoked grants, pending publication and an
unsettled final delete do not silently drop fields. Existing fixed ActionRef and
frozen Run plans survive configuration edits. `knownEntryIds` is binding metadata
for explicitly created rule identities and original rule IDs, used to preserve
ruleId dispatch keys; it is not historical execution identity or a new ledger.

## Red-to-green evidence

Evidence root is `evidence/action-unification-p6/`; `bulk-evidence-index.json`
contains source and artifact SHA256, command provenance and final counts.

- The original Chromium failure was reproduced on the unchanged production
  bundle: two terminal DOM rows were classified unknown by the old oracle.
  `bulk-chromium-baseline-red.json` retains it. The repaired oracle adds exact
  terminal identity/status checks rather than removing rows or assertions.
- Full Chromium then exposed an obsolete fixture assumption that the durable
  tail must be assistant. `bulk-chromium-terminal-fixture-red.json` retains that
  failure. The fixture now requires an addressable assistant and terminal tail.
- This revealed a real bounded-history convergence gap: a terminal after the
  assistant prevented the identity-based proof and left an empty thinking row.
  `bulk-browser-terminal-original-red.xml` and
  `bulk-chromium-bounded-proof-red.json` preserve independent unit/browser reds.
  The minimal store fix crosses only a durable terminal with eventId, still
  requires exact final identity and a loaded user boundary, and retains unknown
  identities. Positive and foreign-identity negative unit cases pass. Fresh
  production build and full Chromium pass (`bulk-chromium-green.json`, 44 stages).
- `bulk-original-red.xml` reproduces bulk list rejection. The unchanged f2045961
  entry editor is separately loaded by CONFIG_18 for mixed/insert/last-delete;
  all three original reds are retained in `bulk-named-f204-original-red.xml`.
  The same named cases pass against the successor in final validation.
- `bulk-jobs-original-red.xml` proves original Jobs PATCH returned 410; the same
  real TCP case passes through the adopted graph (`bulk-jobs-green.xml`).
  Production `install()` separately reproduced missing PATCH (405) / facade
  retirement (410). Both actual mount paths now pass with original identity
  (`bulk-production-mount-green.xml`), and canonical HTTP/retirement regression
  validates that unrelated writes retain their guards.
- `bulk-gateway-original-red.xml` proves missing core scheduler discovery;
  the same real gateway projection now discovers and executes the complete
  editing chain, then verifies contract/grant/disable rejection.
- `bulk-live-lifecycle-red.xml` reproduces a mixed edit accidentally resuming a
  previously paused task. The adapter now reads live Creator state and original
  binding pause before applying the patch; lifecycle/atomic boundary tests pass.
- `bulk-incomplete-controls-red.xml` reproduces two silently ignored controls;
  explicit identity/index validation now rejects without any mutation.

Fixture/control failures are preserved, and each has an independent corrected
green rather than an unexplained red exclusion:

| Retained experiment | Cause / independent green |
| --- | --- |
| lease-notice-first | OwnershipConflict uses code revision_conflict plus reason owner_busy; the notice assertion initially forgot the mandatory WS outbox. Corrected checks assert both channels exactly once; lease-notice-green and final suite. |
| stdio-security-debug / combination-security-harness-red | Legacy bridge intentionally wraps authorization as numeric 403 with structured message; fixture wrongly expected canonical string at outer code. Exact old error shape now asserted. |
| stdio-invalid-binding-harness-red | Fixture tried native/gateway binding modes for Pan core, whose manifest supports standalone only. Existing --core sharing with standalone binding supplies real native/gateway projections; final stdio green. |
| api-r5-contract-red | Three R5 module-owner assertions rejected the already approved scheduler successors. Original parametrized nodes are retained, exact two successor identities recognized, and new real-service cases forbid all legacy writer functions and verify stale-owner rejection. The full canonical HTTP/retirement set passes (107). |
| browser prior-tests-control | An initial cwd mistake ran only preexisting tests (32 passed); not used as evidence for the new regression. |
| initial production typecheck | New terminal fixture initially omitted required HistoryTerminal fields. Full typed fixture fixed; no cast/skip or relaxed typecheck. Final normal build passes. |

## Validation layers

Final JUnit counts and hashes are in the index. Earlier intermediate counts
must not be summed with final runs. Python uses the isolated runner with owned
temporary profiles; frontend uses packages/web pnpm commands.

- Backend combination: **102 passed**, zero failure/error/skip: task_config,
  json_receipts, json_scheduler, scheduler_api. Includes real TCP HTTP and real
  core/native/gateway stdio. The final narrow production mount change was then
  verified with both addresses (**2 passed**) and canonical HTTP/retirement
  regression (**107 passed**); these overlap earlier cases and are not summed.
- Projection regression: existing gateway directory/cursor, real stdio and
  core-sharing/disable tests (3 passed).
- Core/native/gateway security plus fixed-baseline successor cases: **6 passed**
  in independent corrected verification; overlaps the backend combination.
- Frontend focused: terminalTransient plus HistoryTerminal (35 passed); exact
  matching/foreign identity recovery guards included.
- Production: pnpm build passed, 2590 modules and 90 compressed assets verified.
- Real Chromium: pnpm e2e:frontend-full passed against the fresh production
  bundle, disposable profile/server on 8765, 44 recorded stages. Includes
  visual-order-round, switch/pinned/stress visual checks, 5000-row history,
  Worker crash/cold restart and both terminal/restart browser-store replays.
  Cleanup records portFree=true. Production bundle and source hashes are indexed.
- Static: final_contracts --check and git diff checks. Normal repository hook
  remains enabled; React TypeScript is checked at commit.

The stdio tests use real local processes, IPC and canonical storage with
synthetic Sessions and explicit fixture grants. Notification acceptance uses
real isolated durable queue enqueue. Chromium's deterministic CLI and browser
replay seams are owned fixtures; these results do not establish real external
provider/QQ/model CLI consumption.

## Boundaries after this slice

No unresolved red is accepted in this named slice. Historical red artifacts
remain for review and are not current acceptance results. Overall P6 still
requires MA integration review and any broader compatibility items outside this
named slice. Remote CI, Linux/macOS, real provider/CLI/QQ acceptance, actual data
migration, deployment and manual user acceptance are not performed or claimed.
Pure old logs without canonical identity/durable receipt, unknown notification
handoff and unknown writer/lease remain preserve/unknown/blocked, with original
bytes unchanged and no automatic replay. These are required guarded outcomes,
not a claimed successful migration.

No push, deployment, main/practical movement, real data/profile operation or
8767/8768 interaction occurs. The delivered candidate is for MA review.
