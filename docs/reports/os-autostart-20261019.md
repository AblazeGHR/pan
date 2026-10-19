# Optional Pan login startup — 2026-10-19

Implementation is isolated on `feature/os-autostart-20261019`, based on `e4078c186c7e89afc007fd28f24ec6973d2ef117`. This is a TA delivery, not MA acceptance or integration. It is outside the running 0.3.4 release gate.

## Behavior and native formats

App Settings → General → Login startup defaults to Off when no owned native entry exists. Viewing Settings never creates an entry. The UI explains current-user login startup, shows installation/data/entry paths, and can refresh native state. There is no mirrored config boolean: native registration is the sole setting store, so config-save failure cannot leave a contradictory preference. An API write returns success only after native read-back; a failed/uncertain UI write re-reads native state. Unknown state disables mutation and shows an error.

- Windows: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `Pan.Login`, `REG_SZ`. The command uses an absolute sibling `pythonw.exe` and installed `.pyw` entry, with `subprocess.list2cmdline` quoting. Over-260-character commands are rejected explicitly. StartupApproved disable state is read conservatively and never rewritten. Matching HKLM entries conflict. No administrator or system service is installed.
- macOS: `~/Library/LaunchAgents/Pan.Login.plist`, `ProgramArguments` array, absolute `WorkingDirectory`, `RunAtLoad=true`, `KeepAlive=false`. `launchctl print-disabled gui/<uid>` is queried read-only; disabled overrides prevent a false On state, and query/unknown-format failures are explicit. This writes the next-login entry; enabling does not immediately bootstrap or start Pan.
- Linux: `$XDG_CONFIG_HOME/autostart/Pan.Login.desktop` (default `~/.config/autostart`), Type=Application, absolute quoted Exec, Path, Terminal=false, Hidden=false. Exec handles both desktop string escaping and Exec quoting/percent escaping. Availability requires an identified XDG desktop plus DISPLAY/Wayland; headless sessions return unsupported instead of installing a daemon. A same-name system XDG entry conflicts. No systemd service is created.

The fixed per-user identifier intentionally permits only one managed installation. Exact generated native content proves ownership; foreign, modified, corrupt, symlinked, other-interpreter or other-checkout entries are never adopted/overwritten/deleted. The current target and raw conflicting registration are shown. Switch to the owning installation to remove its entry before enabling another. Development/task branches and paths under `pan-worktrees` cannot register; main/practical or a non-Git installed distribution can. Disable can still remove an exactly owned entry after a branch change.

The one-shot `scripts/login_start.pyw` derives absolute root/data/cwd from its installed location, clears inherited Pan runtime identity/port/data overrides, pins the interpreter captured from the running service, logs to `<root>/data/logs/login-startup.log`, and delegates to `launcher.start_service`. The launcher receives only optional pinned-interpreter/hidden-window overrides; existing state/PID/listener duplicate checks, readiness, service/QQ/tunnel start and Worker recovery policies remain in their existing owners. Normal launcher calls retain their previous behavior. No shell, interactive PATH discovery, or resident helper is added to the registered startup command.

Concurrent Pan toggles use a per-user cross-process file lock and native revision CAS, including target identity. Stale tabs get 409; duplicate enable/disable is idempotent. The entry is rechecked immediately before mutation. Arbitrary external OS editors do not honor this lock; native entries are re-read before and after changes, but there is no claim of a cross-application transactional OS registry CAS.

## Official format references checked

