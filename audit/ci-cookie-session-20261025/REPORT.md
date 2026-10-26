# CI 40211009134 Cookie session regression repair

**Outcome:** all 14 original failing test identities pass in both complete Windows runs. The complete local matrix remains red: Python 3.12 has 6,055 passes / 7 failures / 77 skips; Python 3.14 has 6,054 passes / 7 failures / 78 skips. Six additional failures reproduce on the pristine baseline in this environment; the remaining frontend shutdown error is order dependent and its predecessor is unresolved. This is an original-regression repair candidate, not full-matrix green acceptance.

## Scope and source

- Baseline: `bbcb780db9d79117ce5a7a561ac2558560699935`.
- Source candidate: `67310a8f21a50fb79fb139e36bfd7a4371332157`, a direct child of the baseline.
- Branch: `fix/ci-cookie-session-20261025`.
- No push, deployment, main/practical movement, application service operation, or test use of ports 8767/8768.
- `.github/workflows/test.yml` is unchanged. Full verification uses `tests/ packages/qq`, matching the Windows jobs.
- The shared repository's main was separately observed at `34f31613`; this candidate does not include or advance that branch. It is a baseline-relative integration candidate, not a claim that current main can fast-forward to it.

## Original failures and causes

The GitHub jobs `121222027162` (3.12) and `121222027354` (3.14) each report **14 failing test identities**. Ubuntu and macOS fast jobs passed in the original run. These historical results do not establish remote results for this candidate.

| Test identity | Cause and repair |
|---|---|
| `test_eta_final_combined::test_node_query_pause_production_ui_restart_and_delay_mcp` | Settings login creates a Cookie shared across full page navigation. The old Automation login form no longer exists. Assert automatic restoration and retain the Runs/selection checks. |
| `test_event_store::test_api_bounded_pages_replay_authorization_and_dedup` | Login populates the AsyncClient Cookie jar. The supposed anonymous POST instead exercises authenticated Cookie CSRF and correctly returns 403. Use an independent anonymous client and retain exact 405. |
| `test_eventloop_history_offload::test_http_policies_stream_in_the_request_task_without_body_relay` | Product regression: decorator middleware installs Starlette BaseHTTPMiddleware, creating a task/body relay for every HTTP request. Replace with direct ASGI middleware. Test anonymous, invalid Cookie, valid Cookie and Bearer under the same request-task and exact body-chunk assertions. |
| `test_interaction_card_http::test_no_answer_http_stdio_production_browser` | Reload restores the Cookie session. Assert restored terminal state and absence of the login form; preserve stale submit/reject 409 and tombstone checks. |
| `test_job_inbox_http::test_jobs_templates_and_inbox_share_queue_without_consumption_or_permission_leak` | Uses `job-unified-live.mjs`: a redundant API login after GUI login has the shared Cookie but no Origin, so returns 403. Its missing token becomes `Bearer undefined`, hiding protected Jobs. Capture the original GUI login response and use a separate Bearer APIRequestContext. |
| `test_job_unified_http::test_unified_event_system_actions_legacy_and_production_ui` | Same redundant-login/API client cause; retain completed Job, Template and exact CAS 409 assertions. |
| `test_locker_independent_http::test_owned_notification_consumption_and_restart` | Old assertion assumes restart revokes Bearer sessions. Assert restored access, independent anonymous 401, explicit logout and revoked token 401. Retain queue/CLI exactly-once and no-replay checks. |
| `test_locker_order_scroll_live::test_locker_order_scroll_live` | Old reload login assertion conflicts with automatic Cookie restoration. Assert restored latest-50 rows, read state, no divider, exact geometry and unchanged read-write count. No timeout or geometry/assertion thresholds were relaxed. The original 3.14 job also timed out while completing Read all; the complete scroll test is included in current verification. |
| `test_locker_product_live::test_locker_messages_cards_real_service_browser_cli_restart` | A post-login APIRequestContext DELETE carries Cookie but no Origin. Supply the actual same Origin and retain successful deletion. The later restart assertion also assumed session loss; verify persistence then explicit revocation instead. |
| `test_locker_runtime_http::test_product_locker_login_choice_pause_restart` | Post-login APIRequestContext card creation needs same-origin Origin; assert HTTP 200 before using the returned card. The later restart assertion also assumed session loss; verify persistence, anonymous rejection and explicit logout/rejection. |
| `test_main_service_exit::test_exit_job_records_choice_and_exact_pre_exit_running_snapshot` | Test-order pollution, not an additional exit Job. Job persistence broadcasts through an inherited scheduler callback to a stale fake dashboard, creating `pan-ws-sender:dashboard` in the lifecycle test's task capture. Isolate WebSocket collections and loop-bound sender maps per test. Original exact task assertion remains. |
| `test_main_service_restart::test_restart_returns_scheduled_before_python_supervisor_finishes` | Same inherited fake-dashboard sender; original single restart-task assertion remains. A standalone probe reproduces both unchanged assertions with inherited fake state. |
| `test_quota_host_http::test_owned_host_configuration_read_projection_and_restart` | Restored Bearer reaches CAS validation, giving 409 rather than old expected 401. Verify restoration and exact CAS 409, independent anonymous 401, then logout and exact revoked-token 401. |
| `test_quota_policy_http::test_quota_policy_owned_http_default_unavailable_is_unknown` | Restored Bearer legitimately permits the write. Verify restoration, independent anonymous 401, then explicit logout and original exact 401. Preserve recovered policy checks and unknown provider result. |

