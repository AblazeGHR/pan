# Exact-source intermediate CI and Action trace follow-up

Remote source 6e1d66aeb806a0f4665d5faf3e0e653bdde4cb9b, run39971712612 attempt1 concluded failure. Complete logs, JUnit artifacts, metadata and identity manifest are archived here; collection-errors.json is empty.

| Python | Job | Total | Passed | Failed | Skipped |
|---|---|---|---|---|---|
| 3.12 | 120475817860 | 5555 | 5489 | 5 | 61 |
| 3.14 | 120475817674 | 5555 | 5488 | 6 | 61 |

Both test identity multisets match, and both skip multisets match the original baseline. Five failures are shared: ETA report integration, Locker independent, Locker runtime, user-Locker browser, and errno-only EACCES evidence-read expectation. These are addressed by accepted candidate66b26465; its separate remote run39976748349/collector job_16f371b4a273dcbe6ede22f2 remains pending and is not inferred green.

Python3.14 additionally failed tests.test_automation_retry_http::test_node_parallel_finite_retries_and_uncertain_confirmation_http: three successful runs but only 10 trace rows instead of12. Original14: Locker-runtime/user-Locker failed in both legs; automation-retry also failed in3.14; all remaining original identities passed. Old settings500/Locker process-exit1 did not recur as those failure modes and remain intermittent/unlocated.

The Action fixture wrote concurrently to one JSONL via separate O_APPEND descriptors. The follow-up removes shared append contention: each event writes a distinct temporary file and atomically publishes a distinct JSON record. Random event suffixes retain duplicate executions as additional rows instead of overwriting evidence. The reader collects complete published files, orders them by recorded timestamps, and preserves all prior business assertions, including the exact12 trace count, two attempts per successful run, no old-event resend, authenticated uncertain confirmation and original attempt identities. No production runtime or scheduler change.

On base66b26465 the changed fixture passed actual Node/HTTP/command integration and parallel retry contracts: 51 passed, zero failures/errors/skips, exit0. The final unique-event-name form was revalidated: 51 passed, exit0; final JUnit is trace-green.xml. The owned trace contains12 records for the first three keys and five for the fourth interrupted/confirmed key. Both validation JUnits, Job log and final owned evidence are retained. This validates the new observation channel but does not deterministically establish shared append loss as the sole cause of the old remote10-row result. The archived remote JUnit is the red evidence; no synthetic green result replaces it.

No timeout/assertion relaxation, new skip/xfail, queue ID, owner/generation/lease/unknown change, push/dispatch/rerun, main/practical movement, service or real provider/account/QQ operation occurred. This candidate is for MA review; final remote gate remains pending.
