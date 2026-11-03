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

Platform hand-off is a fixed argv with no shell:

- Windows: Explorer `explorer.exe /select, <file>`; a nonzero Explorer exit status
  is tolerated because Explorer reports one even when the window opens. Missing
  Explorer returns `501`.
- macOS: `open -R <file>`.
- Linux: `xdg-open <parent-dir>` because there is no portable “select this file”
  primitive; no `DISPLAY` / `WAYLAND_DISPLAY` or no `xdg-open` returns `501`.
- Opener spawn failure is `500`; a still-running opener is reaped out of band and
  does not become a false failure.

`GET /api/platform` is unchanged. These actions are deliberately not a static
capability: desktop/opener availability is probed per request, so the
cross-platform plan documents the `501`/`500` contract instead of advertising a
platform boolean that would be wrong on a headless server.

## Validation

- `red-backend.txt`: new suite failed before implementation (missing
  `api_editor_reveal_external` / `_external_reveal_argv`; 11 failures + fixture
  errors).
- `red-frontend.txt`: 7 failed / 29 passed before the UI existed (missing reveal
  button, dirty action still disabled).
- `backend-all.txt`: 66 passed across reveal/open containment, symlink and
  Workspace cases, platform argv/failure codes and the unchanged platform API;
  exit 0.
- `backend-web.txt`: 5 passed for web frontend/logging; exit 0.
- `frontend-editor-all.txt`: 51 passed across all editor components; exit 0.
- `build.txt`: `pnpm build` exit 0 (TypeScript + Vite + 90 compressed assets).
- `browser-first.txt`, `browser-final.txt`: fresh owned production FastAPI + dist +
  headless Chromium, dynamic ports 62026 / 57433, disposable runtime, exit 0 and
  owned-listener cleanup. Real product HTTP/UI: open + reveal notes; binary and
  large failed tabs keep both enabled and call the endpoints; dirty buffer opens
  the disk version with the explicit toast and the disk file stays byte-identical;
  traversal/missing/directory/unknown-Session negative controls stay 403/404/400/400
  and never reach the opener; 390px bar has no horizontal overflow.
- OS hand-off in the browser suite is a recording boundary owned by the fixture
  (`packages/web/e2e/editor-system-open.server.py`), so no real editor or Explorer
  window was launched. The real argv construction and failure paths are covered by
  `tests/test_editor_reveal_external.py`.

## Limits and handoff

Candidate validation only; not MA acceptance, remote CI or deployment. Windows
local only: macOS/Linux argv has unit evidence but no target-platform runtime
evidence. A real Explorer window was not opened; Explorer's `/select,` interaction
was verified only at the captured-argv boundary. Tests used the isolated
`D:/project/Pan-main/.venv/Scripts/python.exe` and this worktree's own pnpm
dependencies. No real `data/`, profile, 8767/8768 service, push, protected branch
or service restart was touched.
