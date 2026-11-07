# Editor file tab: system open + file-manager reveal

Base: `51db605cc8b3976f3ad0d6b4a4d8ccba9d319f68`
Branch: `fix/editor-system-open-20261104`

## Behavior and API

The Editor file top bar keeps the existing “用系统默认方式打开” action and adds
“在系统资源管理器中定位”. Both hand the **on-disk file** to the platform; neither
writes the editor buffer. With unsaved changes the open action stays enabled, the
title says it opens the disk version, and a success toast repeats that the buffer
was not written. Read-only failed previews (too large / binary) keep both actions;
attachment virtual tabs and closed Sessions keep them disabled with the same
reason copy.

Backend adds `POST /api/editor/reveal-external`. It shares the new
`_resolve_editor_file_target` helper with `/api/editor/open-external`, so both keep
the exact Editor containment: `..`, absolute paths, symlink targets and the
allowed Session-workdir/Workspace roots are resolved and checked the same way
(`403` outside roots, `404` missing, `400` directory/invalid, `400` no Session).
Requests contain a path only, never a command.

`GET /api/platform` gains `fileManager.reveal`. The capability keeps the existing
response shape and is **runtime-probed** through the same platform boundary used by
the request: Windows needs Explorer, macOS needs `open`, Linux needs `xdg-open` plus
`DISPLAY`/`WAYLAND_DISPLAY`; unknown platforms are false. A false probe means the
route returns `501` and the front end disables the reveal button with an explicit
platform-specific title. A missing/failed capability probe (old server, network
error) leaves the button available so the server's `501` still surfaces as a toast;
it never silently hides the action. The existing default-open action is unchanged
and not capability-gated.

Platform differences and command construction live in
`packages/core/platform/file_manager.py`; `server.py` only resolves the path, maps
errors to HTTP status and calls the platform function. Hand-off is fixed argv with
no shell:

- Windows: Explorer `explorer.exe /select, <file>`; a nonzero Explorer exit status
  is tolerated because Explorer reports one even when the window opens.
- macOS: `open -R <file>`.
- Linux: `xdg-open <parent-dir>` because there is no portable “select this file”
  primitive.
- Spawn failure is `500`; a still-running opener is reaped out of band and does not
  become a false failure.

## Validation

- `red-backend.txt`: new suite failed before implementation (missing
  `api_editor_reveal_external` / `_external_reveal_argv`; 11 failures + fixture
  errors).
- `red-frontend.txt`: 7 failed / 29 passed before the UI existed (missing reveal
  button, dirty action still disabled).
- `backend-capability.txt` (current): 69 passed across reveal/open containment,
  symlink and Workspace cases, runtime capability probing, platform argv/failure
  codes, route `501` when the probe has no opener, and the unchanged platform API;
  exit 0.
- `backend-web.txt`: 5 passed for web frontend/logging; exit 0.
- `frontend-capability.txt` (current): 55 passed across all editor components,
  including capability-false disabling on Windows/Linux, unknown-platform reason,
  and the unknown-probe fallback; exit 0.
- `build-capability.txt` (current): `pnpm build` exit 0 (TypeScript + Vite + 90
  compressed assets). `eslint-capability.txt`: 0 errors, one pre-existing fast-refresh warning.
- `browser-capability.txt` (current): fresh owned production FastAPI + dist +
  headless Chromium, dynamic port 56464, disposable runtime, exit 0 and
  owned-listener cleanup. Real product HTTP/UI: open + reveal notes; binary and
  large failed tabs keep both enabled and call the endpoints; dirty buffer opens
  the disk version with the explicit toast and the disk file stays byte-identical;
  capability forced false disables the reveal button, shows the reason, leaves
  open enabled and returns real `501`; capability restored re-enables it;
  traversal/missing/directory/unknown-Session negative controls stay 403/404/400/400
  and never reach the opener; 390px bar has no horizontal overflow.
- `explorer-select-native.txt` / `.json`: real Windows Explorer verification, not a
  mocked argv. Synthetic temp files under a directory containing spaces, Chinese
  and a comma (and file names with the same) were passed to the real platform
  function; Shell.Application reported the selected item for 3/3 cases and the
  synthetic windows were closed. No product data or user files were used.

`browser-debug.txt` is the one failed browser attempt before the fixture was fixed:
the fixture still patched the old server-local reveal function, so the real platform
function opened Explorer and the recording boundary saw nothing. It is kept as a
failed experiment; the fixture now patches `file_manager` and the rerun passed. The
two Explorer windows opened by that run were closed; no synthetic window remains.

OS hand-off in the final browser suite is a recording boundary owned by the fixture
(`packages/web/e2e/editor-system-open.server.py`), so no real editor or Explorer
window is launched by the suite itself.

## Limits and handoff

Candidate validation only; not MA acceptance, remote CI or deployment. Windows
local only: macOS/Linux argv and capability probes have unit evidence but no
target-platform runtime evidence; do not extrapolate parity. Tests used the
isolated `D:/project/Pan-main/.venv/Scripts/python.exe` and this worktree's own
pnpm dependencies. No real `data/`, profile, 8767/8768 service, push, protected
branch or service restart was touched.
