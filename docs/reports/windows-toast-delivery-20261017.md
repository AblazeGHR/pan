# Windows toast delivery regression, 2026-10-17

## Delivery status

Source fix and owned OS sender regression are complete in
`D:/project/pan-worktrees/windows-toast-delivery-20261017`, branch
`fix/windows-toast-delivery-20261017`, baseline
`e51b4ba50e22db464b8d45c5f3f1f2d4d99b5d4c` (initial status clean).
This is a TA delivery for MA acceptance; main/practical, push/release and service
restart are outside this task's execution boundary.

The precise original user failure has **not** been correlated with its original
result size or system UI observation. The reproduced defects below explain a
complete absence of a notification, but do not establish that every reported
missing toast has this cause. Banner display, the notification-center panel and
native clicking remain unverified for this run. No historical success is used
as present acceptance evidence.

## Reproduced causes

1. v0.3.3 passes the complete task result through UTF-8 base64 inside a
   PowerShell script, itself UTF-16LE base64 in `-EncodedCommand`. A 20,000 ASCII
   character body reproducibly fails before PowerShell starts with
   `windows_system_notification_launch_failed`, **WinError 206**. No WinRT API
   has run and no notification can enter the center. This is a launch failure,
   not an OS permission or toast visibility result.
2. Independently passing that same unbounded XML to the real inbox PowerShell
   helper through a temporary `.ps1` file avoids the command-line limit and
   exposes another failure: `Show()` rejects it with **HRESULT 0x803E0115**,
   `windows_toast_failed`, `setting=Enabled`, helper exit **1**. This shows that
   merely changing the transport to a file/stdin would leave an OS payload
   failure. See `audit/windows-toast-delivery/verification.json`.
3. Production `worker._publish_terminal_events` uses
   `dispatch_completion_nonblocking`, which discarded the thread's sender
   result and returned `ok:true, method:background` immediately. Thus both
   failures were invisible at the completion notification boundary. No existing
   failure log was sufficient to correlate the original incident.

## Source correction

- Bound the **complete escaped UTF-8 XML** to 4096 bytes, a conservative local
  preview budget, including the full activation target and markup. This is not
  a claim that 4096 is the exact limit of this Windows version: an owned 6000
  ASCII character baseline notification was accepted and stored in this run.
- Bound the escaped title to 512 bytes and fit the remaining body at Unicode
  boundaries with an ellipsis. XML escaping, CJK and emoji count by serialized
  byte cost. Session results and browser payloads remain complete.
- Preserve the entire deep link, action, Tag/Group and existing tag hash
  behavior. Overlarge activation metadata fails explicitly; never truncate a
  destination. No balloon fallback was added.
- Preserve WinRT HRESULT, app Setting, helper exit code and launch WinError.
  Asynchronous work reports `ok:null, method:background, delivery:unknown`,
  retains its task through completion and logs the actual result with Session
  ownership. Logs exclude result/title/URL/exception text. Even a successful
  synchronous sender reports `delivery:unknown` for all toast types.

## Job/desktop/token investigation

Existing implementation inspected: `packages/jobs/desktop.py`,
`restricted_token.py`, `independent_runner.py`, `window_guard.py`, `window.py`.
`DesktopCommand` starts on an owned `PanJob_*` desktop, uses a duplicate current
user token with a DefaultDacl deny ACE for PROCESS_SET_INFORMATION, atomically
enters a kill-on-close Job, and runs the owned descendant window guard. The
toast helper inherits its parent context and uses CREATE_NO_WINDOW; there is
no token switch, external notification relay or breakaway in the sender.

The baseline matrix recorded real child desktop names, PIDs, exit codes and
owned notification rows (`audit/windows-toast-delivery/matrix.json`):

| Launch condition | Observed desktop | Sender / own database entry |
| --- | --- | --- |
| CREATE_NO_WINDOW | Default | Enabled, accepted / present |
| Hidden token + Job + guard, explicit Default desktop | Default | Enabled, accepted / present |
| Private desktop + hidden token + Job + guard | PanJob_97775d24353cca375ab1f589cdff55d5 | Enabled, accepted / present |

The initial matrix also contains a `private` experiment whose actual desktop
was **Default**: Python's STARTUPINFO did not apply the dynamically assigned
lpDesktop field. That experiment is invalid as private-only evidence and is
excluded above; the reproducible matrix script now omits it. The combined
experiment uses native DesktopCommand, and really observes PanJob_*.

