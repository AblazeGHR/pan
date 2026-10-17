# Windows toast delivery regression, 2026-10-17

## Delivery status

**Final TA acceptance evidence: banner button passed; delayed notification-
center body navigation passed.** On Oct 18 MA relayed the user's "成功了"
after the root-protocol fix and the instructed delayed Win+N body test.
The center's expanded button was not separately retested. The earlier failure
and investigation below are retained as historical evidence, not final status.
The
user confirmed seeing the post-registration 23:48:02 probe, but the banner
timed out before clicking. That probe had no click target. The earlier
2026-10-17 23:42:55 owned small-toast
check had neither a visible banner nor a notification-center item, despite
`ok:true`/`Enabled` and an exact stored database row. The payload fix is
insufficient to solve the original incident. The latest app-display-identity
candidate below has current human banner evidence (`visible-seen.json`), but
no center-panel or native-click success is claimed.

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

## MA follow-up: desktop capability and current system gates

MA requested renewed investigation of the visible layers after source commit
`10dae21848172ac51deac669ae3213d4f7f2dcb1`. This section records fresh checks;
it does not mark that commit accepted.

Computer Use SKILL.md, guidance.md and confirmations.md were read again.
The current `ALL_TOOLS` inventory has **173** entries and **zero** node_repl,
Computer Use/Windows desktop or lazy tool-search entries. The only screenshot
tool is `mcp__chrome_devtools__take_screenshot`, documented as capturing a page
or element. It cannot observe the Windows toast or notification-center panel.
Exact inventory: `audit/windows-toast-delivery/tool-inventory.json`.

The skill's Initialize section requires: "The bundled `cua_node` `@oai/sky`
package is the core entry point for Computer Use. Import it directly by
package name from the JavaScript session. Do not spawn
`codex-computer-use.exe`, search for the helper executable, or build a custom
helper protocol client." There is no callable JS session tool for this entry
point in this run. No helper executable was searched for/launched, no custom
protocol was constructed, and no unrelated user's window was controlled.

Read-only check at **2026-10-17 23:40:52 +08:00**:

| Check | Actual result | Interpretation limit |
| --- | --- | --- |
| SHQueryUserNotificationState | HRESULT 0, state 5, QUNS_ACCEPTS_NOTIFICATIONS | Does not independently establish the Windows 11 DND switch |
| Pan ToastNotifier.Setting | Enabled | App API setting, not actual display |
| HKCU PushNotifications/ToastEnabled | Value/key absent | No explicit value found; do not infer a user-facing toggle position |
| HKCU and HKLM PushNotifications policy NoToastApplicationNotification | Both absent | No explicit disable found in these exact locations |
| HKCU and HKLM Explorer policy DisableNotificationCenter | Both absent | No explicit center-disable found in these exact locations |
| Pan app Enabled / ShowBanner / ShowInActionCenter | All absent | No explicit per-app override found in these exact locations |

Reproduce this non-mutating check:

```powershell
$py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
& $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/visibility_state.py
```

Evidence: `visibility-state.json`. No notifications were sent by this follow-up
check, no notification payloads were read, and no registry/settings were
written. We have **not** demonstrated that a system policy or DND is blocking
display. The minimum missing observation is a human/working desktop tool
reading Settings > System > Notifications, including the DND toggle and its
automatic rules. Do not ask the user to change them based on these results.

