# Event backlog admission follow-up

Remote source: 66b26465ec83123eff3df91ad588a1d9b9a6768f. Run39976748349, attempt1, concluded failure. Complete per-job logs, run logs, JUnit artifact ZIPs, metadata and identity manifest are archived here. collection-errors.json is empty.

| Python | Job | Total | Passed | Failed | Skipped |
|---|---|---|---|---|---|
| 3.12 | 120493205085 | 5555 | 5493 | 1 | 61 |
| 3.14 | 120493205285 | 5555 | 5493 | 1 | 61 |

Both legs failed only tests.test_event_store::test_pending_old_plugin_events_do_not_starve_independent_event (TimeoutError at the existing three-second deadline). Test identity multisets match; both skip multisets equal the original baseline, with no added/removed identities. All original14 failures passed in both legs. This result is for66b26465 only, not the newer frozen dbf33c96 source.

The dispatcher invokes strict Engine.match for each retained fact. An unavailable dependency leaves no Run, but Ledger.release_event previously still rewrote/fsynced the whole ledger and increased its revision. A page of65 unavailable facts therefore caused65 empty admission commits before the independent fact. On base dbf33c96a61911966ff772093d8069d4d613ce2e, a deterministic test adds60ms latency to actual ledger atomic commits only, after backlog setup. The old implementation fails the unchanged three-second deadline. This demonstrates a concrete avoidable latency source; the remote stack alone does not prove it was the sole source of remote elapsed time.

The production fix checks for any matching Run while holding the existing reentrant ledger lock. If none exists it returns without writing. Existing Runs still use the original mutation, admission and revision increments under the same lock. No cached availability/revisions, skipped Event delivery, changed ordering, durability relaxation or Action replay is introduced. Unknown/uncertain, owner/generation/lease, Queue IDs, Pause and Locker semantics are untouched.

The existing backlog test now covers normal and delayed commits with the same business assertions and three-second deadline. An additional test asserts empty release leaves file bytes unchanged and real release increments both revisions, admits the Run and permits its original claim.

Validation on canonical Python D:/project/Pan-main/.venv/Scripts/python.exe:

- Red: reproduce_before.py restores only the original method in process memory, then runs the new focused tests. 3 tests:2 failed/1 passed,0 skips, exit1. JUnit and stdout are in job-and-validation-logs.zip; red-backlog.exit is1. No source checkout was reverted for this experiment.
- Green: pytest tests/test_event_store.py tests/test_automation_core.py tests/test_automation_retry_parallel.py:87 passed,0 skipped, exit0. Includes partial-persist admission fence, cancellation/restart, uncertain non-replay and parallel retry contracts. See green-backlog.xml/exit.
- HTTP: scripts/run_tests_isolated.py -- python -m pytest tests/test_event_store_http.py:2 passed,0 skipped, durable Job job_8a6f680622edb089033c36cd exit0. Random port65328, temporary owned profile/fakeCLI. Both owned lifecycle evidence records report no remaining owned processes and portReleased=true. Includes durable Node Event delivery, explicit replay dedup, restart and uncertain replay rejection. See green-http.xml and owned-http-evidence.zip.
- An initial direct HTTP invocation failed the protected-port guard before starting a server; the successful run above uses the repository isolation wrapper. A preliminary command also named a nonexistent test file and returned exit4; neither is counted as validation success. The protected-port guard stdout is retained as green-http.log in the log ZIP; the preliminary exit4 output was superseded by the final green-backlog.log. No protected service was operated.

No assertion or timeout was weakened, and no skip/xfail was added. Existing61 remote skip identities are retained in gate.json. Old settings500/Locker exit1 remain intermittent and unlocated; the earlier10-row trace failure is still not proven to have one unique cause. Latest dbf33c96 run39977913668 remains assigned to its existing unique collector job_c151794f29ab350a9c28971d; no result is inferred here. Candidate awaits MA review and subsequent exact-source remote gate. No TA push/dispatch/rerun, main/practical move, shared dependency change, real provider/account/QQ or service operation.