The fixed large-result sender also succeeded inside native DesktopCommand:
child PID 41964, desktop `PanJob_a18057970ed741bb99798da260ede894`, exit 0,
Setting Enabled, one owned stored row. These facts do **not** establish banner
visibility under those conditions. They contradict a deterministic failure to
enqueue caused solely by the tested isolation combination; UI-specific effects
and other Windows configurations remain unverified. No Job/window policy was
weakened to make notifications pass.

## Identity, settings and configuration gates

- Read the old delivery audits and probe scripts. They depended partly on a
  full database copy and old user-click evidence; neither approach was reused
  as proof of this run. Current probes use a read-only SQLite connection with
  exact owned AUMID/Tag predicates; no whole database snapshot was created.
- Current production AUMID registration key
  `HKCU/Software/Classes/AppUserModelId/Pan.Desktop.Notifications` was absent.
  The app-specific notification settings registry query yielded no values.
  Current real production-AUMID diagnostic sender nevertheless returned
  `Setting=Enabled`. No registration is added by this patch; current evidence
  does not establish that registration is necessary to fix these defects.
- `Setting=Enabled` does not prove global notifications, DND, banner visibility
  or notification-center panel behavior. Those settings were not modified;
  no unrelated settings window or other user's notification was inspected.
- Existing gates remain: only `done` with an enabled browser/system channel
  produces completion events; legacy missing/invalid channel fields normalize
  off; system dispatch runs only when the Session system flag is on. The
  completion-bridge defaults and bell settings logic were inspected, without
  modifying a user's Session/config or enabling another channel.
- Protocol button arguments remain the send-time validated loopback URL. No
  custom URI scheme, shortcut, COM registration or global policy is installed.

## Verification and limits

Commands used the existing interpreter
`D:/project/Pan-main/.venv/Scripts/python.exe` through
`scripts/run_tests_isolated.py`, clearing inherited PAN_* settings and using
owned test roots. All commands finished in seconds; none needed a >3-minute
background Job.

```powershell
$py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
& $py scripts/run_tests_isolated.py -- $py -m pytest tests/test_windows_toast.py tests/test_notifications_reminders.py tests/test_notification_closed_browser.py tests/test_agent_notify.py tests/test_report_error_context.py tests/test_codex_native_completion.py -o addopts= -q --junitxml=audit/windows-toast-delivery/unit.xml
& $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/verify.py
& $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/cleanup.py
git diff --check
```

- **Code regression: 92 passed, 0 failed, 0 skipped**, one existing
  pydantic-settings IncompleteFieldDefinitionWarning. Includes oversized ASCII
  and escaped Unicode, transport/XML budgets, complete click target, disabled
  settings, timeout/helper failures, dedup identities, Session snapshot,
  nonblocking execution, actual failure logging without private text and worker
  terminal/completion regressions. No assertion was weakened or test skipped.
- **Real helper and WinRT:** baseline launch failure and actual Show HRESULT
  above; fixed production dispatch and real asynchronous path both accepted.
  `fixed-background.log` records the real async result (local ignored log).
- **Owned runtime:** probe HTTP listener PID 35060, dynamically bound
  127.0.0.1:52334. The production completion deep link was exactly
  `http://127.0.0.1:52334/react/?session=ses_owned_toast_20261017`. The listener
  was closed after verification (`portClosed=true`). This is an owned click
  target, not a full running Pan web/frontend acceptance environment.
- **Stored OS facts:** the full original >20k-character Unicode body became a
  bounded preview; the exact link and action remained in stored XML. Sending
  twice with the production completion Tag/Group produced exactly one row.
  Combined private-job delivery also produced exactly one owned row. Evidence
  JSON contains only those diagnostic payloads.
- **UI/native click: unverified.** No screenshot, panel visual assertion,
  actual native click, browser launch/cold start or real user task acceptance
  is claimed. The owned click listener received zero accesses. The available
  Computer Use skill was read, but the required JS runtime/tool entry point
  was not callable in this session. No substitute simulated click is claimed.
- **Build:** no frontend source/dependencies changed; a frontend build was not
  run in this TA tree. No main/practical build, merge, push, release or service
  restart was performed. MA retains the authorized acceptance/FF/build step.

## Cleanup

Owned DesktopCommand contexts close their retained Job/desktop/guard handles;
the owned listener is closed. `cleanup.py` clears only the task-specific
`Pan.Owned.Toast20261017` identity and removes the exact two diagnostic
production tags/groups, never History.Clear on the production AUMID.
`audit/windows-toast-delivery/cleanup.json` records cleanup API results.
The subsequent read-only exact-identity/tag check found zero remaining probe
notifications and zero remaining production diagnostic tags (`ownedRemaining=[0,0]`).
No protected-port test/request or service operation, QQ/WeChat/external
message, registry write or global setting change was performed.
