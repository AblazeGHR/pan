# GitHub CI repair evidence (2026-10-06)

Worktree: `D:/project/pan-worktrees/github-ci-20261006`; branch: `fix/github-ci-20261006`.
Baseline and failing run SHA: `9f1108a940cf3a0cc514724ad23ccd83daf99de4`.
Original run: https://github.com/AblazeGHR/pan/actions/runs/37450384323.
No push, release, tag, stash, reset, or advancement of main/practical is authorized here.

## Original evidence and classification

The complete run archive includes all 18 job/step log files. Both complete job logs were also downloaded independently with `gh run view --job ... --log`. Archive CRC validation passes. `raw-log-manifest.json` records their sizes and SHA-256 digests.

Each original leg collected 2914 tests: 2824 passed, 40 failed, and 50 skipped. Original double-quiet output did not contain skip reasons. The ordered collection contains every original test plus two new regressions. Mapping the original progress characters to that collection produces exactly the same 40 failure identities as each full log's failure summary, so the original skip identities are also recoverable without guessing. `original-classification.json` contains all 80 individual failures and their classifications; `run-37450384323-py312-outcomes.json` and `run-37450384323-py314-outcomes.json` retain the failure/skip identities.

| Cause | Python 3.12 | Python 3.14 | Treatment |
| --- | ---: | ---: | --- |
| Independent headless sidecar dependencies absent | 36 direct failures + real stdio create failure | 36 direct failures + real stdio create failure | Set up Node 24 and run standalone `npm ci` using the sidecar's own unchanged manifest/lock. The unchanged real stdio chain succeeds with dependencies installed. |
| PID 4 incorrectly assumed unqueryable | 1 | 1 | Deterministically return an OpenProcess failure in the test; keep the production ALIVE/UNKNOWN/retained-DEAD contract unchanged. |
| Ctrl-C child assumed empty read only | 1 | 1 | Handle and separately record empty read and Python KeyboardInterrupt in the cooperative test child; still require interruption, a live process, and PING roundtrip. |
| Secret reads race atomic replacement | 0 | 1 | Include checked reads in the existing per-path cross-process mutex; retain fail-closed read exceptions. |
| 120k summary backfill scheduling gap measured at 63ms | 1 | 0 | Preserve the 50ms threshold and all assertions. Twelve fresh Python 3.12 baseline repeats pass. Final isolated matrix also executes this exact test. The cause of the isolated remote latency observation is not established locally. |

Of the original 50 skips, 34 are terminal tests gated by unavailable sidecar dependencies; the other 16 are Claude/Kimi machine probes, skill sync, frontend reconciliation probes, and timezone availability. Installing sidecar dependencies activates the real terminal tests. Final JUnit outcomes distinguish executed tests from existing environment skips, including ancestor-Job detach restrictions.

## Semantics and test changes

`read_secret` now acquires the same bounded named mutex as write/update/delete before checking paths, checking ACLs, and reading/decrypting the payload. Windows mutex recursion permits the existing update's locked read-modify-write. No read exception is swallowed, no fallback payload is manufactured, and ACL/DPAPI/identity checks remain intact.

The new read-lock regression blocks a checked reader while an independent store attempts publication. Publication must wait for the reader, then succeed; an injected PermissionError must remain the cause of SecretStoreError. An isolated test-process probe loads the exact baseline read_secret function from Git and fails this new regression at `publication raced a checked reader`. The corrected function passes. The existing real concurrent-reader/identity-update test is unchanged.

Identity test changes replace machine-privilege assumptions with a narrow OpenProcess failure injection. Real child ALIVE, retained-handle DEAD, fresh-handle refusal, FILETIME mismatch refusal, and successful identity-bound termination are still checked. The kill refusal case now targets only an owned child rather than System.

The Ctrl-C test catches only KeyboardInterrupt around the child's blocking console read. It records which behavior occurred in JUnit and requires an explicit interruption marker, child liveness, and subsequent input/output. This tests a cooperative child surviving interruption; it does not claim that Python's default uncaught KeyboardInterrupt should preserve the child. The separate production foreground interrupt test is unchanged and must prove delivered Ctrl-C, usable shell, same runner identity, and cleanup.

CI now prints skip reasons and uploads a separate JUnit artifact for each Python leg even when tests fail. No test is removed, newly skipped, xfailed, or weakened; no timing/size/resource threshold is relaxed. Usage code is unchanged.

## Validation

An additional complete run exposed a launcher close-attempt accounting race: a thread can exit between the caller's `in_flight` snapshot and `invoke`'s gated check. Two actual closes were reported as one. The report now uses the worker's actual invocation count; close ownership, single-flight behavior, deadlines and convergence criteria are unchanged. A deterministic regression injects the stale snapshot while using the real worker and checks two calls, maximum concurrency one and reported attempts two. The existing false-then-success assertion remains intact.

