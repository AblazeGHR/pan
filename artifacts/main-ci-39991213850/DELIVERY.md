# Bounded failed-event diagnostic commits

Exact remote source b28c04b2aca7dae4087fbc662d7344043c0eedf5; automatic run39991213850 attempt1 concluded failure. Collector job_f4c602747aba6fdce3a62e76 completed exit0, all downloads succeeded on the first bounded attempt, collection-errors.json=[]. Complete logs, annotations, metadata, original JUnit artifacts and identity manifest are retained here. The MA request for early failed-job inspection arrived after the collector's terminal event; this report uses complete final evidence, not a guessed early red icon.

| Python | Job | Conclusion | Total | Passed | Failed | Skipped |
|---|---|---|---|---|---|---|
| 3.12 | 120543140126 | failure | 5562 | 5500 | 1 | 61 |
| 3.14 | 120543140712 | success | 5562 | 5501 | 0 | 61 |

Sole failure: tests.test_event_store::test_pending_old_plugin_events_do_not_starve_independent_event[0.06], TimeoutError at the unchanged3-second terminal deadline. Normal-delay parameter passed. Both matrix identity multisets match; both61-skip multisets equal the original baseline, with no added/removed skip identities. All original14 identities passed in both legs. Prior cold-schema/watchdog regression cases passed. These conclusions describe b28c04b2, not the follow-up candidate's remote gate.

## Reproduction and repair

The earlier5273145c eliminated empty Run-ledger admission commits, but EventStore.delivery still committed each failed matching diagnostic separately. A page of64 old unavailable dependencies therefore caused64 SQLite updates/commits before the independent Event on the next page. The existing delay test only slowed the JSON Run-ledger commit, leaving SQLite disk latency unmodelled.

The test now also delays EventStore connections only when db.total_changes shows actual writes, after setup. At60ms per real write the old code deterministically fails the original3-second deadline:2 tests,1 failed/1 passed,0 skips,exit1. No business assertion or timeout was changed. This demonstrates the remaining per-event disk-commit bottleneck; the remote traceback does not identify every underlying disk/scheduling latency, so it is not claimed as proof of a unique remote cause.

EventStore.delivery_errors uses executemany inside one existing synchronous=FULL transaction for a dispatch page (at most64 rows). Each error row retains done=0, increments its own deliveries once, and retains the same2000-character last_error bound and original ID. Success delivered acknowledgement is unchanged and still occurs only after strict matching and Job matching. The dispatcher flushes accumulated errors before advancing to another page, also in finally on cancellation/close. Engine.io still settles the owned disk operation before cancellation propagates. Normal orderly cancellation therefore preserves completed error accounting; abrupt process death before the bounded-page flush can omit diagnostic/count updates, while the durable pending fact/Run fence and no-Action-replay semantics remain authoritative. No cross-file atomicity or exact diagnostic accounting across hard crash is newly claimed.

Regression checks confirm per-row error counts/pending status, untouched rows, truncation, all-or-nothing rollback when an SQLite trigger aborts a middle update, and cancellation after one failed match before the next completes (one diagnostic counted, second uncounted, both pending, no Action effect). Event/Queue IDs, unknown/uncertain replay gates, Pause/Illegal, owner/generation/lease, Locker choice binding and Job snapshot semantics were not changed. No schema/storage format change.

## Validation

- Canonical Python D:/project/Pan-main/.venv/Scripts/python.exe. Red JUnit/stdout and exit1 are retained in collection-and-red-logs.zip/red-event-commits.exit.
- Final code, including cancellation flush: tests/test_event_store.py tests/test_automation_core.py tests/test_automation_retry_parallel.py:89 passed,0 skipped,exit0. green-event-commits.xml/exit and final-unit-validation.zip. Covers partial persistence/admission, restart, uncertain non-replay, capacity, cancellation and parallel retry.
- Isolated HTTP Job job_265ad840a4b758c7a054035e exit0: tests/test_event_store_http.py tests/test_automation_retry_http.py,3 passed,0 skipped, green-event-http.xml. Actual loopback HTTP/Node/fakeCLI, original queue identities, Pause, replay dedup, restart, uncertain authenticated confirmation and finite retries remain asserted. This run preceded only the final finally/cancellation-flush refinement; that refinement was subsequently covered by the final89-test suite. No unrelated repeat full load was run.
- owned-http-evidence.zip retains owned generated server logs and evidence; all three lifecycle records (core running, durable Event Store, retry) report port60169 released and no remaining owned processes. Generated tracked baseline artifacts were restored after archiving their new evidence. No protected port was used.

No new skip/xfail, deleted/weakened assertion or timeout increase. Historical settings500/Lockerexit1, trace10-row and Inbox startup issues remain subject to their recorded non-unique/unlocated boundaries; absence in this run does not prove unique root cause. Existing initialization compatibility evidence remains valid as prior tested evidence; the private practical copy gate was not rerun and no practical tree/profile was accessed. Candidate needs MA review and subsequent exact-source dual-version CI. No TA push/dispatch/rerun/release, main/practical movement, service operation, shared dependency write, actual provider/account/QQ or real-profile access.
