# Production Web button and foreground-focus follow-up

This supplements commit `8ddb45ed7ff07c94c6a8313f5c97ee23651da92b`. **No product implementation was changed in this follow-up.** The native helper is byte-equivalent to that commit after normalizing Git LF / Windows CRLF; see [source check](source-check.json).

MA corrected two earlier conclusions:
- TA must not move main/practical; MA may fast-forward after acceptance. `candidate-only` was not an additional user instruction.
- A background route probe is not evidence that a foreground user's Web click must fail. The earlier report's bridge-necessity inference is withdrawn.

## Actual measured paths

Foreign foreground captions are redacted in JSON; HWND/PID/class and focus facts are preserved. [Final cleanup recheck](cleanup-check.json) confirms every recorded owned port is closed and no recorded harness remains active.

All instances used independent random loopback ports, new temporary configuration/data and a fresh Edge browser profile. No 8767/8768 requests, user Session operations, service restarts, provider runs, main/practical changes, installs, extensions or registry changes.

The harness serves the **real production React build and full product FastAPI HTTP/WS route table**, using the repository E2E isolation fixture, a single owned Session and real attachment endpoints. Startup service loops/lifespan are disabled; an empty CharacterManager is initialized for the production panel catalog. This is a production UI/button/HTTP/helper/confirmation test, not a full Pan lifecycle/Worker test. Missing-Worker native-control requests return expected 503; no Worker is started.

Browser navigation and initial modal opening use Playwright. For the acceptance path, an OS mouse event actually hits the production `Open server file picker` button. The harness first prepares **only its own isolated browser** through ordinary accessibility focus and a scoped title-bar mouse click; verifies its OS HWND is foreground; then validates WindowFromPoint against that owned HWND before the button mouse click. GetLastInputInfo advances and the DOM click has `isTrusted=true`. These are automated desktop mouse inputs, not a claim that a human clicked. No simulated keys or input-thread binding is used. No automation activates the native picker before measuring its focus.

| Run | Input / source | Result |
| --- | --- | --- |
| [Background browser control](before/result.json) | Playwright/CDP trusted click, but OS foreground was another Edge process | Both selections functioned, but focus requests were refused. Invalid proxy for a foreground user click. |
| [Original helper comparison](mouse-original/result.json) | Same foreground OS-mouse path; exact helper from baseline `0a2e5f04`; original bytes restored after run | Cancel and select both focused; selected path and Web confirmation worked. The original user's no-popup problem was **not reproduced** on this path. |
| [First candidate mouse run](mouse-actual-before/result.json) | Unmodified `8ddb45ed`, foreground OS mouse | Cancel and select both focused, both API diagnostics granted. |
| [Instrumented candidate](mouse-trace/result.json) | Temporary diagnostics, otherwise same behavior; original bytes restored | Owner Show acquired foreground before the native event hook fired. Both dialogs were already foreground when the explicit SetForegroundWindow request ran; both stayed focused. |
| [Controlled late owner](mouse-late-owner/result.json) | Diagnostic candidate with **test-only 3-second delay** before owner Show | Both focused. This did not support the hypothesis that this preparation delay alone explains rejection. No product reorder was implemented. |
| [Final uninstrumented candidate](mouse-final/result.json) | Exact `8ddb45ed`, foreground OS mouse; samples at 0.4 and 1.6 seconds | Both focused and non-TopMost; ordinary browser reactivation afterwards succeeded; real selection/cancellation/confirmation and HTTP responsiveness passed. |

Final run: host PID and temporary profile are in the raw JSON; port **58346**, test browser PID **19100**, desktop Session **1** for server and helpers. Before each click and at helper launch, OS foreground was the owned browser (HWND **5638346**). The real HTTP middleware recorded request entry before product Popen; the helper parent was the test server, not the browser. No request/response mocks or provider were involved.

- Cancel: helper PID **19216**, dialog HWND **658112**; foreground and keyboard-focus root both **658112** at both sample intervals; `topmost=false`; helper reported `foreground-granted`; HTTP returned cancelled.
- Select: helper PID **22564**, dialog HWND **4655356**; foreground and keyboard-focus root both **4655356** at both sample intervals; `topmost=false`; helper reported `foreground-granted`; exact owned Unicode fixture path returned.
- Visible native owner and dialog both have WS_EX_TOPMOST unset. Internal hidden shell tooltip/autosuggest windows are not treated as dialog topmost state.
- After measuring automatic focus, standard test-browser accessibility activation plus a normal scoped mouse click restored browser HWND **5638346**. This demonstrates the normal window can lose focus; it is **not an actual taskbar-click restoration test**.
- Selection was staged in the real Web modal. No from-server-file request occurred until clicking `Confirm selected attachments`. Real confirmation registered the original file; bytes were unchanged and no fixture copy appeared in attachment storage.
- Independent production summary HTTP requests returned 200 while dialogs waited. Both helper processes exited, host thread stopped, and the owned port closed.

