# 9bdd05e9 Python 3.14 gate and targeted repair

- Exact remote source: `9bdd05e96cfcc7682a03fb3bc13b2bf0fd909519`.
- Run: https://github.com/AblazeGHR/pan/actions/runs/40083487423
- Python 3.14 job `120841620483`: completed/failure, pytest exit 1. Collector `job_5093e5752c9a4d6c8972319a`: completed/0 (successful collection, NOT successful CI).
- JUnit: **5605 passed, 14 failed, 61 skipped, 0 errors** (5680 total). Complete job log, annotations, artifact ZIP and JUnit retained. Collection errors: none.
- Skip identity + reason multiset exactly equals the 61-entry accepted 03158 baseline; no added skip/xfail.
- Original 0156 failure inventory: 11 pass remotely, 3 recur (Locker runtime, MCP handbook, Locker browser). All three are now covered by local repair validation; remote results are not rewritten as green. Full mapping: `original14-3.14.json`.
- Python 3.12 metadata also says completed/failure. Logs/JUnit not collected; current practical rule only requires 3.14. No 3.12 success claim.

## All 14 remote failure destinations

| Identity (full names/parameters in failures-3.14.json) | Count | Evidence and narrow change |
| --- | ---: | --- |
| test_action_persistence::test_api_distinguishes_storage_and_user_authorization | 1 | Fixture lacked initialized auth principal, so both calls stopped at 503 before Store.put. Supply explicit test-only user principal/Bearer; keep storage503 vs authorization401 assertions. |
| test_job_inbox_boundaries::test_disabled_agent_template_cannot_smuggle_enabled_instance | 1 | Passive Templates reject top-level enabled before nested-config authorization. Remove obsolete top-level fixture field; keep enabled config and PermissionError/no-write checks. |
| test_job_window_policy::test_legacy_runner_uses_policy_for_argv_shell_and_old_records | 6 | Window-option unit mocks process but not newly added registry spawn admission. Stub only runner_spawn to invoke the captured factory; preserve Job ID, all six parameters, window options and process-context assertions. No production identity/owner changes. |
| test_locker_independent_http::test_owned_notification_consumption_and_restart | 1 | Old uncredentialed Action setup rejected with401. Use existing owned-profile login helper, preserving explicit-header/raw-http denial tests. |
| test_locker_runtime_http::test_product_locker_login_choice_pause_restart | 1 | Same owned-user fixture correction; queue/pause/binding/restart assertions unchanged. |
| test_mcp_handbook.TestDescriptionCallChain::test_every_tool_ends_with_pan_skill_pointer | 1 | Product documentation omission on locker_message and authorization_objects. Restore required skill pointer; keep AST inventory assertion. |
| test_session_query::test_legacy_and_draft_authority_gates | 1 | ASGI unit had no lifespan/auth service, causing503 before template endpoint. Add explicit unit user auth state and Bearer; retain disabled draft/query validation assertions. |
| test_system_fields_http::test_worker_error_persists_system_session_and_pauses_via_mapping | 1 | Same owned-profile login helper; original Event.system/target/Pause assertions preserved. |
| test_user_locker_browser::test_locker_browser_http_mcp | 1 | Product regression: submitting a Card persisted response but left is_read=0, unlike rejection. Set is_read=1 in the SAME response transaction; idempotent retry, receipt, fixed choice binding, revision and outbox logic unchanged. Browser's original read=true assertion preserved; unit regression checks unread before submit/read after submit. Reading alone still does not mark read. |

## Local validation (candidate includes exact 9bdd source)

- Deterministic pre-repair short tests: 10/10 failed with the matching remote errors (prior execution console); canonical red JUnit and tracebacks are the complete remote artifact here.
- `local-targeted.xml` / `local-targeted.log`: 74 passed, 0 skipped/errors/failures, exit0. Scope: complete affected persistence/boundary/window-policy/handbook/Card unit files + exact session-query authority test. Includes all ten corresponding remote identities and response read-state regression.
- `local-port-guard-red.log`: first four-test Job `job_6e6dc87ab0d99abb684efb9d` exited1: three fixtures refused default8767 before server startup; browser passed. Protected-port assertions worked. No protected service operation occurred.
- `local-http-browser-green.log`: existing Job `job_647f45095a8f47c90cb25730` completed0, four tests passed with `PAN_TEST_HTTP_PORT=52013` (browser fixture separately chose51495). Original owned process/listener/cleanup assertions passed. No green JUnit requested in this earlier Job; plain pytest log is retained, not relabeled as JUnit.
- Browser shutdown evidence: graceful endpoint, returncode0, forcedTerminate=false, serverExited=true, listenerClosed=true. See archived cleanup/shutdown JSON.
- No assertions removed, no timeout widened, no skip/xfail added. No unrelated full suite or repeated load tests.

## Gate boundary and handoff

Remote 9bdd practical gate remains BLOCKED by its 14 failures. The local candidate addresses all fourteen; MA must review/accept and decide the next frozen SHA/push. This TA did not push/rerun/dispatch, move main/practical, change accounts, or operate real profiles/providers/QQ/services. Existing independent /jobs startup cause remains unlocated; absence in this run is not proof of repair. Historical settings500/Lockerexit1 unique-root-cause limits remain unchanged. Latest auth root-type fix is inherited from 9bdd; no private data-copy gate was rerun or claimed.
