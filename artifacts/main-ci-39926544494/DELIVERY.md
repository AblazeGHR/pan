# CI 39926544494 evidence and candidate handoff

- Repository: `AblazeGHR/pan`; branch `fix/main-ci-39913304619`.
- Remote run: [39926544494](https://github.com/AblazeGHR/pan/actions/runs/39926544494), exact source SHA `73289afb5ee3d1e8bf225078cc0ef953f7a9c5eb` (main remains this SHA; practical remains `2652ac8e6d0ff312a255666bc5e459890346e6e5`).
- Remote conclusion: `failure`; `attempt2/gate.json` records the complete identity manifest and matrix comparison.

## Remote matrix

| leg | job | JUnit | failures | skips | conclusion |
|---|---:|---:|---:|---:|---|
| Python 3.12 | 120347402841 | 5519 total, 5456 passed | 2 | 61 | failure |
| Python 3.14 | 120347402304 | 5519 total, 5450 passed | 8 | 61 | failure |

3.12 failures: `tests.test_eta_report_integration_http::test_trusted_locker_and_error_report_share_product_runtime` (isolated server startup timeout); `tests.test_illegal_queue_wake_http::test_pan_and_long_job_crash_notification_stays_locked_until_wake` (ledger remained `reserved`, expected `sent_to_cli`). 3.14 failures are all eight parameterizations of `tests.test_legal_offline_http::test_owned_host_signal_vs_exit_api_handshake` (idle-SIGINT, idle-SIGTERM, safe-api, preserve-api, q-config, q-menu, q-menu-2, q-menu-3), each isolated server startup timeout.

All 14 failures from the earlier 0156 investigation passed on both legs. The 61 existing skips are identity-equal across both legs; no skip was added. Attempt 1 evidence is retained: its 3.14 JUnit had one setup error and its 3.12 job lost runner communication with no JUnit; recovery annotations/run archive document the missing evidence.

## Candidate and root repair

Candidate source is `f46bcd7d55710643d86627996b7122aaac01970d`, two test-support-only commits on 73289. It captures owned server trees before readiness, cleans failed starts and late owned children, drains the owned lifespan through a test-only shutdown hook, and emits bounded startup stacks at 15 seconds. The queue test waits for the original pending ID to reach `sent_to_cli`; it does not resend, change queue IDs, or weaken the lock assertion. Product packages are unchanged.

A differential late-child probe reproduces baseline owned child/conhost residue and shows no residue with the candidate. This proves a cleanup defect and its repair; it does not uniquely prove that defect caused the first remote readiness timeout. Startup tracing makes future timeout evidence actionable while retaining the existing 20-second limit.

Local candidate JUnit `candidate-green.xml`: 541 tests, 540 passed, 0 failures/errors, 1 existing skip, exit 0. Additional new-failure regression JUnit: 28 passed, 0 skipped, exit 0. Process probe: 20 cases, no remaining owned processes. Red evidence is retained in the archive; the later broad candidate run is authoritative.

## Verification boundary

No push, release, main/practical movement, service restart, real provider/QQ/account access, or shared dependency write occurred. This candidate has not been re-run by GitHub; remote 39926544494 remains red. MA should review this clean candidate and run the normal remote gate on the candidate SHA if accepted.
