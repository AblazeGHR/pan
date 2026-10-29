# JOB-RETIRE revision11 — default assign/shell compatibility wrappers

Clean candidate source: `6ce6ab8ac2a35c63d5ece20038dae27a81815690`, branch `r6/retirement-parity-acceptance-20261029-a`. Baseline was clean candidate `cce3a647d838301cfb3956615156bcf48f4d3553`. Only `packages/jobs/final_service.py`, its owned revision11 tests and evidence changed.

The new pure `normalize_legacy_form_fields` adapter translates old assign and shell form fields into canonical Action arguments. It does not persist, publish, dispatch, spawn, select a provider, or write any legacy registry. Callers compose its result with the existing immutable `product_assign_v2` or `product_process_cleanup_v1` Action and continue through canonical admission, Run/Attempt/effect and existing providers.

Assign mapping accepts `sessionId`, `targetSessionId`, `target.sessionId`, `text`/`message`, `source`, `sourceSessionId` and `taskId`. It preserves source fields and text bytes, rejects conflicting aliases, rejects unknown fields and validates non-empty string identities. The real owned queue test proves the existing WorkerAssignProvider receipt, taskId and no-spawn boundary.

Shell mapping accepts `command` or `argv`, plus `cwd` and `hideWindow`. Supplying both command and argv is rejected. A command string becomes an explicit platform shell argv boundary; an existing argv list is preserved element-for-element. The real owned process test proves cwd, exit and canonical process receipt through the existing process executor, with no early execution.

Canonical fields win only when identical. A legacy value that disagrees with an explicit canonical argument raises `reference_conflict` before persistence. Unknown fields and malformed source/target/argv values raise `invalid_schema`.

The current generic canonical Template instantiate endpoint was tested with an assign definition and no Creator. It returns `capability_unsupported` because that endpoint intentionally requires a Scheduler Creator. This is recorded as a precise design conflict. The batch does not restore generic dispatch, add a no-Creator Template protocol, or alter Scheduler/FinalRuntime. Frontend caller wiring remains unverified and is a follow-up at the existing form boundary.

## Immutable verification

Final command used the designated `D:/project/Pan-main/.venv/Scripts/python.exe` through `scripts/run_tests_isolated.py`, serially, at source `6ce6ab8a`:

`tests/test_retirement_rev11_compat.py tests/test_session_product_closure.py tests/test_job_automatic_recovery.py tests/test_job_final_http_service.py tests/test_job_final_executors.py tests/test_retirement_rev6_schedule.py tests/test_retirement_rev6_parity.py::test_scheduled_explicit_shell_argv_preserves_command_cwd_exit_and_no_early_creation`

Result: **142 passed, 0 failed, 0 errors, 0 skips, exit 0, 84.936 seconds**. The dedicated revision11 mapping tests contribute 8 of those passes; the affected canonical/session/process/recovery regressions contribute the remainder. Machine-readable command, source binding, mapping and hashes are in [parity-matrix.json](../../evidence/job-retirement-rev11-20261029/parity-matrix.json).

Earlier capacity refusals occurred before the affected regression command began and are not included in the final count. The no-Creator Template `capability_unsupported` result is preserved as an explicit conflict, not converted to a green compatibility claim.

No generic dispatcher, Worker, provider, scheduler, FinalRuntime, contracts, MCP server, shared fixture, real profile/data, protected port, main/practical, push, remote CI, deployment or WSL operation occurred. Existing r6-r10 evidence and red records remain unchanged. Remaining items are frontend/form caller wiring, the no-Creator Template design decision, MCP compatibility, monitoring/stable-reset, unknown fallback/clear-target, real migration and deployment.
