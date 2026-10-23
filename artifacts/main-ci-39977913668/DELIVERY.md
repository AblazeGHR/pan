# Inbox schema transaction follow-up

Remote source dbf33c96a61911966ff772093d8069d4d613ce2e, run39977913668 attempt1, concluded failure. Logs, metadata, original collection errors, recovery provenance and complete JUnit artifact ZIPs are archived here.

| Python | Job | Conclusion | Total | Passed | Errors | Skipped |
|---|---|---|---|---|---|---|
| 3.12 | 120497266450 | success | 5555 | 5494 | 0 | 61 |
| 3.14 | 120497266016 | failure | 5555 | 5492 | 2 | 61 |

Both test identity multisets match. Both skip multisets match the original baseline, with no added/removed skip identities. All original14 identities passed in both legs. Python3.14 errors were tests.test_legal_offline_browser::test_browser_illegal_dot_recovers_on_successor_idle_without_reload and test_browser_illegal_lock_icon_and_manual_unlock. Both failed isolated server setup at the unchanged20-second readiness deadline; their15-second stack dumps show the main thread inside Inbox.__init__ schema executescript, called by inbox_service.recover in server lifespan.

Collector job_c151794f29ab350a9c28971d exited1 because the per-job3.12 log download exhausted three bounded TLS handshake attempts. The complete run ZIP succeeded and contains both legs' full combined and step logs. The3.12 log was recovered from run-logs.zip:0_pytest (3.12).txt. collection-errors.json retains the original failure; collection-resolution.json records the successful offline recovery. No remote rerun or duplicate collector was needed. gate.json was then reconciled from the complete metadata and both original JUnits; collector failure is not confused with test failure.

## Fix and deterministic evidence

Base source5273145c13bea729ea4db90d51f1313ddb36dd89. Inbox.db starts a transaction, but sqlite3.Connection.executescript commits that pending transaction before executing its script. The previous schema script had no explicit BEGIN, so each CREATE TABLE used a separate autocommit. This causes unnecessary durable schema commits and leaves partial tables if a later statement fails. The fix adds BEGIN IMMEDIATE inside the existing script. All schema statements now share the db context's existing final commit/rollback. No table/column/index/data format, synchronous durability setting, queue identity or forwarding logic changed.

Two new regression cases use SQLite's real authorizer to observe transaction state at each CREATE TABLE and inject a denied routes-table creation. Before the fix both fail (2 failed,0 skipped, exit1): DDL is outside the transaction and earlier tables remain after failure. After the fix all DDL is inside a transaction and failure rolls back the entire schema. This proves the transaction defect and its repair; the remote stack does not prove separate DDL commits were the unique cause of its entire startup delay. No startup timeout was increased.

A third regression constructs a retained older partial schema, initializes it twice, verifies missing tables are added while old Locker source identity/disabled status and unknown route state/original Session/Queue IDs remain unchanged, and verifies no queue put is called. Existing legacy migration/recovery/non-replay tests remain unchanged.

## Validation and boundaries

- Canonical Python D:/project/Pan-main/.venv/Scripts/python.exe; tests/test_agent_inbox.py tests/test_job_inbox_boundaries.py tests/test_job_inbox_migration_drill.py:47 passed,0 skipped, exit0, green-schema.xml/exit. Covers synthetic old data compatibility, repeat initialization, rollback, durable Queue acceptance and unknown non-replay. This is not a new run of the separately MA-accepted real practical data-copy gate.
- scripts/run_tests_isolated.py -- python -m pytest tests/test_legal_offline_browser.py tests/test_legal_offline_http.py tests/test_agent_inbox_http.py:23 passed,0 skipped, exit0, green-http-schema.xml. Durable Job job_e6225c96f0cecbd2decf6eb8. The exact two remote error identities pass alongside shared HTTP fixture, own process-kill/exit matrix, Pause/Illegal/queue identity and Inbox HTTP/MCP/fakeCLI contracts. No assertion, timeout or exit-code requirement changed.
- owned-http-evidence.zip retains temporary test data, server logs, startup stacks, browser images and Inbox lifecycle records. The Inbox server exited0, listener on random port52412 closed, and a post-run read-only scan found no process whose command referenced the owned pytest-4034 root. See owned-cleanup.json. Other fixture shutdown assertions also passed as part of the23-test JUnit.
- Existing remote61 skips unchanged; no new skip/xfail. Historical settings500/Locker exit1 and the earlier10-record trace remain intermittent or without a uniquely proven root cause; absence here does not close them.

The exact5273145c main run39983726899 remains under its existing unique collector job_c4c8e02e6c90353e9acdf9b9. This local follow-up does not replace that remote gate and itself needs MA review and a later exact-source gate. No TA push/dispatch/rerun, main/practical movement, protected-port/service operation, shared dependency mutation, actual account/provider/QQ operation occurred.
