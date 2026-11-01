# R6-B HTTP write leaf retirement

Base: `e153fc4d8b85484a7e9ea27392c36fbdc0f60f5f`.
Branch: `r5/p14-r6-http-writes-20261025`.

## Source and routes

Deleted six Python handler definitions from `packages/web/server.py`:
`api_background_job_start`, `api_session_message_job_start`,
`api_background_job_update`, `api_background_job_retry`,
`api_main_exit`, `api_main_restart`.

Effective method/path subtraction is exactly:

- `POST /api/main/exit`
- `POST /api/main/restart`

Both return 404 through the production app, including with stale CLI identity
headers. The CLI identity guard now applies only to the canonical lifecycle POST.

Four old Job addresses deliberately retain **only explicit 410 tombstones**:
POST background-jobs, POST session-message-jobs, PATCH background-jobs/{job_id},
POST background-jobs/{job_id}/retry. These registrations remain for the requested
410 negative controls; no old producer is registered or defined. This also avoids
405 for writes sharing a retained GET path. Anonymous refusal remains unchanged.

`route-diff.json` records the decorator subtraction; the exact effective route
snapshot is `tests/fixtures/r6_http_write_routes.json`, verified against production
FastAPI routing. No added production addresses.

## Callers and retained scope

The whole baseline-tracked readable-text scan covered 6,310 files and produced
419 classified hits in `references-after.json`. It includes historical evidence,
docs and obsolete positive test fixtures; path strings alone are not live callers.
The new AST test checks packages and scripts for direct names, attributes,
imports and literal getattr references to all six deleted handlers: none remain.

`stop.bat` and `restart.bat` now send the fixed
`POST /api/system-actions/main-lifecycle` with operation exit/restart. Real
cmd.exe -> PowerShell -> curl -> ephemeral TCP receiver tests check parsed JSON,
HTTP failure and business failure. CLI, stdin console and managed Windows console
already used the fixed Action route; their transport tests preserve that contract.
The settings E2E request monitor now observes canonical operation payloads.

Retained: background GET list/detail and cancel; lifecycle status GETs; canonical
receipt lookup by original effect ID across host-memory loss; legacy read facade;
`_legacy_action_job`; all scheduler routes and MCP tools/functions. Legacy MCP
write function bodies still contain old HTTP strings, but R5 removes these tools
from the callable/discoverable registry and the HTTP addresses refuse with 410.
No E04/E06/E07-E09/E15 implementation changes.

## Verification

All pytest commands used `python scripts/run_tests_isolated.py -- python -m pytest`.

- `final-focused.xml` / log: **236 passed, 0 failed/errors/skipped**, exit 0.
  Modules: lifecycle_shortcuts, pan_cli, console_exit, platform_api,
  p5b_lifecycle_action, job_final_retirement, r6_legacy_leaves,
  main_lifecycle_jobs, platform_import_chain, job_path_flip, r6_http_writes.
  Includes server imports, simulated POSIX imports, isolated canonical/Automation
  startup/shutdown, existing canonical route snapshot and TCP authorization/CRUD.
- `http-leaves.xml` / log: **19 passed, 0 failed/errors/skipped**, exit 0,
  after adding the two console transport checks and AST reverse-reference check.
  Overlaps 16 cases above: **239 distinct passing cases** across these two runs.
  Covers production ASGI negative requests with valid/invalid effect-bearing
  payloads, fail-fast store/lease/lifecycle constructor guards, byte preservation,
  actual ephemeral TCP refusal and durable receipt lookup. Receipt completion is
  simulated; no real main service stop/restart is claimed.
- `node --check packages/web/e2e/app-settings-categories.e2e.mjs`: exit 0.
- `git diff --check`: exit 0.

Red evidence is retained explicitly:

- `baseline-lifecycle.xml`: before source edits, **31 passed / 15 failed**, exit 1.
  Old lifecycle tests call a canonical executor while asserting the retired
  service-job/supervisor behavior. Only their call adapter was migrated in this
  change; assertions are preserved. They were not rerun through real execution.
- `collection.xml` / log: full `tests/ --collect-only` exits 1 with **4 errors**,
  all `ModuleNotFoundError: playwright`. No collection skip or assertion weakening.
- `focused.xml` and `http-shortcuts.xml`: intermediate BAT JSON quoting and
  receipt-fixture setup failures; fixed and superseded by final green runs.

Other obsolete positive fixtures still reference removed HTTP Python handlers in
test_background_job_api, test_background_job_retry_if, test_job_window_policy and
test_job_runner_venv. These are not production callers and were not represented
as passing or migrated to weaken their runner/window safety assertions.

No full-suite green claim. No React build or browser execution. No remote CI,
real-data migration, deployment or human acceptance. No push, protected service
operation or intentional 8767/8768 access; TCP tests bind owned ephemeral ports.
