# P6 JSON dispatch receipt reconciliation and shell candidate

Baseline: `d9660dd6c3aa77258417969314e2f05de04fc2ca`.
Branch: `implement/action-unification-p6-20261106`, original isolated worktree.
The delivery message supplies the exact candidate SHA. This is a partial P6
candidate awaiting MA review; it does not close overall legacy parity.

## Contract and source changes

Reviewed revision 4 plan sections 12, 16 and 17 and the P3 MA review identity
boundary. Existing FinalService, OwnerFence, final_operations, original Job
aggregate, immutable plans, independent executor and execution host remain the
only authorities. No new scheduler, effect engine, retry engine or operation
ledger was introduced. Binding metadata contains receipt digests and aliases,
not a second execution state machine.

`json_dispatch_receipts.review` treats original runs.jsonl as a locator. An
explicit mapping `reconcileDispatches: true` can join a dispatch to an already
existing canonical Job/Run and an actual durable provider receipt. It cannot
create a Run/Attempt/effect from a journal, infer identity from similar text,
settle unknown, enqueue another message or launch a process. Each counted
dispatch must have one retained journal identity, one exact frozen dispatch
key, terminal Run/effect, original Job/config and matching actual receipt.
An original checkpoint, when present, must equal the retained aggregate.

For processes the check includes the existing execution database, effect ID,
Attempt owner/generation/lease fence, frozen executable/arguments/completion,
result outcome and OS supervisor exit/cleanup proof. Native process-exit
receipts omit the default completion field; only that exact default is treated
as equivalent. For session/assign the check uses the original child reservation
and read-only query_original_child, with its actual queueItemId. Complete
journal coverage of runCount is required; rolling/incomplete journals remain
blocked. The existing Job envelope, aggregate and authority remain unchanged.
History retains old journal run_id as runId and adds canonicalRunId, avoiding
two projected history records for the same dispatch.

The shell mapping uses the existing `product_process_logs_v1` release and
platform shell_command_argv. The original full command is one opaque interpreter
argument; source text is not substituted and argv is not reconstructed. An
explicit absolute cwd and the original hideWindow boolean/default are retained.
No 30-second command-host timeout is added to an old detached shell process.
Existing pendingProcessAttempt proves a returned running dispatch for legacy
runCount; an Event/admission alone still does not increment it.

## Compatibility and named evidence

| Original entry / gap | Successor and exact evidence | Remaining limit |
| --- | --- | --- |
| Old shell action rejected by JSON mapper | P6_JSON_SHELL_01: actual owned process, literal shell operators/braces, original cwd/hideWindow, fixed release, real logs, count once and cold no replay | Relative/omitted cwd needs original base proof; no arbitrary cwd reinterpretation |
| Old counted dispatch lacks embedded finalRuntime | P6_JSON_RECEIPT_01: existing original Run plus actual process receipt; apply retains identical aggregate; cold receipt lookup; original journal ID | Logs alone cannot recover an absent canonical Run/Attempt/effect |
| Retained session delivery locator | P6_JSON_RECEIPT_03: actual local persistent Session queue receipt, same queue item and aggregate after adoption/cold query | No model Worker started; not CLI consumption or QQ acceptance |
| Unknown/mismatched log, receipt, count or checkpoint | P6_JSON_RECEIPT_02: locator, log unknown, receipt unknown, incomplete count, pending dispatch, notice, lease and checkpoint mismatch all block; byte equality and one execution asserted | No retry, synthesized identity or automatic settlement |
| Active process resource | P6_JSON_SHELL_02: real held process, query/recovery retains Attempt; re-adoption blocked, later original execution finishes once | Active legacy resources are not yet transferable |
| Disabled unselected sibling with unsettled work | P6_JSON_RECEIPT_04: terminal outbox, pendingRuns, execution and recovery intents block whole-profile cutover | Disabled is not proof of a stopped effect/notice writer |
| run-now HTTP return facts | P6_JSON_SHELL_03: actual TCP dynamically allocated listener, running processJobId, repeated key same operation/Run, terminal exitCode 0, counter once | Additive waitForDispatch; default remains accepted admission |
| scheduler_run_now MCP | P6_JSON_SHELL_04: actual standalone stdio subprocess and plugin IPC, wait_for_dispatch, same original Run, one real shell process | Not a real model CLI/QQ Worker |
| Operator dry-run/apply/status | P6_JSON_OPERATOR_01: actual CLI subprocesses; entire owned profile byte snapshot unchanged by dry-run; digest rejection, apply, cold same-key status, unknown status and zero effects | Only existing initialized, cooperative, stopped profiles; never enrolls unknown writer |

The positive predecessor fixtures explicitly initialize a cooperative synthetic
profile before source data exists, then perform actual WP02 admission and real
execution/local queue delivery. They prove recovery of that retained identity,
not qualification of an unknown historical installation. Pending dispatch,
notification/outbox, recovery intent, live or abandoned source leases and
unproven process identity continue to preserve/block. No real source was migrated.

