# CP-X3 remote IPC startup repair

Baseline: `036fe85f23b689195207d6794db0a70d78d87fc2`, clean fast-forward from original candidate `c8c8c4eb340a9db1285596e161f6a7f54bb032bc`.
Remote red source: run 40637381532, code parent `5adbf3662b8e06e8eae13bf4aa5320a336bfa7a7`.
Full original inventory remains at `D:/project/Pan/.workflow/job-unification-review-20261028/remote-ci-40637381532/failure-inventory.json`.
`remote-ipc-failures.json` preserves the three full matching tracebacks: Ubuntu/macOS quota process restart and Ubuntu history browser, all AF_UNIX path-too-long at Bridge Listener construction.

## Change

`packages/core/platform/plugin_ipc.py` allocates unpredictable private 0700 temp directories, bounds encoded socket paths below macOS's 104-byte sockaddr_un budget, and falls back from long TMPDIR to /tmp. No TCP transport or authentication change. Exact endpoint still flows through Bridge.address and PAN_PLUGIN_IPC. Windows named-pipe construction is unchanged.

Cleanup checks original directory device/inode and only removes the empty owned directory; never recursive deletion or caller-directory deletion. Listener/thread initialization failure unwinds owned resources. Runtime start failure restores the previous environment and closes the bridge; Bridge creation failure closes Runtime. Caller data survives concurrent endpoints and cleanup. No process-owner/execution-host/server/Worker/UI product edits.

## Local commands and results

All Python commands use `D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest ... -q --junitxml=...`.

- Initial endpoint run: `tests/test_plugin_ipc_endpoint.py`; exit 0, 1 passed/2 explicit POSIX skips (`red.xml`). This Windows run is NOT local red proof; original red proof is the three real remote failures above.
- IPC/regression run: `tests/test_plugin_ipc_endpoint.py tests/test_plugin_platform.py tests/test_plugin_event_lifecycle.py tests/test_authorization_mcp.py tests/test_job_final_clients.py tests/test_quota_native_admission.py`; exit 0, 116 passed/2 explicit POSIX skips (`green.xml`, `green.log`). Includes unchanged real quota production-process restart fixture on Windows.
- Expanded endpoint run: 4 passed/1 explicit POSIX skip (`endpoint.xml`).
- Final endpoint/Worker/native admission: `tests/test_plugin_ipc_endpoint.py tests/test_worker_platform_admission.py tests/test_native_control_api.py`; corrected fixture exit 0, 39 passed (`final-corrected.xml`). `final.xml/log` preserves a Windows policy-test fixture error (long pytest root used as short fallback), fixed by using the original host temporary root. Assertions unchanged.
- Real history fixture: `tests/test_history_terminal_http.py`; exit 1, 1 failed (`history.xml`, `history.log`). Full evidence retained; no assertion changes.

## History evidence and bundle prerequisite

Exact fresh evidence: `history.xml`, `history.log`, `history-runtime.json`, `history-browser.json`, `history-browser.log` in this directory.
Source was baseline036 plus this IPC patch. Own worktree served `/react` through its `packages/web/dist`; no fresh build was run after the fast-forward. Both dist index and `index-fzuSG3Wu-pc2.js` date to 2026-10-27 20:04:29, from the earlier CP-X3 build before candidate c8c8c4eb. They do not prove source036 behavior. Asset hashes are in `dist-provenance.json`; node_modules junction provenance does not establish dist freshness.

Failure is `history-terminal-live.mjs:90` first assertDisplay after late duplicate error, with helper assertion at line75: `[data-terminal-status="error"]` expected1, received0. Existing durable error/done and held-frame assertions had passed. Captured actual WS frames include terminal:seq:1 error and terminal:seq:2 done. Runtime shows authenticated production bundle, fake CLI and portReleased=true. Classify DOM failure as unmet fresh-build fixture prerequisite, not a confirmed source036 UI regression. No UI rerun or UI changes after MA provenance request. History gate remains open with MA.

## Acceptance boundaries

Windows named-pipe roundtrip/concurrency/start-failure cleanup and existing IPC authorization checks pass. Unix private path allocation is policy-tested on Windows, including mocked short fallback and bind-failure unwind; this is not Unix transport evidence. Real Unix long-path roundtrip and mode checks remain target-platform tests. No fresh Ubuntu/macOS success claimed until MA's next remote run. No push/remote dispatch/main/practical/deployment/account/real data operations. Existing assertions and skips preserved; owned HTTP fixtures cleaned their own processes/ports.

## Fresh production prerequisite and original browser rerun

MA subsequently authorized validation-only fresh production build and original history fixture rerun. Source SHA `cf7510f5152655992e376092d946962812950137` (baseline036 plus IPC candidate), own worktree serving /react. `pnpm build` in packages/web exited0; 88 compressed assets verified. Fresh entry `index-JbINMieB-pc2.js`; exact times and SHA256 in `fresh-dist-provenance.json`, complete build output in `fresh-build.log`.

Original unchanged `tests/test_history_terminal_http.py` via the isolated Python runner then exited0, 1 passed (`fresh-history.xml`, `fresh-history.log`). Fresh runtime/browser/WS evidence is in `fresh-history-runtime.json`, `fresh-history-browser.json` and `fresh-history-browser.log`. This supersedes the stale-dist run for current local browser acceptance; stale evidence above remains intact. No UI, server, Worker or browser assertion edits. Linux/macOS post-fix transport/HTTP/browser evidence remains pending MA's remote rerun.
