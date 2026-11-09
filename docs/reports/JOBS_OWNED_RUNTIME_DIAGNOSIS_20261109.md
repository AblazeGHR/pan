# Owned runtime / scheduler fixture diagnosis — 2026-11-09

Owned branch: `test/jobs-owned-executor-20261109`, previous candidate `c26609673db8649bb0c831d7757eedde7ce80af5`, original product base `b06a6a8799f44ea257c0f5deefb4f5086cc70b6e`.

DS tree was read only. Observed tips: `22fa99292eea47487139060d70ce8f6586deb753`, `14b42cb5ea318898de105400d8e1bfa97e3d370f`, `392a4c4ae28815bdadb9b77dedc8783ba9080251`. The 22fa compose_broadcast hunk selected typed v2/literals; by 14b it was restored to original runtime mapping. This candidate does not edit that product mapping or DS files.

## Root cause and instance checks

Reproduced real TCP HTTP `POST /api/jobs/legacy-create` session-broadcast + once schedule + owned-a target as **503 provider_unavailable**, after the same `automation_api.install(SimpleNamespace(app=app))` used by the DS launcher. The endpoint masks the internal provider message. Direct call in a separate owned root established the original stack:

`legacy_job_compat.compose_broadcast.mutate -> scheduler.providers.legacy_schedule_plan -> TimeProvider.zone(None) -> core.platform.timezones.host_zone_name -> import tzlocal`.

The prior provisioned `D:/project/Pan/.venv/Scripts/python.exe` lacks **tzlocal**, and it also lacks **tzdata**. Both are already declared by `minimal-requirements.txt`; neither is a new product requirement. Installing only tzlocal in an owned interpreter exposed the second error, `ZoneInfoNotFoundError: Asia/Singapore`. Evidence: `red.txt`, `cause.txt`, `red-tzdata.txt`. The failure precedes provider consumption. Replacing runtime action with typed v2 does not address it and removes deliveryRuntime behavior.

`automation_api.install` only mounts the automation/cookie surface here. Tests retain identity before/after installation and check:

- `app.state.owned.service is app.state.job_final_service is automation.final_jobs`.
- The same auth instance, same Creator provider registry, active original owner and running FinalService pulse task.
- Cookie login with same-origin writes and explicit Bearer both create user/local-owned Jobs in the same `profileId`.
- Original resolver/references validate the original runtime Action pin. The same registry resolves the real time/scheduler generation+release reference and reports provider health.
- The examined baseline and DS tree contain **no executor.action_available method**. No invented availability API or permissive override was added. Actual references, dispatch, output and per-target receipts are the evidence.

The earlier ready actionRefs was a fixture discovery filter, not an executor/provider allowlist. Ready now returns **all original published Action objects and object_refs** from the same service, including product_process, product_process_cleanup_v1, product_process_logs_v1, product_session_broadcast_runtime_v1 and product_assign_runtime_v1. Product code must keep selecting its own exact release. The executor identity remains the original default instance.

## Minimal fixture and startup configuration

A provisioned owned interpreter now exists at:

`D:/project/pan-worktrees/jobs-owned-executor-20261109/.tmp/runtime-env/Scripts/python.exe`

It has user site disabled, an explicit `provisioned-pan.pth` pointing to the pre-existing provisioned Pan venv site-packages, and **only tzlocal 5.4.5 + tzdata 2026.6 installed locally**. The attempted fresh minimal install was stopped during dependency resolution because repeated metadata requests were slow; no global environment was changed. `environment.txt` checks every applicable minimal-requirements distribution/specifier successfully and records the actual host zone **Asia/Singapore**. This layered local interpreter depends on that provisioned package path; it is not a portable environment bundle.

Reproduce this environment in a new owned venv if needed:

```powershell
& D:/project/Pan/.venv/Scripts/python.exe -m venv .tmp/runtime-env
Set-Content .tmp/runtime-env/Lib/site-packages/provisioned-pan.pth 'D:/project/Pan/.venv/Lib/site-packages'
& ./.tmp/runtime-env/Scripts/python.exe -m pip install --no-deps 'tzlocal==5.4.5' 'tzdata==2026.6'
```

