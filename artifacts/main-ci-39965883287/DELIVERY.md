# Intermediate CI 39965883287 and legal metadata read follow-up

Remote source: `818f6b849d31cac98f03766ad1a6eb7788f5ea58`, attempt 1.
Run conclusion: failure. Complete job logs, run archive, both JUnit artifacts and identity manifest are retained here; collection-errors.json is empty.

| Python | Job | Passed | Failed | Skipped |
|---|---|---|---|---|
| 3.12 | 120455519175 | 5474 | 1 | 61 |
| 3.14 | 120455518783 | 5475 | 0 | 61 |

Both legs have 5536 cases and identical identity multisets. Skip multisets match the original baseline. All original 14 failure identities passed on both legs. Settings PUT500 and Locker exit1 did not recur here; those intermittent failures remain unlocated, not proven fixed.

The sole failure is tests.test_legal_offline_http::test_external_running_kill_snapshot_manual_changes_restart_and_successor_idle. Its durable() helper directly opens the live Session JSON and received PermissionError errno13. This bypassed the existing bounded Windows metadata open helper. On base 838ada46bca4fc20d77c60e5e7542c1a0bed7c43 an injected identical error representation fails deterministically (red: 1 failed, 2 passed). The fix reuses _read_session_metadata; it changes no scenario assertion, ID, ownership, lock, generation, timeout or skip. Permanent access failure still raises after the existing bound, and JSON parsing is not retried.

Validation Job job_af73f0342fe828cfc00c2b70 completed exit0: 29 passed, 0 failures/errors/skips, 160.391s. It covers the metadata tests, legal-offline file and Illegal queue wake file. legal-read-green.xml and validation-job.log.zip retain the result. The injected regression proves the helper gap, not that transient sharing is the only possible explanation for the remote PermissionError.

This follow-up is based on 838ada46, but does not alter or imply the gate result of its separate remote run 39970102909. That collector remains job_b39a4be02abe1f8101cf42ea. No push, rerun, dispatch, main/practical movement, protected service, real account/provider or QQ operations occurred.
