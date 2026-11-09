# Owned Jobs executor harness — 2026-11-09

Base: `b06a6a8799f44ea257c0f5deefb4f5086cc70b6e` (`feature/action-group-design-canonical-20261109`). Candidate branch: `test/jobs-owned-executor-20261109`. Worktree: `D:/project/pan-worktrees/jobs-owned-executor-20261109`.

## DS / MA integration

Use the already provisioned `D:/project/Pan/.venv/Scripts/python.exe` (user site disabled). Read `minimal-requirements.txt`; verified fastapi, uvicorn, httpx, pytest, jsonschema, mcp and psutil imports. No global or local installation was needed.

From candidate worktree, in PowerShell:

```powershell
& D:/project/Pan/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan/.venv/Scripts/python.exe -m tests.support.jobs_owned_executor_server --root D:/project/pan-worktrees/jobs-owned-executor-20261109/.tmp/ds-owned-unique-run
```

Root must be a NEW absolute path; choose a fresh suffix on each launch. First JSON line from harness contains `url` and `root` (the isolation wrapper also prints a banner). Socket is bound once to `127.0.0.1:0` and passed directly to uvicorn; no port reservation race. 8767/8768 refused. Stop with normal process termination / Ctrl+C for lifespan cleanup; do not kill the detached supervisors independently.

`GET /test/ready` returns a fixture-only bearer `token`, `actionRefs`, `scripts.quick`, `scripts.long`, `cwd`, `targets`, `consumed`, owner state and runtime diagnostics. Token is ephemeral owned-root data: do not copy its value to evidence. Send `Authorization: Bearer <token>` to canonical `/api` routes. This control endpoint is intentionally loopback test-only and is not a product authentication design.

Use `GET /api/jobs/actions` for original published Action objects. Process Action: `product_process_cleanup_v1`. Broadcast: `product_session_broadcast_v2` (v1 rejects duplicate IDs by schema). Use `ready.actionRefs[actionId]` directly: the original `object_ref` helper returns `id`, `revision`, and the definition `digest`; do not synthesize identities. A complete executable HTTP create/enable/run/cancel sequence is in `submit` and `test_http_pulse_process_and_broadcast` in the companion test.

Process arguments are literal `argv = scripts.quick` or `scripts.long`, `cwd = ready.cwd`; completion contract `process-exit`. Quick writes real `owned stdout\n` and `owned stderr\n`. Long writes both then sleeps 300 seconds and can be cancelled with the canonical Run operation.

Broadcast arguments: literal `sessionIds = ["owned-a", "owned-b"]`, `text`; completion `durable-queue-accepted`. Owned provider consumes text into `<root>/consumed.jsonl` BEFORE returning a confirmed receipt. `owned-unknown` also consumes, but returns unknown to model lost acknowledgement. Unknown must remain query-only; do not retry with a new request/effect identity. Other target IDs are rejected. These are synthetic Session consumers, not real CLI Worker delivery.

Read `GET /api/job-runs/{runId}`, `/api/job-runs/{runId}/logs`, then returned log `href` (offset/limit pagination supported by original API). Cancel via POST `/api/job-runs/{runId}/operations` with operation `cancel`, current `expectedStateRevision`, unique `requestKey`. No custom execution routes or replacement product executor exist.

The module also exposes `app_for(marked_owned_root)` for DS to mount behind its own UI server/proxy. It intentionally does not build or host the React bundle. It sets process-global Session/config/background-job roots, so use one app per dedicated harness process. A `.jobs-owned-harness` marker is required for embedded assembly; CLI creates it only in a new directory.

## Execution and boundaries

Assembly uses original `final_service.bind`, FinalRuntime store, default TypedExecutor identity, original detached external adapter/ProcessOwner, `FinalService.start()` dispatch/pulse and canonical routers. Only the owned session provider is substituted. Lifespan cleanup reads execution identities only from this root and calls original ExecutionHost cleanup. Process-log acceptance reuses `tests/test_job_process_logs.py` helpers and its supervisor/owned-tree proof teardown.

Evidence lives in `evidence/jobs-owned-harness-20261109/`. Initial red setup/API/schema failures are preserved separately. Product source, packages/web/e2e/server.py, DS e2e and all React files are unchanged. No push, main/practical integration, real account/profile/service/data mutation, protected port, build, remote CI or human UI acceptance is claimed.

Cold provider test re-creates TypedExecutor with the same original receipt store; canonical HTTP test separately opens cold FinalService readers, including a fresh Python interpreter, against the same owned durable store and proves unchanged consumption after unknown. This is query/read no-replay evidence; full service restart/recovery and real Worker delivery are separate layers.



## Validation result

- Focused suite: **6 passed in 46.40s**, no skips (`pytest.txt`). Final fresh-interpreter cold-reader addition: **1 passed in 10.77s** (`focused.txt`). Both invoked through `scripts/run_tests_isolated.py` with the provisioned absolute interpreter.
- Real TCP HTTP exercised service start/pulse, original process output/log read, long process cancellation, broadcast per-target consumption, unknown query no replay, shutdown success and port release. Reused process-log fixture additionally verifies original supervisor exit and owned-tree cleanup.
- Agent read permission revocation raises original ContractError; claimed/active owner then release refuses canonical writer entry. Initial pre-cutover owner fixture mistake is retained in red evidence.
- Original broadcast v1 duplicate-ID schema rejection and initial pending log discovery response are preserved in red evidence. Correct v2 and explicit 200/404/409 pending polling used. No assertion reduced; unexpected statuses remain failures.
- No blocking product bug identified. No API/schema/product implementation changes. No frontend build or browser/DS UI acceptance performed.
- Protected refs observed: main `99890a0c32890977e31ca0523ea3262dc3828542`, practical `0ed770555493c820355c4f906c2b067425b2cc98`.
