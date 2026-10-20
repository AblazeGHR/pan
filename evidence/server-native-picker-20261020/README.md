# Server native attachment picker delivery

Baseline: `eea3a1b8de745434922a7d9ddd8ddd273fa2bc06`; isolated branch `feature/server-native-picker-20261020`.

## Behavior and boundaries

- Original attachment modal now offers `使用原生文件选择器`. Native results are staged as a path list; `确认添加所选附件` invokes existing registration, once per file. No selection API registration/upload/send or byte copying.
- Existing DirectoryInput browsing, manual file selection and its registration behavior remain available during native requests. Manual navigation/selection invalidates the pending result. Request identity, unmount and Session identity prevent late results entering a later window. Repeated clicks cannot launch duplicate native requests.
- Windows helper uses fixed, repository-owned PowerShell script + STA WinForms OpenFileDialog, multi-select and RestoreDirectory. Inputs travel as JSON stdin, never interpolated script/command content. CREATE_NO_WINDOW suppresses the helper console.
- One owned helper per service process; 90-second timeout, 5-second cleanup budget, nonblocking request gate and thread-owned process communication. Initial-directory filesystem probes are inside the killable helper, including UNC probes. Cleanup uncertainty retains ownership/gate. Lifespan teardown and atexit kill only the owned helper.
- Unsupported OS/no accessible input desktop returns unavailable. Cancellation/failure/timeout/busy/unavailable all produce Web feedback and preserve browser fallback. Browser HTTP deadline is 100 seconds.

## Executed validation (all local, no shared service requests)

Python: `D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_native_file_picker.py tests/test_web_directory_picker.py tests/test_attachment_parts_protocol.py -o addopts='' -q` -> **50 passed, exit 0**.

Frontend (cwd `packages/web`): `node node_modules/vitest/vitest.mjs run src/components/chat/ServerAttachmentPicker.test.tsx src/components/chat/InputRow.test.tsx` -> **81 passed, exit 0**. Includes actual InputRow native multi-selection staging/confirmation and the existing attachment/queue regression tests; component tests cover fallback while pending, repeated clicks, manual override, close/reopen, Session switch, cancellation and failure statuses.

`node node_modules/eslint/bin/eslint.js src/components/chat/ServerAttachmentPicker.tsx src/components/chat/ServerAttachmentPicker.test.tsx src/components/chat/InputRow.tsx src/components/chat/InputRow.test.tsx src/services/api.ts` -> **exit 0**.

Standard `node node_modules/typescript/bin/tsc -b` failed because shared dependencies lack xterm packages (TS2307 and consequent type errors in pre-existing terminal components). No shared dependencies were changed. Read-only xterm source: `D:/project/pan-worktrees/composer-url-node-20261017-webverify/node_modules/@xterm/{xterm,addon-fit}`.

With temporary local validation configs mapping only these two packages to that overlay: `node node_modules/typescript/bin/tsc -p .native-validation.tsconfig.json` -> **exit 0**; `node node_modules/vite/bin/vite.js build --config .native-validation.vite.config.ts` -> **exit 0**, 2503 modules, 10.32s; `node e2e/verify-precompressed-assets.mjs` -> **72 compressed assets verified, exit 0**. Existing dynamic/static router import and large-chunk warnings remain. Temporary validation configs were removed before commit. The first commit hook also encountered the same missing shared xterm dependencies; the successful commit runs the unchanged hook with the same read-only xterm path mappings temporarily applied to this isolated tree's tsconfig.app.json, then restores the original config without staging it. Reproduce by extending tsconfig.app.json with absolute paths for those two packages, and merging the same aliases into vite.config.ts; preserve all other original options.

`git diff --check` -> **exit 0**.

## Independent real Windows / HTTP evidence

Run: `D:/project/Pan-main/.venv/Scripts/python.exe evidence/server-native-picker-20261020/real_native_probe.py` -> **exit 0**.

[Raw result](real-native-result.json), [actual dialog rendering](real-dialog.png), [owned-dialog automation](real-dialog-select.ps1).

Final run: route host PID 37568, independent loopback port 49451, temporary profile `C:/Users/14709/AppData/Local/Temp/pan-native-picker-jy4jdf8z`. This intentionally small FastAPI host mounts the actual product native-picker/registration handlers, not the full Pan lifespan/Worker runtime. No request to 8767/8768.

- Actual PowerShell helper PID 25376 created `Select server attachments`, native class #32770; UI Automation reported IsOffscreen=false. PrintWindow returned true and captured the real dialog contents (inspected visually). This is a window rendering capture, not a claim that the dialog was foreground on the desktop.
- Real Win32 control interaction selected the owned `native-附件.txt` fixture and returned its exact original path. Before confirmation no attachment directory existed. Existing confirmation endpoint registered source=server_file pointing to that fixture; fixture bytes unchanged and no copied file in attachment storage.
- Second actual helper PID 22080 was closed through its own window; API returned cancelled with no paths.
- Independent HTTP identity requests returned 200 while each dialog waited, proving this route host's event loop stayed responsive.
- Probe exited and verified its route host thread stopped. Subsequent OS inspection found no listeners on its probe ports or remaining native_file_dialog.ps1 helper processes.

An initial probe could render the dialog but its UIA Value/Invoke patterns were unsupported; it timed out cleanly. The successful probe used scoped Win32 messages to the real dialog controls. Foreground activation was refused (`foregroundRequested=false`); automatic topmost presentation and human click acceptance remain unverified. Real native multi-selection was not separately automated (multi-selection is covered by backend/helper configuration and frontend regression).

## Remaining acceptance boundaries

No full-browser or mobile OS E2E, no human selection, locked-desktop/service-account scenario, non-Windows runtime, or full Pan shutdown E2E was exercised. No-desktop, concurrency, timeout and shutdown ownership are covered by injected regression tests; those are not OS simulation substitutes for the real window/selection evidence above. MA acceptance is pending. No push, merge/release, main/practical movement or existing service restart.