## Middleware contract

Only a validated Cookie is promoted to an Authorization header. Anonymous and invalid Cookie requests proceed to their original endpoint/routing gates. Explicit Bearer credentials retain their independent path; an invalid Bearer cannot fall back to a valid Cookie. Cookie-authenticated POST/PUT/PATCH/DELETE still require exact same-origin Origin. Session storage errors remain explicit 503. Existing login, renewal, duration-policy propagation, logout, password-change and revocation tests remain enabled.

The new ASGI layer awaits the downstream application directly and intercepts only `http.response.start` for Cache-Control/Cookie headers. It neither consumes nor buffers request/response bodies and creates no middleware task or stream relay.

## Validation

All Python commands use `scripts/run_tests_isolated.py`, owned temporary profiles and randomly allocated nonprotected HTTP ports. Browser/MCP tests keep production React, real HTTP/Chromium/stdio and owned fake providers/CLI.

| Layer | Result / evidence |
|---|---|
| Pristine local baseline unit reproduction | 29 tests: 2 failures (event-store Cookie-jar 403/405 and stream/task relay), 27 passes. `.validation/evidence/ci-cookie-red-unit.xml` and `.log`. |
| Lifecycle assertion pollution probe | Both unchanged lifecycle assertions fail with an inherited fake dashboard and Job event callback. `.validation/evidence/ci-cookie-lifecycle-pollution.json`. |
| Focused Python 3.14 | **119 passed**, 1 warning, 219.00s; `.validation/evidence/ci-cookie-focused314.xml` and `.log`. |
| Focused Python 3.12 | **109 passed**, 30.39s (unit group plus final Locker runtime regression); `.validation/evidence/ci-cookie-focused312.xml` and `.log`. Other affected live tests also ran in the earlier diagnostic live pass. |
| Full collection | Python 3.12 and 3.14 each collect **6,139** tests, including `packages/qq`; `.validation/evidence/ci-cookie-collect312.log` and `ci-cookie-collect314.log`. |
| Frontend regression tests | **11 passed**, 3 files; `.validation/evidence/ci-cookie-frontend.log`. |
| Production build | `pnpm build` exit 0, 90 compressed assets verified; `.validation/evidence/ci-cookie-build.log`. |
| Windows full Python 3.12 | **6,055 passed, 7 failed, 77 skipped**, 6,139 total, exit 1, 2119.297s; Job `job_cbd9703ff03be8fc5f7417a9`; `.validation/evidence/ci-cookie-full312-job.json/xml/log`. All 14 original failing test identities passed. Additional failures detailed below. |
| Windows full Python 3.14 | **6,054 passed, 7 failed, 78 skipped**, 6,139 total, exit 1, 2740.886s; Job `job_7d9a08753e72c6c088975b06`; `.validation/evidence/ci-cookie-full314-job.json/xml/log`. Same seven additional failures as 3.12; all 14 original failing identities passed. |

