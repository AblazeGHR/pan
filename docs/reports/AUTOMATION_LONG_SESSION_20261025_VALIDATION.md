# Automation long-session candidate validation

- Branch: `feature/automation-long-session-20261025`
- Base: `5d0c7f1f471e10d21c287b9820e99dd90276e5f9`
- Validation date: 2026-10-26 (Asia/Singapore)
- Delivery: one isolated fast-forward candidate commit. No main/practical advancement,
  push, service start/stop, or 8767/8768 operation.

## Result

Profile-wide sessionDays defaults to 0 and accepts 0/7/30/90. Cookie login is
HttpOnly/SameSite=Lax/Path=/, Secure on HTTPS; 0 has no Max-Age and a fixed 12h
server TTL. Positive values renew below half-period. Settings are in the existing
authorization.json; sessions.json stores hashed tokens, wall-clock expirations and
key revision, using atomic fsync/replace with 0600 creation/chmod. Global duration
changes update active server sessions and refresh each browser's Cookie policy at
its next authenticated request. Logout, password change and revoke invalidate the
corresponding sessions. Explicit Bearer clients remain compatible.

The startup session probe restores the React login without browser storage or a
JavaScript-readable Cookie. Shared protected request helpers use same-origin
credentials and clear the current login on 401. Login/logout gates, Settings,
Actions, Jobs, Locker and attachment guards recognize restored sessions.

## Verification

| Layer | Command / scope | Result |
|---|---|---|
| Backend contracts + ASGI | Isolated pytest command below | 128 passed, 0 failed, 0 skipped; exit 0 |
| Frontend component/store regression | Vitest command below | 147 passed in 10 files, 0 failed, 0 skipped; exit 0 |
| TypeScript | `pnpm exec tsc -b --force` in packages/web | exit 0 |
| Production build | `pnpm build` in packages/web | exit 0; 2554 modules; 90 compressed assets verified |
| Patch whitespace | `git diff --check` | exit 0 |

Backend command from the worktree root:

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_automation_sessions.py tests/test_automation_core.py tests/test_authorization_password.py tests/test_authorization_permissions.py tests/test_authorization_mcp.py tests/test_authorization_runtime.py tests/test_job_action_templates.py -q -o addopts= --tb=short
```

Frontend command from packages/web:

```powershell
pnpm exec vitest run src/stores/automationSession.test.tsx src/components/layout/AuthorizationSettings.test.tsx src/views/AutomationView.test.tsx src/views/LockerView.test.tsx src/stores/lockerUnreadStore.test.tsx src/views/ActionsView.test.tsx src/views/JobsView.schedule-shapes.test.tsx src/views/JobsView.bulk-search.test.tsx src/services/api.test.ts src/services/api.legacyJobs.test.ts
```

New backend coverage includes cross-instance restart, expired-session pruning,
hashed-only storage, bounded sliding writes, atomic-replace failure, 0-day fixed
TTL, global settings across two browsers, Cookie flags on HTTP/HTTPS, shared
principal gates, POST/PUT/PATCH/DELETE Origin refusal, invalid settings, logout,
password revocation and explicit revoke. New frontend coverage includes startup
restore/login-form state, both request helpers without Bearer, 401, pending-probe
logout fencing, Cookie logout, and server-backed Settings reload.

Legacy anonymous/Bearer fixtures explicitly clear httpx's new login Cookie so
existing anonymous assertions retain their original meaning. The expiry fixture
now uses wall-clock time and asserts restart restoration. No skips, weakened
business assertions or changed delegated grants were introduced.

Warnings: pytest reports the existing unrecognized timeout option in this local
environment. Vite reports mixed static/dynamic imports and a chunk-size advisory.
All listed successful commands exited 0.

## Unverified boundaries

- No real HTTP listener, TLS reverse proxy, or browser end-to-end test was run.
  HTTP/HTTPS flag checks use in-process ASGI; UI checks use jsdom.
- Browser close/session restore behavior was not manually exercised. Session
  Cookies follow browser close/restore semantics.
- No three-platform remote CI, POSIX filesystem-mode runtime check, or full
  repository test suite was run. The implementation calls portable 0600 file APIs;
  Windows chmod is not a verified per-user ACL guarantee.
- No production data migration, deployment, service restart or human acceptance.
- A single runtime owns a profile; simultaneous independent session-file writers
  are outside this local-session design.

## Changed files

- [docs/design/Authorization-Profile-Agent-Matrix.md](../../docs/design/Authorization-Profile-Agent-Matrix.md)
- [docs/reports/AUTOMATION_LONG_SESSION_20261025_VALIDATION.md](../../docs/reports/AUTOMATION_LONG_SESSION_20261025_VALIDATION.md)
- [packages/automation/api.py](../../packages/automation/api.py)
- [packages/automation/auth.py](../../packages/automation/auth.py)
- [packages/web/src/components/automation/JobUserAuthorization.tsx](../../packages/web/src/components/automation/JobUserAuthorization.tsx)
- [packages/web/src/components/automation/RunList.tsx](../../packages/web/src/components/automation/RunList.tsx)
- [packages/web/src/components/automation/TriggerEditor.tsx](../../packages/web/src/components/automation/TriggerEditor.tsx)
- [packages/web/src/components/jobs/JobTemplateEditor.tsx](../../packages/web/src/components/jobs/JobTemplateEditor.tsx)
- [packages/web/src/components/layout/AuthorizationSettings.test.tsx](../../packages/web/src/components/layout/AuthorizationSettings.test.tsx)
- [packages/web/src/components/layout/AuthorizationSettings.tsx](../../packages/web/src/components/layout/AuthorizationSettings.tsx)
- [packages/web/src/main.tsx](../../packages/web/src/main.tsx)
- [packages/web/src/services/api.ts](../../packages/web/src/services/api.ts)
- [packages/web/src/stores/automationSession.test.tsx](../../packages/web/src/stores/automationSession.test.tsx)
- [packages/web/src/stores/automationStore.ts](../../packages/web/src/stores/automationStore.ts)
- [packages/web/src/stores/lockerUnreadStore.ts](../../packages/web/src/stores/lockerUnreadStore.ts)
- [packages/web/src/views/ActionsView.tsx](../../packages/web/src/views/ActionsView.tsx)
- [packages/web/src/views/AutomationView.tsx](../../packages/web/src/views/AutomationView.tsx)
- [packages/web/src/views/JobsView.tsx](../../packages/web/src/views/JobsView.tsx)
- [packages/web/src/views/LockerMessageRow.tsx](../../packages/web/src/views/LockerMessageRow.tsx)
- [packages/web/src/views/LockerView.tsx](../../packages/web/src/views/LockerView.tsx)
- [packages/web/src/views/UserCardRow.tsx](../../packages/web/src/views/UserCardRow.tsx)
- [packages/web/src/views/lockerModel.ts](../../packages/web/src/views/lockerModel.ts)
- [tests/test_automation_core.py](../../tests/test_automation_core.py)
- [tests/test_automation_sessions.py](../../tests/test_automation_sessions.py)
- [tests/test_job_action_templates.py](../../tests/test_job_action_templates.py)
