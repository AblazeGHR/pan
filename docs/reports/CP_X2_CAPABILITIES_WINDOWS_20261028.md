# CP-X2 Windows hidden command migration boundary — 2026-10-28

Baseline: `a9d236c1c16159ca27697cfe090e3b7256c8c417`. This is a candidate
platform implementation, not MA acceptance or a three-platform support claim.

| Existing capability / interface | Candidate implementation | Assurance / limits |
| --- | --- | --- |
| Windows ProcessOwner.spawn, hide_window=True | Thin Popen stdio adapter → extracted DesktopCommand native creation → retained HiddenDesktop + HiddenJobToken + OwnedWindowGuard | Actual unique PanJob_ desktop inherited by ordinary descendants. No SwitchDesktop. Not a sandbox; explicit alternate desktop selection, modified DACLs and external brokers remain outside this guarantee. |
| Windows owned tree | Existing job_startup_info / PROC_THREAD_ATTRIBUTE_JOB_LIST, now also explicit stdio handle list | Job membership at kernel creation, proven while suspended, before ResumeThread. No later-assign fallback. Missing native attribute/token/desktop support fails launch. Windows 10+ native API boundary. |
| hide_window=False | Same atomic Job creation, ordinary CreateProcessW, inherited desktop, CREATE_NEW_CONSOLE | Preserves visible intent; no hidden desktop/token is imposed. |
| FILETIME / ownership / public callers | Same ProcessOwner API, record schema and PID + exact FILETIME checks | No Attempt/lease/epoch/generation/fence change. Popen still handles pipes, communicate, poll and wait. |
| Owned resource lifecycle | Confirm Job tree empty → close Job → wait/close retained process → stop window guard → close desktop | Failure remains an error/unconfirmed and close can be retried; no false closed marker. Runner death relies on existing non-inheritable kill-on-close Job plus OS handle cleanup. |

Old→new mapping: DesktopCommand's token/job attribute/membership/suspended resume
algorithm is extracted into `process_creation_win.create_owned_process` and used
by both DesktopCommand and Windows ProcessOwner. The Popen subclass replaces only
its creation callback; it adds no runner, ledger, provider or public schema.
Its private CPython callback is bound by parameter names; incompatible callback
shape fails before creation. Python 3.14.5 is locally tested; 3.12 is source
checked, not executed here. No POSIX/Darwin/server/worker/ledger product edits.

Dedicated real-kernel nodes: `tests/test_platform_process_windows_hidden.py`.
They cover base + specified venv tree identity/desktop/cleanup, pipes and Unicode
environment, visible intent, pre-resume atomic membership with forbidden assign,
three failure stages, close failure/retry, token DACL, running runner death and
creation-edge runner death with no child instruction. Proof JSON lives under
`evidence/cp-x2-windows-hidden-20261028/proof/`; no argv/env/business output.

MA combination gate, supplied by original CP-X1 (not present in this baseline):
`tests/test_lifecycle_controller_publication.py::test_canonical_job_cancel_after_controller_crash`
with all four parameters (`before-claim-base`, `before-claim-named-venv`,
`after-running-base`, `after-running-named-venv`) and
`-p tests.support.lifecycle_strict_options --cpx1-require-hidden-desktop`.
The child reads the actual desktop via GetThreadDesktop; CP-X1 reuses the original
`desktop_gone` assertion. No fabricated name/claim or branch merge substitutes for
that public combination proof. Supervisor retains the OwnedProcess (including
`popen.desktop`) through cleanup; stable private diagnostic name is
`owned.popen.desktop.name` while held, with no public schema expansion.

Primary sources:
- [Microsoft UpdateProcThreadAttribute](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute): JOB_LIST assigns the child to supplied Jobs; Windows 10+; HANDLE_LIST restricts inherited handles and requires inheritable handles/bInheritHandles.
- [Microsoft CreateProcessAsUser](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-createprocessasuserw): startup desktop/environment/handle inheritance.
- [CPython v3.12.10 subprocess.py](https://github.com/python/cpython/blob/v3.12.10/Lib/subprocess.py): Windows _get_handles/_execute_child/_close_pipe_fds and retained Handle. Local Python 3.14.5 source inspected for exact callback/pipe ownership; [Python subprocess docs](https://docs.python.org/3/library/subprocess.html#windows-popen-helpers) expose handle_list, not a Job-list hook.

Unverified: corrected combined CP-X1 public cancel/desktop proof, Windows Python
3.12 execution, remote CI, and any Linux/macOS change (none made in this task).
Original legacy retired-entrypoint failures remain red and are reported separately.