The initial completion observer Job `job_9fc37156361815ca9f418309` failed because its previously foreground-managed pytest processes exited before producing JUnit. Their last recorded progress was 87% / 85%; 3.12 had no recorded failures, while 3.14 had two terminal failures. The exact process-termination cause was not established; these interrupted runs are **not complete matrix results**. Logs are retained as `.validation/evidence/ci-cookie-full312-interrupted.log` and `ci-cookie-full314-interrupted.log`. Merely observing preexisting processes from a Job did not transfer process ownership. The replacement Jobs above directly own the pytest descendants and persist output, exit codes and JUnit. No progress/log polling is performed while waiting for their terminal notifications.

### Additional local terminal runtime controls

The interrupted 3.14 run's two terminal failures reproduced independently under Job `job_c8bfe0db38a8dd8fa2d56b70` (2 failures, 0 skips). Terminal source and these two test files have no diff between the baseline and `67310a8f`. Four controls used the same source and exact assertions:

| Interpreter / cwd | External-kill archive test | Protocol-A screen replay test |
|---|---|---|
| venv / long nested worktree | failed: returned running after retained outer handle exit | failed: prompt/echo screen mismatch |
| venv / short worktree | failed | passed |
| owned base Python / long nested worktree | passed | failed |
| owned base Python / short worktree | passed | passed |

The final control passed 2/2 in Job `job_6ba3f4c7b9c357a99f96db67`. The two single-factor controls completed under Job `job_524f97899c657c0f8c5a93c9`; each retained one failing assertion. Evidence: `.validation/evidence/ci-cookie-terminal-runtime-controls.json` and the XML/log paths it records. The venv-only result is consistent with an outer-redirector/actual-runner identity issue, but these controls do not independently capture the complete retained-handle ancestry proof. The screen replay failure is cwd/prompt dependent. These additional terminal behaviors are **not repaired or silently treated as supported** by this Cookie CI candidate; the complete matrix uses the workflow's base-interpreter layout and a short checkout. Venv-terminal ownership and long-prompt replay remain separate product/fixture investigation boundaries.

Environment: Windows, CPython 3.12.12 / 3.14.5, Node 24.22.1; pytest 9.1.1, pytest-timeout 2.4.0, httpx 0.28.1, pyte 0.8.2 and nonebot2 2.5.0. The 3.14 owned installation has Playwright 1.63.0, FastAPI 0.162.0, Starlette 1.10.0, MCP 1.28.1 and pywinpty 3.0.5. A post-run interpreter probe found 3.12 user-site precedence: Playwright 1.62.0, FastAPI 0.141.1, Starlette 1.6.0, MCP 1.29.0 and pywinpty 3.1.0. Thus the 3.12 full run is local evidence with a dependency-layout difference from the workflow's pinned Playwright 1.63.0; it must not be described as an exact CI environment. The affected focused venv tests and full 3.14 exercise the owned dependency layout. No user-site packages were modified for this task. Neither run establishes byte-identical GitHub runner images.

The first full 3.12 invocation exposed a local dependency-layout mistake: project-local npm `.bin/codex.cmd` does not satisfy the adapter's documented global-prefix resolver. Its native-kernel failures are retained as red evidence. The owned installation was corrected to the workflow's global-prefix layout with pinned `@openai/codex@0.162.1`; a representative original failing real-kernel test then passed. No source assertions or skips were changed for this setup error.

## Existing skips