## Operator entry

`scripts/migrate_json_scheduler.py` defaults to dry-run. It requires explicit
absolute profile, canonical and authorization roots, existing authorization
and owner stores, and an explicit mapping file with pinned refs/policies. The
canonical root must belong directly to that profile. It does not initialize
grants, enroll a profile, start an engine or launch providers.

Dry-run arguments:
`--profile-root <owned-profile> --canonical-root <owned-profile/canonical>
--authorization-root <existing-auth> --mappings <absolute-mappings.json>
--output <new-plan-outside-profile-and-auth.json>`.

Apply replaces mappings/output with
`--apply --plan <reviewed-plan.json> --expected-plan-digest <printed-digest>
--expected-owner-epoch <stopped-owner-epoch> --request-key <same-operation-key>`.
The existing source hash/cohort/journal/ref CAS, owner lease and durable handoff
intent remain authoritative. `--status-key <original-key>` reads the canonical
receipt; missing receipt returns unknown (exit 3), never replays an effect.
This CLI is not a real-data migration authorization or unknown-writer bypass.

## Verification layers and preserved reds

All test commands used run_tests_isolated.py, temporary auth/profile/stores,
owned processes and isolated dynamic ports. Source hashes, artifact hashes,
JUnit summaries and commands are in
`evidence/action-unification-p6/json-receipts-validation.json`.

| Layer | Result and qualification |
| --- | --- |
| Final JSON receipt/shell and existing JSON scheduler suite | 41 passed in json-receipts-delivery-checkpoint.xml; 19 new receipt/shell/boundary cases and 22 existing cases |
| Scheduler/legacy/owner/Worker combination | 163 passed, no fail/error/skip in json-receipts-combination.xml, before final sibling/checkpoint guard additions; final affected modules rerun above |
| Extended canonical group | 103 passed, 14 failed, 1 skipped (118 cases). All failures were live process-log fixture requests receiving proxy 502 before JSON; original evidence retained |
| Corrected direct-loopback process-log group | 20 passed, 1 explicit POSIX lost-leader skip in json-receipts-logs-direct.xml; NO_PROXY set only for test process, no system proxy/test assertion change |
| Actual HTTP/MCP | Owned shell HTTP and real standalone core stdio/IPC above passed; actual local persistent queue lookup passed |
| Source/static | AST parse of six changed Python files, git diff --check and isolated final_contracts --check passed |
| Build/Chromium | Not run this slice; no React/dist edits. Python checks do not establish production build/browser behavior |
| Remote CI / Linux/macOS | Not run; Windows local evidence only |
| Real data / deployment / human acceptance | Not performed; candidate awaits MA review |

Counts overlap and must not be summed as unique coverage. The original shell
red loads the mapper from exact baseline d966 and fails the unchanged positive
eligibility assertion. Initial supervisor cleanup and native default completion
shape reds, the CLI harness collection error, and the proxy 502 group are kept
as separate compressed artifacts; the harness error is not product-red proof.
Failed artifacts redact synthetic fixture token representations before gzip;
raw/redacted/archive hashes are recorded. Prior P6 reds remain unchanged.
Process-log tests regenerated historical cp-x2 proof JSON; those generated
overwrites were restored to HEAD rather than replacing earlier evidence.

## Open items and next work

P6 is still incomplete. These are concrete implementation/evidence gaps, not
new product decisions or claims of unsupported old capabilities:

1. A purely legacy dispatch with no existing canonical Run/Attempt/effect and
   no original provider identity cannot be adopted by this adapter. It remains
   unknown with bytes preserved. Further work needs the real retained identity
   model/receipt contract; a journal ID cannot impersonate a Run.
2. Live legacy resource adoption, pending occurrence/dispatch transfer and
   pending notification/outbox transfer remain blocked. This slice verifies
   safe refusal and existing canonical cold recovery, not complete migration
   of these original intents.
3. Full old create/update HTTP/MCP configuration editing remains unimplemented;
   no route is restored merely to allocate a replacement task identity. Next
   work must route validated config/ref/cursor updates through existing atomic
   Job/Creator/Trigger operations and receipts, preserving old field semantics.
4. Additive waitForDispatch returns actual shell dispatch/terminal facts through
   the same engine and lock; default synchronous run-now parity for all old
   providers, WS timing and terminal notifications remains open. Same-key
   replay, including unknown, stays read-only.
5. Relative/omitted shell cwd, complex/multiple schedule entries, arbitrary old
   Action families, all legacy Scheduler/Trigger lifecycle history/notification
   combinations and previously reported P6 gaps remain open unless covered by
   their earlier individual candidate evidence.
6. Real CLI/QQ, cross-platform service/browser checks, remote CI, real-data
   migration and manual acceptance remain separate unverified layers.

No push, merge, deployment, protected-port operation or real-data/profile
operation occurred. main/practical refs are recorded unchanged in the evidence
index. The normal commit hook runs for the candidate; MA decides acceptance.
