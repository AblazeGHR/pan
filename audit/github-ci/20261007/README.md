# Follow-up CI repair: retained identity and close proof ordering

Worktree/branch: `D:/project/pan-worktrees/github-ci-20261006`, `fix/github-ci-20261006`.
Parent: `ee2816b61691be8bc185ba2a264999b09100120e`.
Remote failing run: https://github.com/AblazeGHR/pan/actions/runs/37461154308 (exact parent SHA).

Both remote legs collected 2916 tests: 2897 passed, 17 skipped, two failed. All original 40 failures per leg passed. The new failures were previously sidecar-dependency skipped. Full remote logs, original JUnit, CRC-verified archive, metadata, all skip identities, and failures are in `validation.zip` under `.ci-evidence/remote-37461154308/`.

## Root causes and semantics

`test_real_external_kill_is_exited_and_archivable`: production bootstrap retained a separate identity-bound runner handle only when the Python shim PID differed from the runner PID. GitHub uses direct Python, so no handle was retained; after kill, a refused fresh lookup defeated the conservative reconciliation contract even though the owned spawn process had exited. Real Windows startup now always retains and verifies the bootstrap runner handle, independent of interpreter layout. The existing same-handle identity checks, UNKNOWN refusal, tree cleanup separation, release retry, and secret retention rules are unchanged. No new global PID killing or wrapper-death inference is introduced.

`test_detached_shell_survives_real_service_host_exit_and_reconnect`: the child writes its report before checking a refused detach, then immediately calls close. Its failed remote trace shows `CleanupUnconfirmed: engine-cleanup-unproven`; cleanup in the parent subsequently reports `not-attached`, masking the initial child error. Stop acknowledgement precedes launcher engine cleanup/status publication. Service close previously sampled those final proofs once and returned early even with budget remaining. It now spends the existing remaining deadline observing engine convergence and identity-bound process exit after confirmed stop. It does not resend stop, extend the budget, weaken proof requirements, terminate unknown processes, or delete secrets before proof. Tests exercising genuinely missing proofs and budget limits remain unchanged and pass.

The same-service detach test still skips on the actual ancestor Job restriction. We do not claim durable breakaway is supported on this machine; the separate real service-host test executes its refusal/cleanup path. The full remote child stderr was truncated by pytest, so deeper unprinted diagnostics are not claimed. Final remote candidate acceptance must distinguish a genuine durable detach from refusal and confirm both previously failing tests.

## Regression and validation

Two new deterministic service tests: direct launcher startup must retain the bootstrap handle when spawn and runner PID are equal; close must consume later engine/exit proofs without another stop, retaining the secret until proof. Both fail against exact parent methods loaded only in an isolated test process; both pass after repair. All original tests and assertions are retained.

Pan matrix Job `job_66856681c766effea1196a54`: completed exitCode 0, notification delivered. Separate Python 3.12.12 and 3.14.5 venvs use workflow dependencies and sidecar's unchanged manifest/lock. Each focused service/rework/archive/durable suite: 97 passed, 1 existing skip. Each complete CI: 2905 passed, 13 existing skips, zero failures/errors; exact identity multiset is all 2914 original tests plus four regressions. Python 3.12 full duration 1079.81s; Python 3.14 1092.26s. The unchanged original failure identities all pass. Remaining full skip categories: seven Claude machine probes, three Kimi probes, one CodeBuddy checkout copy, one timezone database, one ancestor-Job detach restriction. No new skip/xfail or threshold relaxation.

Before repair, diagnostic Job `job_7fce74b12d9ec81967ceb63d` completed exitCode 0 across five rounds per Python: both target real tests pass locally while same-service detach is skipped. This confirms local success alone did not reproduce GitHub's direct-interpreter layout/timing. Its runner log is retained; the repeat script reuses its own scratch XML/log paths for the post-fix rounds, so those per-round files represent the latest post-fix run, not independently preserved baseline files.

Post-fix repeat Job `job_535fd9aeffa1f75afbee5dff` completed exitCode 0: five fresh rounds per Python, each two real tests passed and one existing ancestor-Job detach skip. All ten per-round JUnit files confirm zero failures/errors. No frontend/dependency edits in this follow-up; prior independent frontend build remains applicable.

All follow-up children redirect profile, AppData and temp directories and remove inherited PAN settings, using dynamic non-8767/8768 HTTP ports. No user-profile probe, deployed-service operation, main/practical advancement, stash/reset, push, tag or release. Remote main was rechecked after the matrix and remains the parent. User requests shutdown once the task is complete; it remains pending final delivery/acceptance.