The pre-fix isolated complete matrix exited 0 on Python 3.12 and 1 on Python 3.14. The latter failed the existing machine-global sidecar PID check during simultaneous matrix execution; serial execution is needed to distinguish another leg's sidecar from an owned residual. No assertion was changed. A new durable Pan Job `job_d7bf6eec1c9d8b1142044be1` runs each launcher's entire suite and the complete matrix serially for 3.12 then 3.14, with separate profiles/temp directories and UTF-8 output. Registry now confirms completed, exitCode 0, notification delivered. Each targeted launcher suite passes 84 tests. Each complete matrix preserves all 2914 original tests plus two regressions: 2903 passed, 13 skipped, zero failures/errors. JUnit identity comparison and all original 40 failure outcomes pass. Python 3.12 takes 1071.09s; Python 3.14 takes 1103.84s.

At the user's request the local skill copy `C:/Users/14709/.agents/skills/pan/SKILL.md` and its three reference files were synchronized byte-for-byte from this worktree's `docs/skills/pan`. SKILL.md SHA256 is `ffb30cdc8c51be78c3a69133ee8e2cac4a6bde5f7511618b3c8e3c4b9ee408e0`. The repository source was not edited.

Final complete matrix is green locally; exact identity/outcome checks are in `final-summary.json`. The exact workflow Python requirement files and pytest-timeout are installed in separate Python 3.12.12 and 3.14.5 venvs; pip upgrade/install output and version freezes are retained. Local Node is 24.15.0. The sidecar manifest and lock are unchanged and `npm ci` installed its own two exact-pinned dependencies; no shared junction was installed.

Directed concurrency/read-lock/real-ConPTY tests: 10 fresh rounds per Python version, three tests per round, all pass. Each local Ctrl-C observation is `empty-read`; the original GitHub runner's KeyboardInterrupt branch is documented in its full log and still requires remote candidate validation.

Real stdio transport suite: five fresh rounds per Python version, ten tests per round, all pass. A first combined Python 3.12 focused run timed out in the stdio test; the isolated single test and the same ordered 85-test suite subsequently pass. Timeout stacks and diagnostic scripts are retained rather than discarded. Final complete matrix results are the local acceptance evidence.

Frontend dependencies were independently installed with `pnpm install --frozen-lockfile`; production build passes and verifies 50 compressed assets. No frontend source, manifest, or lock was changed.

Initial local complete runs used a basetemp inside the repository and reproducibly failed a manifest-label test because repo-relative labeling differed from its external-temp contract. The exact test passes with an external isolated basetemp; final complete runs use the latter layout. No manifest test or implementation was changed.

## Isolation deviation and remaining boundaries

Evidence navigation: the two standalone original logs and complete GitHub archive are beside this report. `original-classification.json` enumerates every failure; original outcome JSON files enumerate every skip identity. `final-summary.json` maps all original failures/skips to final outcomes and lists current skip reasons. Final skip categories per leg: seven absent Claude probes, three absent Kimi probes, one absent gitignored CodeBuddy skill copy, one absent timezone database, and one actual ancestor-Job detach restriction. The installed global Pan skill is separate from the CodeBuddy checkout copy. Some non-ASCII JUnit skip messages are emitted with replacement characters by the existing environment; the final UTF-8 raw logs retain their readable reasons.

`local-validation.zip` retains the entire `.ci-evidence/` directory, including initial failed/timeout runs, repeat XML/logs, exact scripts, freezes, frontend build/install logs, both deterministic baseline-failure probes, and final candidate hashes. Extract at the repository root to recover script paths. `pan-job-registry.json` records terminal Registry facts; `pan-job-matrix.log` records the serial stages. Local runner options only replace `addopts=-q` with verbose output and use legacy JUnit to retain properties; the configured 300-second timeout remains active. Remote-main was rechecked after Job completion and still equals the baseline.

The first local runs inherited the user's profile. Existing Claude probe tests detected a historical probe there; their title-write/restore and fork/cleanup cases executed successfully. This was an isolation oversight. Their assertions prove the title was restored and the fork removed; no pre-run byte hash was captured, so byte-identical restoration is not claimed. The final matrix explicitly redirects child USERPROFILE, APPDATA, LOCALAPPDATA, TEMP, and TMP to independently owned external directories, in addition to removing inherited PAN settings and using isolated Session/config/Job/scheduler/terminal stores and dynamic HTTP ports. No further user-profile probing is authorized. This deviation is retained for MA review.

Port 8768's original listener is PID 12180, Python 3.14, started `2026-10-06T12:58:35.483377+08:00`; it is inspected read-only. No test/service action targets ports 8767/8768 or the deployed Pan service. Main/remote-main snapshots must be rechecked before delivery. If main moves, merge its current history into this branch and rerun affected tests and final matrix; never advance main here.

MA owns integration acceptance and push. The exact delivered SHA's remote Python 3.12/3.14 jobs, JUnit failures/skips, foreground Ctrl-C behavior, and the remote 50ms history assertion must be checked after MA push. Local checks are not remote CI or human/browser acceptance. No release/tag is permitted.

The user subsequently specified that future long tests must use Pan durable Jobs. The already-running local matrix was launched before that instruction and is not represented as a Pan Job; its actual subprocess exit codes and JUnit files are the evidence. Any subsequent long rerun must use `agent_background_start`, retain its Job ID, and inspect Registry exit/log facts and result artifacts after the terminal notice.
