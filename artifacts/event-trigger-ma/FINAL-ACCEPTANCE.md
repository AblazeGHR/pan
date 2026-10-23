# MA final isolation acceptance — 2026-10-22

Accepted integration source: 7b66a3b0e9a5115064f3350d0a049af709465ea0.
Encoding repair candidate: b694ea53bb23651770ad61debf3184e3c5a32274 (full authoritative candidate SHA is recorded in merge ancestry; implementation 1126367188836155479614952cfe5f904354d208).

MA independently reran tests/test_eta_final_combined.py and tests/test_eta_ledger_growth.py with canonical Python through scripts/run_tests_isolated.py, default Windows encoding, no UTF-8 environment override: 2 passed, 0 failed/skipped, exit 0, 12.21 seconds. See final-review-pass/ma-final.xml. One existing Pydantic deprecation warning remains.

The real isolated Node event / query frozen targets / Pause queue gate / production Chromium / delayed-restart stdio chain passed. Recovery is a persisted fixture restart experiment, not an OS power-loss test. combined-http.json records ownedProcessesRemaining=[] and portReleased=true. Both browser logs round-trip a character GBK cannot encode. Previous MA encoding failure evidence is retained under final-review-failed; it is not counted as passing evidence.

Source review confirms explicit UTF-8 on Python text I/O and Git/browser subprocess decoding, and Node JSON output. Business assertions remain. Independent earlier stage evidence is retained separately: query backend 55 passed, query UI 79 passed, plugin lifecycle 36 passed. Counts overlap other suites and are not summed into a grand total. TA final 25-case cross-line suite is separate from this MA two-case execution.

Disposition: accept the implemented v0.1 scope in the isolated integration branch. No main/practical advancement, push, release or existing service restart. Both main/practical remain 4ae711249af41a555a7a088e8fdfabcbafc75f43. The core TA tree was restored to its committed clean state after copying MA evidence.

Human acceptance remains pending. DESIGN-MATRIX.md distinguishes delivered functionality from future extensions: parallel execution, reliable replay/outbox/automatic reconciliation, all Pan API adapters, and ledger retention/pagination/indexing are not implemented. Single-file ledger growth, process-local coordination and untested real-provider/POSIX/power-loss paths remain explicit limits.

