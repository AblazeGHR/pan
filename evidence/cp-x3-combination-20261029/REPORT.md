# CP-X3 combined lifecycle / capability validation

## Source and integration

Original task branch `xplat/worker-capability-20261027` was clean at `99e51f1f6ad2115328459341bf38f9300d0e02a8`.
It fast-forwarded to accepted CP-X1 `ac28b89bfa628af4c8c32fdfe63938f1ca1e38e9`.
Ancestry review found CP-X2 `2817e48a3a194204f16725abb3df28bf5e6cb582` was not included:
merge-base was `a9d236c1c16159ca27697cfe090e3b7256c8c417`.
After explicit MA authorization, normal merge (no reset, cherry-pick or file replacement) retained both histories.

Exact combined source: `e3fceb50fa1f059299b7fa179fee5cc069888c03`.
Parents: evidence checkpoint `80dd769e338777bbab008e504bd60e36435cf5e3` and exact CP-X2 `2817e48a3a194204f16725abb3df28bf5e6cb582`.
Both ac28 and 2817 are verified ancestors. Merge had no conflicts.

CP-X2 product scope relative to ac28 is six files: `packages/core/platform/desktop_win.py`, `process.py`, new `process_creation_win.py`, `packages/jobs/owned_process.py`, `tests/helpers/process_owner_hidden_probe.py`, and `tests/test_platform_process_windows_hidden.py`. CP-X3 did not independently edit any of these symbols. CP-X1 lifecycle root representation fixture and CP-X2 POSIX target-platform evidence are retained in their original histories.

## Local validation on the combined source

Windows Python 3.14.5, interpreter `D:/project/Pan-main/.venv/Scripts/python.exe`. Every Python command below uses this interpreter twice, with outer `scripts/run_tests_isolated.py --`, then inner `-m pytest`. Owned fixture roots/PIDs only; no protected ports or deployed services.

1. `tests/test_lifecycle_followthrough.py tests/test_lifecycle_controller_publication.py tests/test_platform_process_windows_hidden.py -q -ra --junitxml=evidence/cp-x3-combination-20261029/combined-controller-hidden.xml`: exit0, **21 passed, 0 skipped**. Includes all four original public canonical controller-crash/cancel parameters (before-claim/after-running, base/named-venv), both followthrough controller crash windows, and the exact original remote red `tests/test_platform_process_windows_hidden.py::test_hidden_tree_identity_stdio_and_desktop_cleanup[named-venv]`. Original PID/PPID, retained FILETIME, Job membership, desktop-gone and safety fence assertions unchanged. No platform product repair was needed by this run.
2. `tests/test_platform_api.py tests/test_worker_platform_admission.py tests/test_native_platform_projection.py tests/test_native_control_api.py tests/test_plugin_ipc_endpoint.py tests/test_job_final_http_service.py tests/test_lifecycle_fast_contract.py tests/test_lifecycle_strict_successors.py tests/test_platform_process_contract.py tests/test_job_final_executors.py -q -ra --junitxml=evidence/cp-x3-combination-20261029/combined-protocol.xml`: exit0, **157 passed / 21 existing explicit skips**. Covers canonical real-storage/API identity, authorization, cancel/control, unknown/replay fences, native capability denial before side effects, IPC and supported process ownership contracts. POSIX-only and unsupported-lifecycle-only skips are preserved; they are not passing target-platform tests.
3. In `packages/web`, `pnpm exec vitest run src/stores/platformStore.test.ts src/components/layout/viewEntries.capability.test.ts src/views/TerminalPanel.capability.test.tsx src/components/session/NewSessionModal.test.tsx --reporter=default`: exit0, **47 passed / 1 existing skipped**, four files. Explicit false vs unknown capabilities and React native entry gates remain unchanged.

Combined Python totals: **178 passed / 21 skipped / 0 failed** across 199 nodes. `results.json` contains exact collected node names, statuses, source and suite times; logs and JUnit are adjacent. `combined-proofs/` contains newly generated controller and hidden desktop proofs, including `kill-tree-named-venv.json` with the tested source SHA and original lifetime identity. Test-generated historical tracked proof files were copied here then restored, preserving prior evidence.

Premerge checks (`protocol.xml`:107pass/2skip; `controller.xml`:8pass) are retained separately, source ac28; they are not added to combined totals. Earlier interrupted combination command had no XML/exit proof and is not counted.

## Remote evidence and boundaries

CI40711485368 on source `3faef66ce064c62dd53d45b2f0286a3df880a91c` supplied real Windows/Ubuntu/macOS Python3.12 platform-fast and authenticated Ubuntu production browser passes. CP-X2's `evidence/cp-x2-remote-ci-40711485368/REPORT.md` and original immutable artifacts retain actual Linux starttime/boot_id and macOS proc_uniqueid/lifetime-v1/boot identity, Unicode log, cleanup and leader-loss unknown proofs. Those are evidence for that exact remote source, not this new combined SHA.

The original full-repository pytest failures remain preserved and open according to CP-X1 classification (`docs/reports/CP_X1_CI40711485368_TRIAGE_20261029.md`). No legacy test deletion/skip, assertion weakening, collection-only claim or Windows simulation of target OS. Local named-venv pass does not clear the original Python3.12 remote failure until the next same-source remote run. No fresh production build/browser was required or claimed by this batch; no frontend product source changed during the merge or validation. No model/account, main/practical, push, remote CI dispatch, real profile/data or deployment operation.

## Next CI proposal (MA only)

Use the final clean evidence candidate containing source e3fceb50; record the pushed non-main ref's exact HEAD. Existing `.github/workflows/test.yml` is unchanged. Run its three platform-fast jobs and authenticated Ubuntu browser with a fresh production build; retain full pytest red inventory separately instead of relabeling as all-green.

For the new Windows hidden/controller combination specifically, run the exact three-file command in item1 on Windows Python3.12 and3.14. Existing platform-fast does not include all those files, so its green status alone cannot close the named-venv/controller combination gate. Run item2 on Windows and, for supported contracts, Ubuntu/macOS with their original platform skips. The bounded node inventory is `results.json`; no new CI was started by this TA.
