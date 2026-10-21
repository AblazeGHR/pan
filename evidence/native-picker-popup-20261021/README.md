# Native server attachment picker investigation and candidate

Worktree: `D:/project/pan-worktrees/native-picker-popup-20261021`
Branch: `fix/native-picker-popup-20261021`
Baseline: `0a2e5f04d13917cdcf9ff8175f96e97e1411bfbf`
Original implementation: `fc0c8d6f836509ced4784229b12aacda8dfbc4b5`

> Follow-up correction: this document records the earlier background route probe. It does not establish failure of a foreground user's Web click. The [production Web / native mouse follow-up](../native-picker-web-focus-20261021/README.md) records successful real foreground button paths, the original-helper comparison, and remaining limits. MA clarified that main/practical restrictions are this TA's delivery boundary, not a prohibition on MA fast-forward after acceptance.

**Earlier probe status:** foreground activation was refused in the two background cases below. Do not extrapolate those cases into a conclusion that a genuine foreground button click cannot focus the dialog.

## Proven defect before edits

The fresh baseline run used PID 32756, independent loopback port 57645 and a temporary profile. The actual product helper created a selectable dialog (HWND 526634), but `GetForegroundWindow` and `WindowFromPoint` at the dialog center both returned another HWND, 394370. This demonstrates a created but covered background dialog, rather than a failure to create a helper. The probe did not activate or restore any window. See [baseline result](baseline/real-native-result.json).

The old 20261020 PrintWindow screenshot is not used as proof of current visibility or focus. It already documented refused foreground activation.

## Final candidate behavior

- Fixed repository PowerShell helper, JSON stdin, STA and CREATE_NO_WINDOW launch remain unchanged. No shell interpolation, copied file bytes, new permissions, elevated process or system settings changes.
- A normal visible WinForms owner named `Pan server attachments` supplies a taskbar entry. It owns the common dialog and is disposed with it.
- A PID-scoped EVENT_OBJECT_SHOW hook observes the actual owned #32770 dialog, then makes one ordinary HWND_TOP / SetForegroundWindow request. HWND_TOP is not HWND_TOPMOST. No repeated focus timer, AttachThreadInput, synthetic input, foreground-lock modification or foreign-window activation.
- On refusal, FlashWindowEx requests taskbar notification. Hook/delegate, dialog and owner are released in finally. On timeout/shutdown the existing owned-process cleanup remains authoritative.
- English Web feedback describes the server desktop, 90-second bound, taskbar window, all failure statuses, possible stale backend, and manual browsing. Native selections still require the original Web confirmation.
- Python gate, shielded request ownership, 90-second production timeout, HTTP 100-second deadline, directory restrictions and attachment registration protocol are unchanged. Manual browsing and late-result / Session identity protections remain covered.

## Final actual Windows / HTTP run

Command (from repository root):

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-popup-20261021/probe.py event-focus
```

Exit 0. Host PID 14120, port 54459, temporary profile `C:/Users/14709/AppData/Local/Temp/pan-native-popup-ecy2b_tw`. This small isolated FastAPI route host mounts the actual native-picker and confirmation handlers. It does not run the full Pan lifespan or a Worker. It reads an absent configuration path in its own temporary root, clears inherited PAN variables before imports, and replaces Session/attachment storage with owned temporary state. No requests to 8767/8768 or real Sessions.

[Final raw result](event-focus/real-native-result.json), [dialog rendering capture](event-focus/real-dialog.png), [probe](probe.py), [PID-scoped automation](real-dialog-select.ps1).

- Real selection returned the exact Unicode fixture path. No registration existed before confirmation. Existing confirmation registered source=server_file pointing to the unchanged original; no file copy was stored.
- Real cancellation returned cancelled with no paths.
- Both visible dialog and visible owner have WS_EX_TOPMOST unset. Internal hidden tooltip/autosuggest windows are Windows shell implementation details; they are not the dialog or owner.
- Both cases recorded `pan-picker: foreground-denied`. Foreground remained 394370; the helper thread's focus HWND belonged to the dialog but the helper was NOT the foreground process. This is **not keyboard foreground focus success**. Center-point occlusion was also recorded. The final candidate does not guarantee that the dialog is above the browser when the OS refuses activation.
- Concurrent requests returned busy while each real dialog waited. Independent HTTP identity requests returned 200.
- Missing initial directory returned failed. A separate 6-second test timeout returned timeout; its owned helper exited. Production timeout remains 90 seconds.
- Real helpers exited, host thread stopped, port closed, and picker ownership cleared. No visible ConsoleWindowClass appeared among helper-owned windows. This enumeration plus CREATE_NO_WINDOW is evidence for hidden helper consoles, not proof about unrelated console hosts.
- PrintWindow captures dialog rendering, not desktop foreground. It is partially black/cropped under this desktop's rendering/DPI setup and must not be presented as proof of unobscured display. Win32 metadata is the visibility/focus evidence.

Microsoft documents that SetForegroundWindow can be refused even when eligibility conditions appear met, and recommends taskbar notification when another window is in use: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow . Those background observations alone do not prove that a native bridge is necessary; the follow-up actually measured successful foreground Web-button clicks. No restriction bypass was attempted.

## Validation

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-popup-20261021/validate.py
```

