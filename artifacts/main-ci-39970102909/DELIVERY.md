# Locker CI follow-up on source 6e1d66ae

Remote evidence: run 39970102909, exact SHA 838ada46bca4fc20d77c60e5e7542c1a0bed7c43, attempt1. Both Python3.12/job120470207961 and Python3.14/job120470208054 have 5554 tests: 5488 passed, 5 failed, 61 skipped. Consult jobs.json for authoritative job IDs. Collection errors are empty; both identity multisets and original skip multisets match.

All five failures occur in both legs:

- tests.test_eta_report_integration_http::test_trusted_locker_and_error_report_share_product_runtime
- tests.test_locker_independent_http::test_owned_notification_consumption_and_restart
- tests.test_locker_runtime_http::test_product_locker_login_choice_pause_restart
- tests.test_user_locker_browser::test_locker_browser_http_mcp
- tests.test_session_evidence_read::test_session_evidence_read_preserves_other_permission_errors[None]

The first four use browser probes written for the former Locker UI: Choice select, old status text, always-visible trusted details, implicit browser authentication and single-step rejection. Current production UI uses radio choices, explicit local-key auth (in memory and cleared on reload), selected trusted binding details, read-only completed responses and confirmed rejection. Updated E2E follows those real interactions and preserves target/action digest, one trusted execution, response receipt, original queue IDs, no replay, layout and actual fakeCLI consumption assertions. Explicit rejection reasons retain the existing two-notice test scenarios under the accepted no-reason/no-notification contract. Test-only synthetic recipient auth accepts the real owned-profile UserAuth session and retains the test-user mapping; unauthorized requests still fail. Product runtime/UI code is unchanged.

The fifth is a stale fixture expectation after the accepted errno-only EACCES bounded retry. EACCES/None now joins the transient-open parameter matrix; unrelated winerror123 still asserts one read and unchanged exception. Existing persistent-lock, invalid-JSON and other-platform failures remain checked.

Local initial repair: production build succeeded; 14 passed, 2 failed. The two failures exposed missing explicit rejection reason and missing cold-page login in the probe. That red JUnit is retained in job-logs-and-red.zip. Final targeted Job job_6d6a9fbb82c90d4c179034be exit0: 16 passed, 0 failures/errors/skips, 54.953 seconds. All five remote identities and shared HTTP/MCP/read regressions are included. Actual browser output is archived in owned-browser-evidence.zip; JUnit is repair-green.xml. Build/validation logs are in job-logs-and-red.zip.

Base source: 6e1d66aeb806a0f4665d5faf3e0e653bdde4cb9b. Its separate run39971712612/collector job_3626e05719b93c824cc5f704 remains authoritative for that SHA, not inferred green from local evidence. Original14: Locker-runtime and user-Locker failed in both old legs; remaining12 passed. Settings500/Locker exit1 from c9c9fe92 did not recur as those failure modes here and remain intermittent/unlocated. No new skips, relaxed timeouts, removed business assertions, queue IDs or owner/generation/lease/unknown changes.

No push/dispatch/rerun/main/practical movement or real service/provider/account/QQ operations occurred. Candidate is for MA acceptance; final exact-SHA remote gate remains pending.
