# CP-X2 Windows hidden-owned migration candidate

Baseline: a9d236c1c16159ca27697cfe090e3b7256c8c417 (normal fast-forward from clean e7fc87e0).
Frozen source: 62644ff2aebc57d72fc6a1c454c7cc6b78ff4bc5.
Worktree: D:/project/pan-worktrees/xplat-job-posix-safety-20261027.
Branch: xplat/job-posix-safety-20261027. Integration-only; MA acceptance pending.

## Problem and narrow migration

WindowsProcessOwner previously used ordinary Popen then guard.assign. SW_HIDE /
CREATE_NO_WINDOW did not select the existing private desktop. The exact original
baseline ProcessOwner replay demonstrates actual Default desktop and forbidden
post-create assign (baseline-red-final.log / baseline-red.xml; 2 failed, exit 1).
This replay executes the baseline module in an isolated child; it is not a claim
that a full detached baseline checkout was tested.

DesktopCommand already had HiddenDesktop, HiddenJobToken, OwnedWindowGuard,
job_startup_info / JOB_LIST, suspended creation and membership-before-resume.
The candidate extracts that native creation algorithm into
process_creation_win.create_owned_process and routes BOTH DesktopCommand and
WindowsProcessOwner to it. OwnedPopen replaces only Popen's creation callback:
Popen still owns stdio construction, env caller mapping, communicate/poll/wait.
The existing attribute helper adds HANDLE_LIST to restrict inheritance to child
stdio handles. No second launcher/ledger/provider, new public interface/schema,
executable pin or claim-order change. No POSIX/server/worker/ledger product edit.

Hidden commands retain a real unique PanJob_ desktop and existing token/window
guard. Visible hide_window=False uses ordinary CreateProcessW/CREATE_NEW_CONSOLE
and inherited desktop, while retaining creation-time Job containment. Native
attribute/token/desktop failures fail launch; no post-assign fallback. Exact
FILETIME and all caller fences are preserved. No sandbox claim: explicit desktop
selection, modified DACL and external brokers are still outside the inherited
private-desktop boundary.

Close proves tree empty, closes Job, waits/closes process and thread handles,
stops window guard and closes desktop. Failure remains explicit/retryable.
Popen.Handle.Close marks closed before the system call; the adapter instead
checks CloseHandle and marks closed only on success. A real-handle negative test
proves retryability. Failed spawn uses existing CleanupStack/close_resources so
cleanup failures preserve the original error rather than being swallowed.

## Validation and provenance

Specified interpreter: D:/project/Pan-main/.venv/Scripts/python.exe
Python 3.14.5 (MSC v.1944 AMD64). No P23 private .validation venv used this round.
All final validation uses scripts/run_tests_isolated.py. Exact argv/source SHA /
exit / duration are in final-*.json; JUnit and logs are adjacent. Reproduce with:

```
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-windows-hidden-20261028/run_validation.py final-platform
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-windows-hidden-20261028/run_validation.py final-p23
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-windows-hidden-20261028/run_validation.py final-logs
D:/project/Pan-main/.venv/Scripts/python.exe evidence/cp-x2-windows-hidden-20261028/run_validation.py final-legacy
```

The runner writes the actual child exit even for intentional red. Source manifest
contains hashes for all seven changed source/test/doc files. Earlier iterations
and source-83b1c5e7 results are retained and do not substitute for final results.

Dedicated Windows tests prove real current desktop and exact FILETIME, three
generations plus venv redirector membership, all-tree death, desktop disappearance
(OpenDesktopW ERROR_FILE_NOT_FOUND), active input desktop unchanged, Unicode env,
stdin/stdout/stderr merging/cwd, visible intent, membership observed inside native
creation before resume/owner return with late assign forbidden, before/after
creation errors, post-return identity failure, close failure/retry, original token
DACL, runner hard death after running, and runner death INSIDE create before return
with no child marker ever executed. Sanitized proof/ records actual identity,
member desktop, birth, allDead/desktopGone and input desktop before/after. No argv,
environment values, business output or credentials are included in those proofs.