[Web before](mouse-final/web-before.png), [Web after](mouse-final/web-after.png), [final request log](mouse-final-run.log). Web screenshots are not foreground evidence. Foreground HWND, keyboard-focus root and topmost facts in the raw JSON are the evidence. No PrintWindow screenshot is used to infer focus.

## Startup, owner and hook assessment

The chain is browser mouse input -> real HTTP -> existing Python request handler/to_thread owner -> CREATE_NO_WINDOW PowerShell, STA -> normal WinForms owner -> common file dialog. The event hook is scoped to the helper PID, requires its own owner and #32770 window, and is released with the owner. Diagnostic output shows that in the successful path the helper's normal owner Show had already obtained foreground, followed by the common dialog, before the explicit hook request.

Microsoft's [SetForegroundWindow rules](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setforegroundwindow) distinguish foreground ownership, last input, startup lineage and OS policy. The local HTTP request itself is not a documented transfer of eligibility. **Measurements show actual clicks can succeed; we do not claim HTTP confers a guarantee.** The [out-of-context WinEvent hook documentation](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwineventhook) says delivery is asynchronous on the registering thread's message loop. A single early visible-window sample cannot establish that the activation callback has completed.

No evidence justified an extension, native foreground bridge, elevated/UIAccess process, TopMost, AttachThreadInput, synthetic keyboard input or foreground-policy change. No such feature was added. Owner-first reordering was considered, then rejected as unsupported after the controlled delay comparison still succeeded.

## Reproduction commands

From this worktree root, each normal run was expected below three minutes and uses only its own processes/profile:

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-web-focus-20261021/web_probe.py mouse-final
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-web-focus-20261021/original_probe.py
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-web-focus-20261021/trace_probe.py trace
D:/project/Pan-main/.venv/Scripts/python.exe evidence/native-picker-web-focus-20261021/trace_probe.py late-owner
```

`original_probe.py` and `trace_probe.py` temporarily replace **only this isolated tree's helper**, run the measured UI path, and restore its exact original bytes in finally. Do not run them concurrently. The original helper is loaded from Git, not reconstructed. The test harness's mouse calls exist only under evidence; product code contains no test input generation.

## Limits and failed attempts retained

- One earlier uninstrumented candidate attempt sampled at 0.4 seconds while the **owned browser still held foreground**. It aborted before collecting the helper's activation result or a later sample. Its raw result was overwritten when the same `mouse-final` output directory was reused; [historical observation](historical-observations.md) preserves the facts copied from this conversation's tool output. It must not be represented as a confirmed persistent OS denial or omitted from the assessment. Later final samples at 0.4 and 1.6 seconds passed, but do not erase that observation.
- `foreground-before`, `mouse-before`, `mouse-focused-before` contain harness preparation failures (covered target, nonfocusable top-level UIA element, emulated-viewport coordinate mismatch). They launched no native picker and are not product failures or acceptance results. The corrected harness uses a real, non-emulated browser viewport and verified target coordinates.
- A prior diagnostic attempt aborted on an ordinary browser-switch target being covered. Its helper exited and port closed, but its Uvicorn thread did not finish promptly. Only the exact report PID, verified web_probe argv and trace_probe ancestry were terminated; the parent restored the helper bytes. `mouse-trace/external-cleanup.json` records that **earlier** cleanup PID, not the later successful trace run's PID. The first `foreground-before` preparation failure had the same lingering-thread issue without ever starting a product helper; its verified PID was also terminated and recorded in that directory's external-cleanup.json. The harness now has a bounded force_exit fallback for its own server.
- A Python 3.14/Playwright pipe cleanup emitted WinError 10054 in some intermediate attempts. The final acceptance run exited 0 and recorded all owned resources stopped.
- Physical human click acceptance, actual taskbar-button restoration, locked/noninteractive/remote desktop behavior and deployed backend freshness remain unverified. Protected services were deliberately not queried.
- Because the original helper also passed the actual foreground mouse path, this follow-up **does not prove the original deployment report's root cause**, nor claim the hook caused all successes. The actual foreground-click requirement passes the final measured cases; broader or intermittent-focus acceptance remains for MA to assess from all evidence.

No product files changed in this follow-up, so the prior 8ddb45ed targeted results remain applicable without new broad runs: backend 50 passed; frontend 90 passed / 1 existing skipped; targeted lint, typecheck, build and 74 compressed assets passed. This follow-up additionally runs script compileall, source-byte restoration checks and Git diff checks. These are separate from the actual browser/OS observations above.
