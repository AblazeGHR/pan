# P6 system background-process and main-lifecycle compatibility slice

Baseline: `b9d8dc3b3db26ab70b5d487db96762981f34f571`.

The retained `/api/background-jobs` reader previously consulted only the legacy registry. This left canonical process Jobs, their Run/Attempt/effect history and process-owner identity invisible through the old GET/cancel shape. The gap is recorded in `process-compat-red.txt`.

This slice adds a thin FinalService projection/control adapter:

- canonical process Jobs can be projected as the old `background-process` shape with the original Job ID, argv/cwd/hideWindow, status, Run history and notification marker;
- old cancellation delegates to the existing canonical Run cancel operation under the same owner fence and request receipt; it does not kill by PID alone or create a legacy row;
- complex Jobs and non-process Actions fail closed as `capability_unsupported`; unknown/invalid identity does not become success;
- the existing independent process executor remains authoritative for process identity, logs, cleanup proof, unknown receipts and no-replay;
- the existing fixed `system_main_lifecycle` supervisor path is kept intact and covered by the focused canonical preparation/strict successor checks; no second lifecycle engine was added.

Evidence:

- `process-compat-final.xml`: 9 focused tests passed, including canonical old-view/cancel/history, stale owner fence, unknown/partial no-replay and supervisor recovery.
- `main-lifecycle-final.xml`: 21 canonical preparation/strict successor tests passed.
- `process-mcp-stdio-final.xml`: 2 real stdio MCP runs passed through the owned canonical bridge; no user Bearer was injected into MCP.

The compatibility adapter is exercised against the isolated FinalService fixture; existing canonical process-log HTTP/cold-reopen evidence remains the source of production log identity proof. The real stdio MCP run covers the owned transport boundary. No real profile or protected port was used in this slice.

Unverified layers remain: full legacy background-process writer creation transfer, real model CLI/QQ, Linux/macOS/remote CI, production build/Chromium, deployment, real data migration and human acceptance. Complete old Scheduler/Trigger compatibility is outside this slice.

No push, `main`/`practical` advance, real data/profile access, 8767/8768 operation or deployment occurred.