No skip/xfail or CI-scope change is introduced. Both full runs retain 43 retired oneshot skips, 19 platform-only skips, 8 explicit real CLI opt-in skips, 3 absent machine-local Kimi workdir skips, 1 missing local documentation-sync copy, 1 Windows task-registration opt-in and 2 enclosing-Job detach restrictions: 77 total. Python 3.14 adds the existing zstandard-only test skip (dependency marker `python_version < "3.14"`), giving 78. The complete exact skip messages and original-identity status maps are in `validation.json`.

## Evidence and reproducibility

The tested source commit is `67310a8f21a50fb79fb139e36bfd7a4371332157`. The final audit commit changes only this report, the sanitized `validation.json` and the standalone lifecycle reproduction script. Raw XML/logs and generated screenshots are preserved locally under `.validation/evidence/`; SHA-256 values for the named CI evidence are recorded in `validation.json`. Prior tracked audit/artifact outputs are restored to their source versions. Raw runtime data and temporary credentials are not committed.

Full selection: `python scripts/run_tests_isolated.py -- python -m pytest tests/ packages/qq -o addopts= -q -ra --tb=short --junitxml=<owned-path>`. Each durable Job owns the wrapper and actual pytest process. Both completed with full JUnit and exit 1; no interrupted run is counted as completed acceptance. Use the workflow's pinned browser dependency and an owned base interpreter with user-site imports disabled for future exact-environment reproduction.

## Unverified boundaries

No candidate push or remote workflow rerun; no new POSIX execution claim; no actual application-service restart/exit, real account/provider operation, user-data migration, deployment or human acceptance. Owned temporary test servers restarting inside their fixtures are test evidence only.

### Additional failures in both completed full Windows runs

Both complete matrix runs are **red**, despite all 14 original failing identities passing in each. No failures are excluded from their counts.

- Five `test_plugin_provider_window::test_owned_api_provider_console_lifecycle` variants fail: legacy-exe observes a visible ConsoleWindowClass; hidden-exe/cmd/powershell fail EnumDesktopWindows and close provider stdout; shown-exe observes owned input-indicator windows. These exercise real Windows desktop/console behavior under a hidden durable Job.
- `test_terminal_steady_pressure_ma::test_three_minute_natural_output_isolates_nonconsuming_observer`: producer threshold passed, fast observer received 23,144,162 bytes versus the unchanged strict >50,000,000 assertion. Python 3.14 receives 23,091,318 bytes against the same assertion. Concurrency/load causation is not established.
- `test_web_frontend::test_root_redirects_to_react_when_dist_exists`: response entered shutdown with inherited worker-1 process and queue-retry state bound to a closed/different event loop. Cookie middleware passes lifespan unchanged. This is a shared-state diagnosis from the traceback, not yet a complete offending-predecessor reproduction.

Baseline controls completed under actual Job `job_b6dcf69e89338a0fbf62c46e`. Its wrapper exits 0 when diagnostic pytest processes complete with expected exit 0/1; this does **not** mean the selected tests passed.

| Frozen source / selection | Passed | Failed | Skipped | pytest exit | Time |
|---|---:|---:|---:|---:|---:|
| baseline bbcb780d: five console variants, pressure test, four frontend-entry tests | 4 | 6 | 0 | 1 | 191.81s |
| candidate 67310a8f: five console variants, four frontend-entry tests | 4 | 5 | 0 | 1 | 5.48s |

The same five console variants fail on both sources, and the baseline pressure observer receives 23,117,399 bytes versus the same >50,000,000 assertion. These six failures predate this patch in the tested environment. All four frontend-entry tests pass on both sources outside the full-suite order; the original full-suite shared-state predecessor remains unproven. Evidence: `.validation/evidence/ci-cookie-extra-controls.json`, `ci-cookie-extra-baseline.xml/.log` and `ci-cookie-extra-candidate.xml/.log`. No product change, test skip or assertion relaxation was made to these additional tests.