[Exact argv, cwd, duration and exit codes](validation/results.json); individual logs in `validation/` (trailing whitespace normalized for Git diff checks).

- Backend: isolated `tests/test_native_file_picker.py tests/test_web_directory_picker.py tests/test_attachment_parts_protocol.py -o addopts= -q`: **50 passed**, exit 0. Includes fixed helper arguments/STA/hidden console, selected/cancel/fail, unavailable, spawn failure, busy, timeout, cleanup uncertainty, HTTP cancellation, shutdown ownership, path-only selection and confirmation.
- Frontend: targeted ServerAttachmentPicker and InputRow: **90 passed, 1 existing skipped**, exit 0. Existing skipped test: `uses the CBC stream capability for direct Steer and hides it for native TUI/oneshot`; unchanged. Added waiting/taskbar and rejected-request fallback checks; retained multi-file confirmation, manual override, repeat click, close/reopen and Session switch cases.
- Targeted ESLint: exit 0.
- Standard `tsc -b`: exit 0, no overlay or config edits.
- Vite production build: exit 0, 2506 modules; existing mixed router import / large chunk warnings remain.
- Precompressed assets: **74 verified**, exit 0.
- git diff --check: exit 0.
- Canonical dependency junction prepared using `tools/prepare_validation_env.ps1`; no install or shared dependency mutation. No charged-model or full-suite runs. Each command/probe was expected below three minutes; no existing Pan service was used as a Job host.

## Classification and outstanding acceptance

| Situation | Evidence / current boundary |
| --- | --- |
| Dialog created but covered / unfocused | Fresh baseline and final real Win32 observations; reproduced |
| Missing owner/taskbar discoverability | Baseline used implicit WinForms owner; final explicit visible ordinary owner verified |
| Failure before helper creation | Injected spawn failure / unsupported desktop regression; not the cause of this baseline OS run |
| Invalid directory / busy / timeout / cancel | Targeted regressions and actual isolated HTTP/OS cases above |
| Noninteractive account, Session 0, locked desktop, remote desktop | Not exercised live; existing OpenInputDesktop gate is retained, not relaxed. Accessible input desktop alone cannot prove the user's browser is in the same interactive desktop |
| Current deployed backend missing endpoint / stale frontend | No protected-service requests were permitted, so unconfirmed. Client rejected-request feedback and fallback are tested |
| Guaranteed automatic focus without topmost | **Unmet on final OS run**; safe API request denied. Requires further design/authorization or foreground-process cooperation, not acceptance by screenshot |
| Full browser / mobile / full Pan shutdown / human click and focus | Not verified by this route harness or jsdom tests |

Intermediate directories are historical investigations, not final acceptance: `normal` records an earlier ordinary-window run that reached foreground before the final activation hook; `fixed` is the rejected topmost experiment; `final`, `focus`, `accepted`, and `candidate` are superseded trial runs, including a harness timing assertion failure. They cannot substitute for the final `event-focus` result. Automatic approval blocked deletion of these owned intermediate evidence directories with the reason `blocked by policy`; they are retained rather than bypassing that rejection.

No main/practical movement, push, release, existing-service requests/restart/kill or real Session changes. MA clarified that these are TA delivery boundaries; MA may apply the requested fast-forward strategy after acceptance. There is no additional user-imposed candidate-only restriction.