Microsoft's documented meaning of SHQueryUserNotificationState concerns
notification availability, including busy, presentation, fullscreen and quiet
time; it is not evidence of a rendered toast:
[QUERY_USER_NOTIFICATION_STATE](https://learn.microsoft.com/en-us/windows/win32/api/shellapi/ne-shellapi-query_user_notification_state).
The checked toast-disable policy mapping is documented in
[ADMX_WPN](https://learn.microsoft.com/en-us/windows/client-management/mdm/policy-csp-admx-wpn).

### Minimal manual acceptance handoff for MA

1. Read Settings > System > Notifications: record the Notifications toggle,
   DND toggle and enabled automatic rules. Read the Pan app entry if present,
   including banner/center toggles. If the app is absent, record that fact.
   Do not toggle anything. If a setting is off, report its precise label and
   value; any requested change must be explicit and user-performed/authorized.
2. In this tree, send **one** small clearly marked production-AUMID diagnostic
   while the user watches. This command creates no listener and has no click
   target, so it tests banner/panel visibility alone:

   ```powershell
   $py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
   & $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/visible_check.py --send
   ```

3. Record whether that exact title visibly pops up. After the banner times out,
   the user opens the notification center and checks only that exact diagnostic
   item. Capture/crop only this item's relevant evidence; do not inspect other
   notifications. A helper `ok:true` is recorded separately from both answers.
4. Clean **only** the diagnostic item after observation, even on failure:

   ```powershell
   & $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/visible_check.py --cleanup
   ```

   The helper uses exact History.Remove with the task's Tag/Group and records
   sender/cleanup results separately from visibility; never substitute
   History.Clear on the production identity.
5. Native click is a separate test: MA first owns an isolated Pan runtime on a
   dynamic non-8767/8768 port and a real owned Session, then sends a completion
   for that Session. User clicks its native "打开会话" button after the sender
   exits. Verify the access URL and selected Session in that owned runtime.
   Do not use the already-closed 52334 listener, a typed/pasted URL, or a browser
   onclick injection as native-click proof. Shut down only that owned runtime
   and remove only its diagnostic Tag after verification.

These steps are prepared instructions; they have **not** occurred in this
follow-up. No new banner/panel/click success is claimed.

### Latest main composition check

Canonical root `D:/project/Pan-main`, branch `main`, still points to
`e51b4ba50e22db464b8d45c5f3f1f2d4d99b5d4c`. `git diff e51b4ba5 main` is empty
and main is an ancestor of the task HEAD. Canonical untracked files were
observed and left untouched. There are no new main source changes to integrate
or risk-specific combination tests to add. The 92-test result above applies to
the unchanged implementation. This follow-up changes diagnostic artifacts and
this report only; no mechanical full-suite rerun, frontend build, main/practical
mutation, push or restart is warranted or performed.

## Reopened after current human visibility failure

MA relayed the user's exact answer, "没有，什么也没有", after running
`visible_check.py --send` at 23:42:55 +08. `visible-failure.json` preserves the
original helper JSON, the observation attribution, and the **exact same
AUMID/Tag/Group** database row before removal. This is a demonstrated counter-
example to inferring visible center entries from database storage.
`visible-cleanup.json` records exact History.Remove for this one diagnostic.
No production History.Clear was called.

The observer was PID 30632 in Session 1, active console Session 1,
`WinSta0/Default`. A fresh registration-only inbox PowerShell helper also ran
in Session 1, `Environment.UserInteractive=true`, with the **same user SID** as
the invoking process (evidence stores only a hash). These are current process
context facts, not a screenshot or proof of DND position. The queried WPN
Operational log yielded no usable matching event evidence; no logging policy
was enabled or changed.

### Intermediate working probe vs shipped path

The user's recollection of a visible intermediate version matches a concrete
historical difference that warrants a current controlled test:

- The earlier human-click probe used **Pan.NotificationsProbe**, per
  `audit/notification-center/probe/user-check.json` (01:24:52 on Oct 16).
- The old report records that at 01:19 its registry key was created with only
  `DisplayName="Pan Probe"` under HKCU Classes/AppUserModelId; the app key was
  removed at cleanup around 01:53. The old `reg-aumid.ps1` and
  `cleanup-probes.ps1` explain those distinct operations.
- The final production sender instead used **Pan.Desktop.Notifications** with
  no equivalent display registration. Its old production E2E established
  database storage, not a current human-visible result for that identity.
- Therefore the old visible probe and final delivery were **not equivalent
  application-identity conditions**. Historical click evidence does not prove
  current visibility. This is a supported difference and a candidate cause,
  still awaiting the current single-variable human comparison.

### Minimal candidate and bounded side effects

`windows_toast.build_identity_script()` now ensures before toast creation:

```
HKCU\Software\Classes\AppUserModelId\Pan.Desktop.Notifications
  DisplayName (REG_SZ) = Pan
```

That is the entire production registration change. It uses a constant app
display label, reads it back, closes the handle, and avoids an extra write
when already correct. No HKLM, policy, notifications-permission key,
custom URI scheme, Start Menu shortcut, COM server, process token or desktop
policy is changed. This matches the minimal display metadata present during
the earlier visible probe. It is a **candidate**, not yet demonstrated to be
enough on the current Shell.

If registration fails, the sender stops with
`windows_toast_identity_registration_failed`, `stage=identity` and HRESULT;
no silent unregistered send/fallback occurs. Successful API response adds
`identityRegistered=true` while preserving `delivery=unknown`. Actual sender
logs retain identity/stage diagnostics. Payload bounds and dedup/deep-link
semantics are retained.

Microsoft's toolkit implementation registers unpackaged app display metadata
in HKCU AppUserModelId, separately from its COM activation setup:
[ToastNotificationManagerCompat source](https://github.com/CommunityToolkit/WindowsCommunityToolkit/blob/main/Microsoft.Toolkit.Uwp.Notifications/Toasts/Compat/ToastNotificationManagerCompat.cs).
The documented full protocol-only/stub-CLSID option also specifies identity
and shortcut metadata:
[Desktop toast activation options](https://learn.microsoft.com/pt-br/windows/apps/design/shell/tiles-and-notifications/toast-desktop-apps).
Those references justify investigating missing identity; they do not prove
that a DisplayName-only registration supplies every full protocol-activation
requirement. Shortcut/stub/COM expansion is not silently assumed or installed.

### Current tests and next human comparison

- Directed regression after the candidate: **95 passed, 0 failed, 0 skipped**,
  one existing pydantic-settings warning. See `identity-unit.xml`; same six
  test files/isolated command as above, replacing the JUnit output filename.
  Added checks cover app-scoped registration/order, explicit registration
  failure and the fact that registration is not display proof. XML/command
  budgets still pass with the added registration fragment.
- `identity_roundtrip.py` executed the **actual source registration fragment**
  twice in real inbox PowerShell under a random task-owned AUMID, without
  sending any notification. Both runs returned identityRegistered=true;
  readback was Pan; exact owned key was removed. Production identity was
  absent both before and after. Evidence: `identity-roundtrip.json`.
- No visible probe was sent autonomously after the user's failure. MA should
  arrange the user observation and rerun the same `visible_check.py --send`
  command. It now registers only the production app display metadata before
  posting the same title/Tag/Group. Observe banner and center independently,
  record the `identityRegistered` result, then use `--cleanup` for that exact
  diagnostic item. The app registration is persistent app metadata, separate
  from the diagnostic item; retain it until the comparison is resolved.
- Native click remains separately unverified; this visibility-only test has
  no launch target. Do not infer native-click success from a positive banner.

Do not accept this candidate or move main/practical until the new human
banner/center comparison succeeds and MA reviews its scope.

## User observed the registration candidate's banner

The next current user reply confirms that the 23:48:02 probe appeared, but was
too brief to click. `visible-seen.json` records banner=seen, center=not
confirmed and click=not attempted. The original 23:42:55 failure is retained
separately. These results support the missing app display identity as a real
visibility cause; the full user acceptance requirement is not yet complete.

The source's completion XML has an `activationType=protocol` action labelled
"打开会话", with the full send-time validated loopback Session URL. That same
button is carried by the toast for both banner and center presentation. The
current implementation does **not** set root `activationType=protocol`, so
clicking the notification body should not be advertised as a supported target.
The visible-only probe had neither a launch URL nor this button.

For the next human-controlled test, the probe now accepts `--long`,
`--origin <owned-loopback-origin>` and `--session <real-owned-session-id>`.
The latter two must be paired; origin validation reuses completion_deep_link
and rejects absent/8767/8768 ports. MA must own and verify that isolated runtime
and Session before use. `--long` requests the XML long duration solely for that
test; actual timing remains Windows-controlled and production defaults are
unchanged. Duration metadata is included in the existing XML byte budget.

1. MA arranges the user observation, then sends with all three options. User
   clicks **打开会话** on the banner and MA verifies the exact owned Session.
2. MA resends the same owned diagnostic after user readiness. User lets the
   banner expire, finds only this item in the notification center, then clicks
   its **打开会话** button. MA independently verifies the same Session again.
3. MA uses the probe's `--cleanup` after both observations and closes only its
   owned runtime. No click probe was sent by this TA in this follow-up.

Directed regression after adding the optional duration and probe arguments:
**96 passed**, zero failures/skips, one existing warning, recorded in
`click-ready-unit.xml`. The new case locks long-duration XML budget and the
complete button target. `visible_check.py --help` also passes. These checks
do not prove OS duration, center UI or native clicking.

## Real owned click runtime, build and Oct 18 center-click correction

Runtime is intentionally **left running for MA's human acceptance**, not
waiting for terminal Job notification before delivering readiness:

- Origin: `http://127.0.0.1:52389`.
- Real Session: `ses_054cea78b612e558`, name
  `OWNED Toast click acceptance 20261017`.
- Deep link: `http://127.0.0.1:52389/react/?session=ses_054cea78b612e558`.
- PID **43372**, created UTC **2026-10-17T15:54:38.372550+00:00**,
  raw creation FILETIME **134367260783725499**.
- Hidden owned Job **job_fae467053d20b479dfda26f8**, targeting this TA Session
  `ses_3f87f64dbb736845`.
- Command: existing Pan venv Python + absolute
  `audit/windows-toast-delivery/owned_runtime.py --serve --runtime`
  `D:/project/pan-worktrees/windows-toast-delivery-20261017/audit/windows-toast-delivery/runtime-click-20261017-native1`.
- Config: that runtime directory's `config.json`; data: its `data/` subtree.
  Session/config/Job/scheduler/terminal/workspace/reminder paths are explicitly
  isolated. No Session queue or external provider was started. QQ, WeChat,
  scheduler and network-exposure autostart are off in the owned config.
- Actual startup executes **main.py as __main__**, with real Uvicorn and
  production lifespan. No HTTP stub/test injection endpoint serves the page.
  Paths are substituted before startup following the existing isolated server
  pattern. Hidden Job launch is real; external thread-desktop inspection was
  denied with WinError 5, preserved in `runtime-context.json`, so do not claim
  a measured desktop name for this particular server.

The first attempt to reuse canonical dist showed a stale bundle: sidebar text
matched, but screenshot review found the main area unselected. That false
preflight is explicitly invalidated in `runtime-browser-prebuild.json` and
`runtime-session-prebuild.png`. Canonical dependencies then failed a build
because @xterm modules were absent. The task's dependency junction was safely
repointed to existing complete dependencies in
`D:/project/pan-worktrees/cbc-worker-tui-settings-verify-20261017/packages/web/node_modules`
after verifying the lockfile hash matched. **No installation or canonical
mutation** occurred. Task-tree `pnpm build` then passed TypeScript, Vite and
**66 compressed asset** verification; ordinary chunk warnings remain.

Current task-built dist is a real local directory. The running static server
serves that path without a restart. Health/Session/deep-link HTTP checks passed.
Owned headless Chromium preflight then checked **main**, not sidebar/body,
for the selected Session name and unique historical message. Screenshot review
confirms the actual conversation; zero page errors. See `runtime-browser.json`,
`runtime-ready.json`, `runtime-session-page.png`. This is browser/deep-link
preflight, not native clicking. Chrome MCP was unavailable at its configured
9222 debugger endpoint, so existing Playwright Chromium was used in an owned
headless context without controlling user tabs.

### Center failure evidence and source diagnosis

MA relayed the current user statement: "屏幕中直接弹出的可以跳转，但是通知中心的不行，补这一部分。"
This establishes **banner navigation passed / center navigation failed**.
It does not distinguish center body from an expanded action button.
`center-before-fix.json` preserves the owned access log and last sender receipt.
The real Edge request at **15:58:07.855 UTC** reached the exact Session link.
The retained last send receipt is **15:58:37.993 UTC**, later than that request;
we therefore do not fabricate click/send pairing. No matching notification
remained at snapshot, so the item's exact lifetime cannot be reconstructed
from current storage. No new notification was sent by this TA.

Source confirms **there is no short-lived Activated handler**: the helper calls
Show and exits; it neither registers an event handler nor runs a message loop.
Instead a concrete activation mismatch remained in serialized XML:

```
Before: <toast launch="http://127.0.0.1:52389/react/?session=...">
         <action activationType="protocol" arguments="the same full URL" .../>
After:  <toast activationType="protocol" launch="the same full URL">
         <action activationType="protocol" arguments="the same full URL" .../>
```

Without the root activation type, body clicks use app activation, for which
this sender has no COM activator. A collapsed center item commonly exposes the
body as the click surface. The button had protocol activation already; no
evidence proves that a distinct expanded-button failure has the same cause.
The fix makes **both body and button** Shell-owned protocol targets using the
identical validated, send-time full URL, requiring neither a running helper nor
a custom protocol/browser choice override. Root protocol activation is the
same construction as Microsoft's toolkit SetProtocolActivation:
[toolkit source](https://github.com/CommunityToolkit/WindowsCommunityToolkit/blob/main/Microsoft.Toolkit.Uwp.Notifications/Toasts/Builder/ToastContentBuilder.cs),
[content schema](https://learn.microsoft.com/windows/apps/design/shell/tiles-and-notifications/toast-schema).

No extra registration is added in this iteration. Current display registration
remains HKCU DisplayName only. Standard stub CLSID/shortcut or COM activation
remains a possible follow-up if the explicit center protocol paths still fail;
this patch does not claim that a body fix establishes every persistent Shell
activation prerequisite. It does not add a 60-second helper or alter expiry.

Directed regressions: **97 passed**, zero failed/skipped, one existing warning
(`center-unit.xml`). Added checks parse root and action activation semantics,
require exact same Session URL, reject dependence on callbacks/waits/expiry,
and preserve plain reminder behavior and the complete XML payload budget.
Those are source/contract checks, not actual post-exit OS-click proof.

### MA's next center acceptance and owned shutdown

Before sending, verify PID/FILETIME/command/listener and Session readiness:

```powershell
$py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
$rt = 'D:/project/pan-worktrees/windows-toast-delivery-20261017/audit/windows-toast-delivery/runtime-click-20261017-native1'
& $py audit/windows-toast-delivery/owned_runtime.py --check --runtime $rt
```

When the user is ready, MA sends one new notification with the current source:

```powershell
& $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/visible_check.py --send --long --origin http://127.0.0.1:52389 --session ses_054cea78b612e558
```

After helper exit and banner expiry, user opens Win+N, clicks the notification
**body**, and verifies the owned conversation. Send a separate marked owned
notification only with user readiness to test the **expanded button** as well.
For durable behavior, test after a meaningful delay beyond 60 seconds; record
each send/click time and preserve each receipt before reusing the probe file.
Only actual UI outcome plus correlated access/session evidence closes this.

After acceptance, remove only the diagnostic item and gracefully stop the
identity-verified runtime:

```powershell
& $py scripts/run_tests_isolated.py -- $py audit/windows-toast-delivery/visible_check.py --cleanup
& $py audit/windows-toast-delivery/owned_runtime.py --stop --runtime $rt
```

`--stop` verifies PID creation FILETIME, command markers and listener, then
calls the production `/api/internal/main/shutdown` on **52389 only**. Check the
owned Job terminal notice and closed port afterward; cancellation by owned Job
ID is the fallback only if graceful shutdown fails. Do not close this runtime
before the scheduled human test. Native center clicking remains **pending**.
main/practical/push/release/restart boundaries remain unchanged.

## Final acceptance and cleanup, 2026-10-18

MA's current feedback: user said **"成功了"**, specifically for the last resend
and the instruction to wait 60 seconds, open Win+N and click the notification
body. This closes the delayed center-body navigation case. Banner-button
navigation passed earlier. **Center expanded-button navigation and a fully
closed browser cold start remain unverified independently.** No screenshot of
the native center or mouse-click timestamp was collected.

Evidence is preserved in `final-acceptance.json`, `final-owned-access.jsonl`,
the current `visible-send.json` and `visible-cleanup.json`:

| Fact | Timestamp UTC / value |
| --- | --- |
| Last send receipt, identity registered | 2026-10-17T16:04:43.161983+00:00 |
| Non-preflight default Edge browser request to exact owned Session | 2026-10-17T16:06:13.059021+00:00 |
| Receipt-to-HTTP-arrival interval | **89.897038 seconds** |
| Target | http://127.0.0.1:52389/react/?session=ses_054cea78b612e558 |
| User result via MA | Delayed center-body navigation succeeded |

The measured interval is **not** a direct measurement of click time, a claim
that the user waited exactly 60 seconds, or proof of indefinite multi-day
retention. It does show this request occurred well after the one-shot helper
returned. Source has no activation callback or event loop, and body/button
carry native protocol URLs. No long-lived sender was introduced.

After preserving the evidence, exact History.Remove deleted only
`Pan.Desktop.Notifications / Pan-owned-visible / owned-visible-20261017`.
Read-only exact-tag verification found **0** remaining owned diagnostic items.
The product's HKCU AppUserModelId DisplayName registration is **retained as Pan**;
no registry cleanup removes this prerequisite.

Before shutdown, `owned_runtime.py --check` again verified PID 43372, creation
FILETIME 134367260783725499, command markers and the owned 52389 listener.
`--stop` used that verified identity and called the real production graceful
shutdown endpoint, which returned HTTP 200 / stopping. The server wrote its
terminal record at **16:06:56.785132 UTC**. Subsequent checks confirmed port
52389 closed and PID 43372 absent. Owned Job
`job_fae467053d20b479dfda26f8` is **completed, exitCode=0**, not cancelled.
See `final-cleanup.json` and `final-job.json`. Runtime is no longer available;
the former click link is retained for evidence, not a live acceptance target.

Latest main remains `e51b4ba50e22db464b8d45c5f3f1f2d4d99b5d4c`, an ancestor
of the task branch. There is no new main combination to merge or retest. No
implementation source changed during this cleanup, so the **97 passed**
directed suite and successful task-tree frontend build remain applicable;
neither was mechanically rerun. Final changes preserve acceptance, cleanup
and current user-facing documentation only. MA independently reviews and
performs the authorized FF/practical build; TA has not modified either tree,
pushed, released or restarted a protected service.