Standalone dynamic-port harness, from the candidate worktree (root must be NEW):

```powershell
& ./.tmp/runtime-env/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/pan-worktrees/jobs-owned-executor-20261109/.tmp/runtime-env/Scripts/python.exe -m tests.support.jobs_owned_executor_server --root D:/project/pan-worktrees/jobs-owned-executor-20261109/.tmp/runtime-ui-unique
```

After cherry-picking the candidate onto DS, **use this owned interpreter to launch DS's existing owned-jobs-ui-server.py**. Keep its PAN_OWNED_ROOT in a new owned directory with `.jobs-owned-harness`; set PAN_PORT=0 for uvicorn dynamic allocation (observe the actual bound URL in its log). The isolation test wrapper removes PAN_* values, so do not place those launcher-specific settings outside that wrapper and expect them to survive. For DS launcher, start a clean process environment with only the required PAN_OWNED_ROOT/PAN_PORT task settings. No service start, UI build or DS launcher execution is claimed in this diagnosis.

`app_for(root)` now fails early with an actionable error if the real host calendar dependencies cannot resolve. It does not monkeypatch host_zone, force UTC, override registry generations, re-publish Actions or replace runtime bindings. Ready reports interpreter/hostZone, full actions/actionRefs, timeProvider reference/health and binding checks. Mount automation_api.install once as the DS launcher already does.

Both send_session and assign providers consume only synthetic fixture targets or Sessions whose identity files resolve directly inside this marked owned root. This also supports DS's generated Owned Alpha/Beta IDs; a selector is not required to choose only the fixed owned-a/owned-b names. Consumption is written before receipt. owned-unknown still consumes with a lost acknowledgement; unknown remains query-original. No real Worker/CLI is invoked.

## Verification and limits

Runtime broadcast acceptance asserts original product_session_broadcast_runtime_v1, input bindings and deliveryRuntime before-reservation; original per-target child receipts are accepted/accepted/unknown; cold and HTTP reads leave consumed bytes unchanged. Manual Run uses the required original `delivery` input schema; empty input was a preserved test setup red, not a product fix. Replay of the original create request returns the same Job.

Production product_process reference is tested via legacy-create, followed by an explicit Run and real stdout/stderr/log API. This does **not** claim that facade background-process automatically admits a Run; that separate product issue remains owned by DS. No product Action release is changed to accommodate the fixture.

Original time/scheduler pulse is tested through a real interval schedule and product_assign_runtime_v1, with a real event-caused completed Run and owned consumption. A separate once schedule red is retained: mapped graceSec=0 yields original expired/backfill when the pulse arrives late. Relevant symbols: scheduler.json_handoff.JsonTaskHandoff._map (default graceSec=0), scheduler.providers.TimeProvider due classification, jobs.schedule_calculations.misfire_outcome. No assertion or default was changed to hide this. Interval/fire_now is an explicit test schedule; no frozen clock or timing override is used.

Evidence directory: `evidence/jobs-owned-runtime-20261109/`. No product/DS/React files edited, no new product executor/protocol, no main/practical changes, no push. Browser acceptance and DS's latest product business fixes are separate verification layers.

Final acceptance: **12 passed in 81.27s**, zero skips, from the final helper/test source (`final.txt`): original 6 process/log/cancel/permission/owner tests plus 6 runtime/production-reference/clock/preflight/owned-selector tests. All applicable minimal-requirements version checks passed (`environment.txt`). Normal repository pre-commit hook applies; no frontend source is staged. This is real local TCP HTTP, synthetic provider and owned process evidence, not DS browser or remote CI acceptance.

The original environment inspection verified general HTTP/process imports but missed the calendar dependencies; the new preflight and scheduled facade tests cover that gap. Startup instructions in this report supersede the earlier harness report's interpreter choice.
