# JOB-RETIRE revision12 — legacy MCP thin compatibility

Clean candidate source: `f7116a898469bfd6334ad0d781338b86a1c7bfdf`, branch `r6/retirement-parity-acceptance-20261029-a`. Baseline: `4a86a3ce275159b203ef883f4072931ce7125bee`. Only `packages/mcp/server.py`, its owned tests and evidence changed.

The current MCP catalog already exposes canonical `job_*`, `trigger_*`, `event_creator_*`, `job_template_*`, `job_run_*` and `job_operation_*` tools. Existing retained legacy reads/cancel tools remain discoverable. Removed old write names are now covered by a pure structured compatibility disposition: callers receive `capability_unsupported` with the exact migration conflict and canonical tool family, without HTTP, IPC, persistence, Worker, Runner or scheduler activity.

The disposition covers:

- `agent_background_start` / `agent_background_retry`: old Runner owner and retry identity cannot be mapped to canonical Run/Step/effect/owner fences without restoring the retired lifecycle or creating a new protocol.
- `agent_message_job_create` / `agent_message_job_update`: old backlog and schedule envelope lacks canonical Creator/Trigger/source admission and immutable CAS fields.
- `scheduler_create`, `scheduler_update`, `scheduler_delete`, `scheduler_pause`, `scheduler_resume`, `scheduler_run_now`: old cursor mutation lacks canonical Creator/Trigger/Job references and Event identity. `scheduler_list/get/runs` remain read-only.

`legacy_mcp_compatibility` is deliberately pure. Unknown tool names and non-object argument shapes fail closed. It does not infer caller identity from payload and does not translate arbitrary old parameters into a generic dispatcher. The existing canonical MCP client and authorization bridge remain authoritative for identity, permissions, CAS, frozen Runs and unknown/no-replay behavior.

## Immutable verification

Final command used `D:/project/Pan-main/.venv/Scripts/python.exe` through `scripts/run_tests_isolated.py`, bound to `f7116a89`:

`tests/test_retirement_rev12_mcp.py tests/test_authorization_mcp.py`

Result: **16 passed, 0 failed, 0 errors, 0 skips, exit 0, 7.692 seconds**. Tests cover every removed tool disposition, no transport fallback, malformed/unknown inputs, canonical tool discovery, retained read/cancel discovery, and existing MCP authorization/CAS/identity behavior. Evidence and source hashes are in [parity-matrix.json](../../evidence/job-retirement-rev12-20261029/parity-matrix.json).

No generic dispatch, legacy writer/registry, Worker, FinalRuntime, scheduler, contracts, product publication, frontend, shared fixture, real data/profile, protected ports, main/practical, push, CI, deployment or WSL operation occurred. The no-Creator Scheduler Template conflict from revision11, frontend form caller wiring, monitoring/stable-reset, unknown fallback/clear-target and real migration remain open.
