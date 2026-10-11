# Codex native-operation passthrough

Baseline: `b5cc14f85fa94ce7c6d2262692f9735c4ce9c450`.
Implementation is isolated in `codex-native-passthrough-20261011`; no provider
account, practical data, or running service is used for testing.

## Policy and UI

- App Settings → Adapter → Codex → **允许所有 Codex 原生操作**.
  This is a **current-Session** setting, not a global default; select an editable
  Codex Session first. Existing Session data defaults to restricted mode.
- Persist `adapter_config.native_passthrough`, accept only real boolean values.
  A live native Worker receives an acknowledged control message (including in
  Pan mode), without a new turn, mode switch, or restart. An offline/non-native
  Worker uses the setting when its native transport is next spawned.
- Save intent before delivery. Missing acknowledgement means **pending**, not
  confirmed. Retry targets the same Worker; persistence failure sends nothing.
  Spawn/runtime generation, authenticated private transport, control owner and
  explicit current-thread binding remain fenced.
- Restricted mode preserves the existing interaction whitelist. Passthrough
  mode forwards otherwise unknown/configuration RPCs without inventing their
  semantics. The known TUI bootstrap `thread/resume` remains a read-only bridge
  to the current loaded thread, not a reload of an executing Agent.
- Known events continue to use Pan's existing projection. Before an unknown
  operation or a known turn carrying setting overrides is sent, emit a static
  uncertainty event (including ambiguous delivery). No method/params/secrets
  are copied into this diagnostic. Even an upstream rejection conservatively
  retains the marker; Pan does not guess whether an unknown RPC changed state.
- Persist `native_settings_uncertain` and show a warning both beside the switch
  and in Session settings. Refresh, disabling the switch, control handoff and a
  failed restart do **not** clear it. Only the first init of a new Worker kernel
  clears it; a duplicate init on the same kernel does not.
- Clearing the warning at successful startup is the requested UI lifecycle,
  **not** proof that arbitrary global Codex settings have been restored or that
  Pan understood all unknown operations. The warning says global configuration
  can be modified. Official RPC semantics:
  [Codex App Server](https://learn.chatgpt.com/docs/app-server), `config/batchWrite`.

## Verification (each layer separate)

- Python: **145 passed**, zero skips, `python.xml`.
  Nine files: Codex adapter, control owner, passthrough, native completion,
  native transport, HTTP boundary, conversion, real kernel, resumed init.
  Run via `scripts/run_tests_isolated.py` and practical's Python interpreter.
- Real installed Codex/private RPC: temporary `CODEX_HOME`, no model turn.
  `config/batchWrite` is rejected by default (no config file created), forwarded
  after enabling (actual temporary `config.toml` updated), rejected again after
  disabling (file bytes unchanged); same live kernel. Owned resources closed.
- Two real wrapper/TUI kernel round trips use a local synthetic provider; policy
  toggles are acknowledged without starting a TUI or restarting the kernel.
  These are not external model/account or full browser-settings E2E claims.
- Frontend: **97 passed**, seven files, `frontend.xml`: switch, ownership store,
  App Settings, Session context/optimistic settings, TopBar, NativeTerminal
  restart. Lost-receipt reconciliation/uncertainty lifecycle are covered.
- Changed frontend files: ESLint zero errors. Four existing SettingsPopover
  hook warnings remain; no lint rules or assertions were suppressed.
- Feature-tree `pnpm build`: TypeScript/Vite/precompressed verification passed,
  **66** compressed assets. Existing large-chunk warning remains.
- Package/lock hashes matched the previous restart worktree before the marked
  validation dependency junction was created. Junction is removed after tests.

The first wider Python command had a nonexistent conversion test filename and
ran no tests; it was corrected to `test_codex_session_conversion.py` before the
145-case run. This is a command error, not a passing test run. The first Vitest
invocation found four files/80 tests; corrected filenames produced the final
seven-file/97-test run. JUnit files are generated test evidence, not fabricated
historical logs.

## Integration boundary

Fast-forward main and practical only after checking both target heads/status.
Preserve existing unrelated untracked docs/dependency backups. Build practical
frontend separately. No push or service restart is part of this change.
Already-running Pan/Worker processes must load the new backend/wrapper before
the live switch is usable. Real browser model/tier switching with this new
switch, all possible future Codex RPCs, and full repository regression remain
unverified; the tests above establish the specific policy and adjacent paths.