Original tests/assertions/skips were not edited. test_job_lifecycle_prevention.py
retains 11 legacy failures (8 RetiredEntryPoint errors and 3 timeouts after retired
controller entrypoints), same 11 nodes/categories with original baseline
ProcessOwner replay. See legacy-baseline.*, final-legacy.*, and
legacy-failure-comparison.json. These reds are not counted as passing or skipped.
Other 11 original lifecycle cases pass, including native independent creation
edges. The existing hidden-token and cleanup-failure suites remain unmodified.

## Remaining integration gate

Original CP-X1 owns public Job/lifecycle/controller changes. We exchanged current
interface notes only, not duplicate completion reports. Private diagnostic name:
owned.popen.desktop.name, actual retained HiddenDesktop; child GetThreadDesktop
must agree. No public schema addition. CP-X1 supplied these MA combination nodes:

```
tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash[before-claim-base]
tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash[before-claim-named-venv]
tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash[after-running-base]
tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash[after-running-named-venv]
-p tests.support.lifecycle_strict_options --cpx1-require-hidden-desktop
```

Those files/options are not in a9d236c1 and were not copied/merged here. Their
combined public cancel + actual desktop-gone proof is PENDING MA combination.
Existing local P23 public/canonical regressions are a separate evidence layer.
No remote CI, Windows Python 3.12 execution, Linux/macOS revalidation or deployed
service acceptance is claimed. No main/practical/push/deployment/existing-service
operation; no real data/profile/8767/8768. No new TA was created.

Native API/source rationale and exact old→new mapping:
../../docs/reports/CP_X2_CAPABILITIES_WINDOWS_20261028.md (from repository root use
docs/reports/CP_X2_CAPABILITIES_WINDOWS_20261028.md). Microsoft JOB_LIST/HANDLE_LIST
and CPython v3.12.10 primary links are retained there; local 3.14.5 source was also
inspected. CPython 3.12 callback is source-reviewed, not executed here.

## Final measured results at frozen source 62644ff2

| Group | Passed | Failed | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: |
| Dedicated Windows (13) + original ProcessOwner/token/cleanup | 52 | 0 | 19 | 0 |
| P23 protocol/owned/canonical/public + final executors | 54 | 0 | 0 | 0 |
| Process logs | 20 | 0 | 1 | 0 |
| Original full lifecycle prevention | 11 | 11 | 0 | 1 |
| Exact original ProcessOwner defect replay | 0 | 2 | 0 | 1 |

Shared/server import: exit 0. Relevant collection: 90 nodes, exit 0.
The 19 skips are original platform guards; log skip is the original POSIX-only
leader-lost test on Windows. No skip/assertion change. The two warnings are existing
Starlette/httpx and anyio deprecations, retained in final-logs.log.

Final runtime archive: 13 P23 stats + 14 log stats + 28 pre-existing diagnostic
format proof files, copied byte-for-byte under final-runtime/. Only the 13 tracked
generated P23 files were restored, after archival. Only the exact 28 archived
untracked proof files were removed from the shared generated output, with resolved
path containment and copy hashes checked. No recursive delete or unrelated change.

Final cleanup: all 10 exact proof-member lifetimes absent/dead (PID reuse compared
against original FILETIME), their real desktops gone, zero owned helper survivors.
See cleanup-final.json. P23/log own teardown asserts supervisor exit/resource cleanup.

Intermediate source-83b1c5e7 records retain their start SHA; some overlapped the
handle-close refinement. They are excluded from final source-bound claims. All
final-* results were rerun after frozen 62644ff2, with no later product/test edit.
validation.json and source-manifest.json are the acceptance index. Evidence commit
adds only this report/evidence, leaving the frozen source unchanged.
