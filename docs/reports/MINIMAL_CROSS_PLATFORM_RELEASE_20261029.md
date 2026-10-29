# Minimal cross-platform candidate: ordinary server / pipe Worker

Base: `9b4506c94ac67a39093961b218b2f01d387d1049` (main observed2026-10-29).
Branch/worktree: `xplat/minimal-release-20261029`, `D:/project/pan-worktrees/xplat-minimal-release-20261029`.
This is a new minimal candidate; CI40873872539 on4a86 does not accept this different source.

Only seven product paths are extracted as patch hunks from c8c8c4eb / cf7510f5:
`packages/core/platform/worker_runtime.py`, `packages/core/worker.py`,
`packages/web/native_control_api.py`, `packages/web/server.py`,
`packages/web/src/components/session/NewSessionModal.tsx`,
`packages/plugins/ipc.py`, `packages/core/platform/plugin_ipc.py`.
No whole mixed commit/file, Job retirement, FinalService, OwnerFence, schema or migration is included.
`evidence/cp-x3-minimal-20261029/guard.py` verifies this boundary and hashes actual source.

## Capability matrix for this candidate

The baseline registry is unchanged. `GET /api/platform` returns these six keys.
The table is declaration and tested boundary, not a guarantee every provider or machine works.

| Capability | Windows | Linux/macOS | API / UI boundary |
|---|---|---|---|
| `terminal.native` | declared true; existing ConPTY implementation | false | Terminal/native REST refuses before effects; Terminal panel/entry gates, native Session create/config/start reject; Create checkbox disabled on explicit false |
| `terminal.detach` | declared true, subject to owned terminal/lease proofs | false | Existing terminal runtime gate blocks POSIX detach; no POSIX durable detach/reattach |
| `rewind` | declared true; CBC-specific existing implementation | false | HTTP501 unsupported_platform; rewind control disabled |
| `service.lifecycle` | declared true; existing main launcher/lifecycle | false | Exit/Restart API501, status available=false; settings controls disabled |
| `processOwner` | declared true; baseline Windows implementation | **false unchanged** | Full POSIX ProcessOwner release not included. Pipe start_new_session is signal isolation, not durable ownership or complete tree containment |
| `secretStore` | declared true; existing DPAPI | false | DPAPI/terminal secret functionality remains Windows-only; no POSIX secret backend |

No `job.runtimeOwner`, `process.hiddenDesktop`, or `time.hostZone` capability was added.
Unknown/missing capability probe keeps existing UI available; only explicit false disables.
Unknown backend errors are not converted into successful or unsupported receipts.

## Startup and limitations for users

Use a separate checkout and explicit Python environment. Install that checkout's
`minimal-requirements.txt`; build React with its lockfile (`pnpm install --frozen-lockfile`,
`pnpm build`), and configure only provider CLIs you intend to use. This task reused
existing dependencies read-only and did not install packages into shared environments.
For POSIX, the intended bounded entry is foreground `python main.py` after setup;
packaging, systemd/launchd, native PTY, detach, rewind and DPAPI are not added here.
The declared POSIX service.lifecycle=false means `pan exit/restart` or shell wrappers
must not be advertised as fully supported lifecycle management. Host Ctrl+C/SIGTERM
is not a safe-completion or durable Job migration authorization.

Ordinary pipe Worker now starts its own POSIX session; host terminal signals no longer
implicitly define CLI cancellation. Pan's explicit stop path still owns stopping its
known CLI tree. This does not prove children that deliberately escape containment or
durable reattachment. CLI stdin/stdout encoding belongs to that provider; the owned
Unicode probe explicitly runs Python with UTF8. No real model/account consumption is
claimed. Missing React dist yields the existing503; never reuse an old bundle as proof.

The short Unix plugin socket uses random0700 private temp directories, falls back
from long TMPDIR to /tmp, and removes only its verified empty directory. No TCP fallback
or authentication change. Read-only/networked filesystems, unsupported CLI installations,
unported third-party plugins and production data are outside the smoke claim.

## Existing profiles and Jobs

All Job/Scheduler/host ownership product paths remain byte-identical to9b4506.
Create/update/retry/start/recovery behavior is not retired. Original retry creates a
new Job with retryOf, preserving the old record. Synthetic preexisting completed Job,
Session and disabled schedule are cold-loaded by an owned real HTTP server; the schedule
can still be updated and old failed process retried after stop. No canonical store or
owner-cutover DB is created. This is compatibility evidence for these main-format cases,
not a real-user data migration. No migration is required/introduced by this patch set.

## Acceptance and recurrence

The prior4a86 review verdict is retained at original tree commit
`cd8b6d682136c98e7294b864f6e2d715544d06b8` in
`evidence/cp-x3-freeze-20261029/REVIEW.md`: that larger branch must not ship as a
platform-only upgrade preserving existing Jobs. Job unification proceeds separately.

The Windows3.14 plugin timeouts in CI40881289687 remain an open recurrence concern.
This candidate's plugin tree differs from4a86 (it deliberately retains main), so the
old focused green cannot be inherited. Run the original unchanged plugin suites on the
new source, preserving timeouts/diagnostics. Local green does not close remote risk.

Three-platform import/collection/fast and fresh Ubuntu authenticated production
HTTP/browser must run on the exact independent candidate via MA. This task does not
push/dispatch CI, move main/practical, deploy or claim all roadmap phases complete.
