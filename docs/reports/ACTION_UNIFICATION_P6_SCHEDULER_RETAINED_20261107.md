# P6 revision 4 — retained Scheduler successor query and explicit Run cancellation

Parent: `911935d95dc212af79398f1af684717f6a65e1e4`.
Branch: `implement/action-unification-p6-20261106`.
This is a **partial candidate**. Neither this Scheduler/Trigger group nor P6 overall is complete.

## Named red and compatibility matrix

`P6_SCHED_01` executes the actual parent Scheduler API source obtained with
`git show`, mounts it on the owned FastAPI host, and calls a random-port TCP
endpoint. `scheduler-retained-original-red.xml.gz` records the missing
`canonicalSuccessor` field, while proving the original unknown history row was
returned. This is a real baseline red, not a fabricated HTTP fixture response.

| Original capability / identity | Candidate behavior | Evidence / remaining limit |
| --- | --- | --- |
| GET `/api/scheduler/tasks/{taskId}` and original Job-ID alias | Original task, raw-byte digest, revision 7/generation 3 and delivered notification marker remain intact; additive `canonicalSuccessor` uses existing reviewed `runtimeMetadata.originals` plus exact source digest | `P6_SCHED_01` real TCP and cold service; this is provenance resolution, **not execution adoption** |
| GET `.../{taskId}/runs`, MCP `scheduler_runs` | Original `runs`, `runId`, `taskId`, `status`, `dispatchKey` remain unchanged. Successor Run/effect/outbox/cancellation facts are returned separately under `canonicalSuccessor`, bounded by the route's limit/offset | `P6_SCHED_01/04`; old unknown run stays unknown |
| Cancelling an explicitly reviewed successor Run from original task identity | New POST `.../{taskId}/cancel` and additive MCP `scheduler_cancel` require Job ID, Run ID, config revision, state revision and request key; existing FinalService operation receipt/CAS/cleanup path owns the request | `P6_SCHED_01/02/04/05`; accepted is not cancelled; no task-wide disable or future schedule cancellation is inferred |
| Original managed Session access | MCP resolves the persisted task target before any POST, checks it against the supplied target and reuses the existing managed-access check. HTTP/IPC also require existing Job read/execute grants | Real stdio plus forwarding test; no permission schema/grant added |
| Cancellation race and lost reply | Same-key calls resolve the existing `final_operations` receipt; competing revisions produce one accepted request and one revision conflict. Postcommit cleanup is extracted from the existing FinalService control path, not a new engine | `P6_SCHED_01/02/05` |
| Notifications and history | Successor history exposes the actual original outbox IDs/receipts. Real Session notice queue accepts one notice after two drains; cold queries retain that outbox and Run association | `P6_SCHED_03`; synthetic work provider, actual local notice queue; no real CLI/QQ |
| Unconfirmed notification | Actual outbox state remains uncertain and is not resent after reopening/draining | `P6_SCHED_06` |
| Fixed canonical Trigger → Job/Event identity | Same Event/Trigger yields the original canonical Run/operation after reopening; successor query observes it without dispatch/replay | `P6_SCHED_07`; does not claim an old Trigger ledger row was adopted |
| Unknown/active original, missing/ambiguous alias, source mismatch | Preserve original bytes; refuse successor control unless there is one exact reviewed alias and the old Scheduler row is disabled and settled | `P6_SCHED_02/05`; not a new handoff mechanism |
| Owner epoch/nonce, revoked grants, foreign Run | Existing owner fence and canonical Run membership/revision checks reject before provider calls | `P6_SCHED_05` |

The existing historical tool inventory remains unchanged. `scheduler_cancel` is
an additive controlled successor, declared separately in `P6_COMPAT_MCP_TOOLS`.
No legacy runtime writer, Trigger ledger, scheduler or execution engine was
reactivated. An initial experiment using LegacySchedulerBridge was discarded
because that host is a legacy writer, not the FinalService authority.

## Commands and observed results