- [Microsoft Run and RunOnce Registry Keys](https://learn.microsoft.com/en-us/windows/win32/setupapi/run-and-runonce-registry-keys): current-user login Run key and 260-character command limit; Run may be delayed by Windows.
- [Microsoft Configure Startup applications](https://support.microsoft.com/en-US/Windows/Experience/Startup-Boot/configure-startup-applications-in-windows): user versus all-user startup scope and native management.
- [Apple Creating Launch Daemons and Agents](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html) and [Apple launchd.plist source](https://github.com/apple-oss-distributions/launchd/blob/main/man/launchd.plist.5): user LaunchAgents directory, ProgramArguments, WorkingDirectory, RunAtLoad and KeepAlive. [Apple Developer Forums first-party discussion of launchctl override state](https://developer.apple.com/forums/thread/783433) confirms GUI/user disabled overrides; its output parser remains mock-tested on this Windows host.
- [freedesktop Autostart specification](https://specifications.freedesktop.org/autostart/latest/) and [Desktop Entry specification](https://specifications.freedesktop.org/desktop-entry/latest-single/): user autostart directory, system precedence/Hidden behavior, Exec quoting/field codes, Path and Terminal. This supports compliant desktop sessions; it does not claim headless boot support.

## Validation and boundaries

Final repeatable batch: `python scripts/validate_login_startup.py`; stage exit codes and logs are stored in `audit/os-autostart-20261019/validation.json`. Backend JUnit, frontend JUnit, browser result JSON and screenshots are stored alongside this report's linked evidence. Exact final counts are recorded after the batch.

Windows native tests use unique UUID values in a disposable `HKCU\Software\PanAutostartTests\...` namespace, not the product Run value. They register/read/enable/idempotently-enable/disable/read and remove the entire owned UUID key, verify foreign bytes remain unchanged, and use Windows CommandLineToArgvW for Chinese/space argv. A real pythonw process records GetConsoleWindow=0 / IsWindowVisible=false while invoking an owned launcher stub; launcher tests separately prove hidden override, pinned interpreter and original duplicate-start paths. This does not assert a real post-login launch or measure every possible third-party descendant window.

Chromium mounts the actual LoginStartupSettings component with the actual production GET/PUT handlers in an owned minimal FastAPI server (no Pan lifespan or Worker startup), backed by an owned Windows registry namespace. It exercises default Off/no-registration, enable read-back, reload truth, disable, injected permission failure plus state rollback, foreign conflict display/protection, and cleanup. AppSettingsModal integration is covered by its 50 existing tests plus the new section's focused interaction tests. Permissions failure is injected as PermissionError at the native write boundary; no user's registry ACL is altered.

Mac/Linux verification on this Windows host is format generation/parsing, native file read/write in temporary roots, explicit state/error cases and mocked OS capability/launchctl output only. Real platform login launch, OS-managed disable UI, real reboot/logout, production startup targets and production restart are deliberately untested. No request targets production 8767/8768 or their data. The full repository test suite and remote CI are not claimed.

Frontend validation uses a complete owned node_modules copy, with 1,319 junctions remapped only inside this worktree, copied xterm packages and local generated bin paths. No package install, manifest/lock change or canonical dependency write is used.

## Final batch result

Durable Job `job_d4565107ada53a77ace89f8d`, target/creator `ses_8ddfbe6cd2fc1dde`, completed with exit code 0; registry result was checked after terminal notification. No polling was used.

| Stage | Result | Evidence |
| --- | --- | --- |
| Backend directed regressions | 78 passed, 0 skipped, exit 0; includes 30 new login-startup tests | [JUnit](../../audit/os-autostart-20261019/backend.xml), [log](../../audit/os-autostart-20261019/backend.txt) |
| Frontend section + AppSettingsModal | 54 passed, 0 skipped, exit 0 | [JUnit](../../audit/os-autostart-20261019/frontend.xml), [log](../../audit/os-autostart-20261019/frontend.txt) |
| `pnpm build` | exit 0; tsc -b, Vite and 72 compressed production assets verified | [log](../../audit/os-autostart-20261019/build.txt) |
| Chromium + owned API/native registry | 7 checks passed, 0 browser page errors, exit 0 | [result](../../audit/os-autostart-20261019/result.json), [log](../../audit/os-autostart-20261019/chromium.txt), [failure-state screenshot](../../audit/os-autostart-20261019/permission-failure.png) |

The batch used API port 51623 and its owned Vite listener 5173. Both were absent on the final listener check. Native namespace `Software\PanAutostartTests\Pan.Test.cc1f1a24092b4fd0973e06943af9eadd` was verified absent by the browser harness after graceful API shutdown. Screenshots show the actual isolated section without the full application's styling/layout; they prove section interaction/state, not full-modal visual acceptance.

Backend warnings: the local interpreter lacks pytest-timeout (unknown pre-existing timeout config option), and FastAPI/Starlette emits an httpx deprecation warning. There were no skips. Build reports the existing large-chunk and mixed dynamic/static router import warnings; all build commands and compressed-asset verification passed.

Only this task branch is submitted. No main/practical integration, push, release, reboot/logout, production restart, or addition to the 0.3.4 release gate was performed. MA acceptance and the authorized main/practical fast-forward + practical build remain with MA.
