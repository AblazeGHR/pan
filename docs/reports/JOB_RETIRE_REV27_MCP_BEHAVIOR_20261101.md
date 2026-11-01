# JOB-RETIRE revision27 — MCP stdio compatibility and retained behavior evidence

This continuous candidate is on `r6/job-entry-notification-closure-20261029`, source/test baseline `fee7ffa0c3439668597ab3c58f3e218751bb9b8f`. Migration-readiness `73f14dd48e09ca25bd7236678711640a009833f6` was not merged. No product, migration, main/practical, service, real-data or remote-CI change was made here.

## Actual MCP caller boundary

`tests/test_retirement_rev16_mcp_stdio.py::test_retired_names_actual_stdio_boundary_and_authorized_canonical_path` launches `python -m packages.mcp.server` as an owned subprocess and performs real JSON-RPC `initialize`, `notifications/initialized`, `tools/list` and `tools/call`. It checks canonical `job_create`, `job_run`, `job_operation_find` and retained read/cancel tools. Retired write calls return structured `capability_unsupported` without IPC, HTTP, operation, queue, effect or Worker activity. Malformed arguments fail closed. A canonical `job_create` follows the existing authorized IPC bridge and its original operation identity is found by request key.

Rev12 pure compatibility checks also passed: retired mapping does not call `_api`, malformed shapes return `invalid_schema`, and retained tools remain discoverable. No generic dispatcher or legacy writer was restored.

## Retained behavior successors

The same focused command passed exact successors for schedule replacement, Creator disable/retire, explicit Run cancellation, missing notification target/unknown receipt, partial fanout terminal notice and cold no-replay. These use owned canonical service/queue/receipt fixtures and do not claim real profiles, suppliers or deployment.

| Gate | Result | Exit |
| --- | ---: | ---: |
| Actual rev16 MCP stdio + rev12 compatibility + rev16 behavior | 14 passed / 0 failed / 0 error / 0 skip | 0 |
| Historical `scheduler_surface_retirement.py` red preservation | 9 passed / 13 failed / 0 error / 0 skip | 1 |

The 13 red assertions are preserved in [legacy-surface-red.xml](../../evidence/job-retirement-rev27-20261101/legacy-surface-red.xml). They expect `Unknown tool` for retired calls, while approved rev16 behavior handles registered retired names at the actual call boundary and returns structured `capability_unsupported`; these assertions were not weakened or deleted.

## Matrix and boundaries

The versioned matrix in `evidence/job-retirement-rev27-20261101/current43-matrix.json` carries all previous 43-node assertions and records the new exact MCP/behavior successors. It does not mark the whole migration complete. Original reds, unknown/partial no-replay rules, scheduler host retention and writer/owner fences remain unchanged.

Unverified layers remain real profile/51/60 migration, production deployment, remote/other-platform CI, real QQ gateway traffic and live user acceptance. The separate migration-readiness candidate is an MA input, not evidence from this worktree.
