# Practical Terminal / Rewind deployment acceptance

## Baseline and repair

- Candidate seed: `420f627ca370faac0f80c6cd473fbf23e2483407`; independent branch `fix/practical-terminal-deployment-20261006`.
- Actual deployed interpreter: `D:/project/Pan/.venv/Scripts/python.exe`, Python 3.14.5. This acceptance did not substitute a temporary uv interpreter.
- `pyte==0.8.2` was already in minimal-requirements.txt, but absent from the deployed environment. Installed that manifest into the actual environment (plus pytest-timeout for verification). Installed the exact-pin sidecar packages with npm ci in its dedicated directory; no lockfile changes.
- Rewind now checks the observer dependency before provider fork for all three scopes. Missing-dependency text points to the deployed interpreter, not a temporary uv test environment.
- setup.bat installs the separate headless sidecar dependencies and runs a read-only deployment check. `ready` checks prerequisites, not provider credentials or functionality.

## Evidence, failures, and differential verification

- The first broad Job was interrupted around a practical restart; its partial output is retained, not counted as a completed suite.
- Completed broad Job `job_e889a892972f9cbc75513aa9` used actual practical Python with isolated test data/environment. Its exit 1 was real: 51 Python files collected 903 tests, initially 899 passed / 3 failed / 1 skipped. Logs/XML remain unchanged in round2/.
- One failure was the driver static ban on adapter literals in the new core error text. Removed the adapter-specific wording. Two failures were the lifecycle harness requiring durable detach even in an ancestor Job that restricts it. Added an explicit refusal branch that asserts unchanged identity/status and verifies managed cleanup/restart; the successful durable branch remains intact. Cleanup also handles an unattached, already-dead owned record without masking the original assertion.
- The API deep-JSON test accepts either static parser rejection or static non-object rejection: Python 3.14 can parse the deeply nested list where older Python raised RecursionError. Both paths still reject with 422 before business execution.
- Final differential run: 28 passed (driver, deployment, real Pan lifecycle, rewind terminal-core). Source blob anchors and JUnit/XML are in round3/. Across the original file selection plus these replacements: 902 passed / 1 skipped. This is a combined file-by-file result, not a fresh single full-repository run.
- The skipped durable test explicitly records an actual ancestor Job restricting detach. Do not claim that capability passed.
- Full frontend: 122 files / 1330 passed; build succeeded with 50 compressed production assets. Existing large-chunk build warnings remain.

## Real browser and process checks

- Browser archive/restore/manual-workspace/delete check used Chromium, the actual practical Python and real Terminal REST/WS/ConPTY/headless chain, isolated data and an ephemeral port. Result: no page errors, archive did not stop the shell, restoration/binding/deletion passed.
- Full browser check initially failed at its unconditional durable-detach expectation. The saved failure has harnessExit=0 and independently shows Chinese input, resize, control takeover, disconnect preservation and real foreground CTRL_C_EVENT delivery succeeded. Added an explicit 409 detach-refused identity/status branch; the original successful detach branch is preserved. Both paths test reconnect and shell state without upgrading the refused path to a durable success.
- Corrected full browser check passed (71 WS events, zero page errors, harnessExit=0). It also verified browserless output beyond the retention window, screen recovery after reconnect, and confirmed-close control-display cleanup. This environment used the honest detach-refused branch, not a durable success.
- All browser outputs are in the new `audit/terminal/implementation/browser/practical-deployment*` directories. Earlier evidence was not overwritten.
- Owned runner-survival experiment (`runner_survival.json`): killing only the parent preserved a detached child; closing an inherited KILL_ON_JOB_CLOSE Job killed it; taskkill /T of the owned parent killed it. DETACHED_PROCESS is not independent-service isolation. These results do not identify which mechanism the user's actual Pan restart used.

## Boundaries

- No existing Pan listener, user Session, provider history, or practical data was stopped, rewound, migrated or otherwise mutated by these tests. Practical runtime dependency installation is the intended deployment repair.
- Real provider-side CBC three-scope rewind against new provider checkpoints was not executed. The three-scope preflight, scope/anchor/file/transcript regression suites, and real PTY observer were tested; they are not a provider acceptance claim.
- This is the terminal/rewind/deployment selection, not the entire repository or a complete running-Pan integration test. Browser harness mocks unrelated Dashboard endpoints only.
- Durable detach remains environment-dependent; write budgets are not hard OS SLAs; screen recovery remains conservatively partial. Independent Job-service isolation is not implemented by this repair.
- Existing untracked main/practical files and ignored dependencies must be preserved during the authorized merges. No push or service restart is included.