Source SHA-256 values, command lines, protected refs and every JUnit result are
indexed in `evidence/action-unification-p6/scheduler-retained-validation.json`.

All commands used `python scripts/run_tests_isolated.py -- ...`. Fixtures own
their temporary stores, random TCP listener, IPC bridge and stdio child. The
TCP fixture checks the port is neither 8767 nor 8768 and joins/closes it.

- Focused: `python -m pytest tests/test_action_unification_p6_scheduler_retained.py tests/test_job_mcp_config.py -q` — **15 passed**, 0 failed/error/skipped; `scheduler-retained-focused-final.xml`.
- Latest auth/HTTP follow-up: `-k P6_SCHED_01` — **1 passed**; `scheduler-retained-http-final.xml`. This includes unauthenticated cancel denial, original aliases, TCP cancellation and cold query.
- Core/native stdio inventory/regression: `tests/test_job_final_clients.py -k test_real_stdio_crud_operations_permissions_and_legacy` — **2 passed**; `scheduler-retained-stdio-regression.xml`. The new Scheduler call itself is separately exercised by `P6_SCHED_04` in the focused group.
- Combination: Scheduler history/API/MCP + FinalService HTTP/clients/process-pending — **251 passed, 1 failed** out of 252; `scheduler-retained-combination-red.xml.gz`.
- Additional retirement inventory run — **88 passed, 6 failed** out of 94; `scheduler-retained-retirement-red.xml.gz`. This overlaps the focused tests and is not an additional pass count.

The combination/retirement failures are retained, not skipped or weakened:
four old Template routes no longer satisfy the retirement-only assertion;
anonymous Template DELETE answers 422 before auth when expectedRevision is
missing; the aggregate retirement assertion fails; and the old facade Template
instantiate test expects 410 but gets 400. They precede this slice. The
read-only `tests/support/p6_scheduler_baseline_probe.py` loads every modified
existing product module from the pinned parent and runs the unchanged failure
assertions: **55 passed, the same 7 failed** out of 62, recorded in
`scheduler-baseline-retirement-red.xml.gz`.

Red XMLs are stored losslessly as gzip, with raw SHA-256 and byte lengths in the validation index. Harness/experimental reds also remain named separately: UTF-8 baseline source
decoding, notification receipt/IPC-loop setup, and the discarded legacy bridge
experiment. These do not replace the product baseline red.

## Open implementation gaps and acceptance boundary

1. **Old Trigger ledger ownership/identity is not transferred.** Original
   `/api/automation/runs/{rid}/cancel` and old Trigger notification/history
   fields have no committed same-Run/Attempt/effect mapping in this slice.
   No guessed state revision, original Event or synthesized receipt is used.
   This is unfinished implementation, not a claim that all such rows are complex.
2. **Active retained Scheduler jobs are not handed off.** A provenance alias
   alone cannot establish writer/lease retirement or adopt an in-flight old
   occurrence. They remain byte-preserved/blocked. The next implementation must
   join the original owner/handoff receipt and old occurrence identity to the
   existing canonical admission; a fresh successor Job is not that migration.
3. **Task-wide cancel/disable, pause/resume/run-now and full old lifecycle
   aliases remain open.** The additive cancel here addresses one explicit
   canonical Run only. It cannot substitute for those old capabilities.
4. **Seven Template baseline failures remain release blockers** pending repair
   of that prior slice's authorization/retirement contract and meaningful successor
   assertions while retaining the original reds.

Source imports, focused tests, real random-port HTTP, real core stdio/IPC,
local durable notice queue and cold storage reads are evidenced above. No
production frontend build/Chromium (no React change), remote CI, real data
migration, model CLI/QQ delivery, Linux/macOS execution, deployment or human
acceptance was performed. The existing supervisor/process evidence is not
relabelled as evidence for old Trigger adoption.

No push, main/practical advance, real data/profile access or protected-port
operation occurred. Candidate awaits MA review; this report does not claim MA
acceptance or overall P6 completion.
