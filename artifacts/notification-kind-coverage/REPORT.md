# Notification error kind coverage repair

Base / reproduction source: `ae58a4639488db7abb3094fb0c0cd08ef2ca6848`.
Branch: `fix/notification-error-kind-coverage-20261029`.
Same worktree: `D:/project/pan-worktrees/notification-locker-error-20261027`.
The previous branch remains at `363a8c764772a82ee3a5023302e8cd00c87bc42b`.
This candidate starts directly from the exact requested main; no old-history
merge, protected-ref movement, push, deployment or existing-service operation.
MA performs independent acceptance and fast-forward integration.

## Reproduction and repair

Before changing production code, `test_notification_kind_coverage.py` reproduced
eight failures: all six general kinds were absent from the selector/predicate,
transient Codex native types under `cli.error` were missing, and a terminal
`codex_error_payload/rate_limit` was not selectable. See `backend-red.xml`.

The only backend production file changed is `source_notifications.py`:

- `ERROR_KINDS` derives from authoritative `session_error_log.KINDS`, excluding
  `cancelled`. Both admission and catalog use that one set. Six stable IDs use
  `kind/{literal_kind}` with labels such as `Pan kind · tool_failure`.
- A record now has a set of matches: its Pan kind plus every known provider
  code/native variant. Any selected match excludes new admission. The existing
  `error_type()` helper remains as the first-provider compatibility projection.
- All 25 existing source/code IDs remain accepted. Twelve proven provider IDs
  are added, for 37 provider IDs and six kind IDs: **43 total**, with no duplicates.
- Provider facts use `cli.errorCode`, `cli.error.code/type/errorCode` and
  `cli.error.codexErrorInfo`. Known one-key Codex native objects contribute their
  enum tag only. The exact `codex.item_error` retained `cli.raw.item.error` seam
  is handled when the flattened error is missing; no recursive scan is used.
- No prose, error-message vocabulary, HTTP status or real log is interpreted.
  Unknown/unselected facts continue forwarding. A known selected kind still
  matches when provider code is absent/unknown. Cancellation never admits.

No API, persisted schema, settings path, ledger schema, epoch algorithm or
transport changed. Behavior and Notifications still use the same saved
`{enabled, types}`, same catalog endpoint and same shared component. Its copy
now explains kind-or-provider matching. Existing selected IDs and disabled
retained selections remain valid. Exclusions still apply only at new admission;
pre-admitted targets/epochs, no replay, error samples/history and manager reports
are unchanged. Worker execution/classification was inspected, not edited.

## Producer coverage inventory

| Existing admitted kind | Authoritative producer in `packages/core/worker.py` | Selectable ID / repaired coverage |
| --- | --- | --- |
| `terminal_error` | `_record_terminal_error_sample` for non-cancelled error terminal | `kind/terminal_error`; works without CLI facts and alongside provider codes |
| `worker_crash` | `_record_worker_crash` for unexpected process/EOF failure | `kind/worker_crash`; no code required |
| `worker_start_failure` | `_record_worker_start_failure` | `kind/worker_start_failure`; no code required |
| `native_operation_error` | `pan.native_error` branch → `_record_transient_error_sample` | `kind/native_operation_error`; wrapper string is not parsed into a fake code |
| `transient_provider_error` | `codex.turn_error`, recovered notice, bare `error`, non-tool `codex.item_error` | `kind/transient_provider_error` plus all known codes/native variants |
| `tool_failure` | `codex.item_error` with `_TOOL_ITEM_TYPES` → `_record_transient_error_sample` | `kind/tool_failure`; no-CLI, unknown-code, flattened and retained item-error facts |
| `cancelled` (not admitted) | terminal cancellation path / `KINDS` | no catalog ID, no notification admission; cancellation-flagged records also rejected |

| Structured source/shape | Existing evidence inspected | Catalog/matching result |
| --- | --- | --- |
| `codex_error_payload` terminal | Codex `app_server_wrapper` terminal/external-result and `wrapper.py` result projection; `runtime_issues` code sets | 18 exact codes/variants; retains previous 11 and adds seven known rate/unavailable variants |
| `cli.error` transient/item | `app_server_wrapper` `error` / `item/completed` envelopes; `session_error_log` projection; Codex adapter fixtures | 18 exact codes/variants; retains previous 14, adds native variants and `unavailable` |
| `dsh_turn_error/MISSING_CREDENTIAL` | actual `dsh.events.terminal_result`, `test_dsh_adapter.py` structured reason fixture | one exact proven ID; tested using the real adapter projection |
| `error.type` / `error.errorCode` aliases | existing `runtime_issues._structured` allowlisted fields | known codes matched as well as `error.code`; no reclassification |
| `codex.item_error` item-only error | worker `_record_transient_error_sample` can read `event.item.error`, while sample preserves it in `cli.raw` | exact retained item envelope contributes known codes/native tags even without flattened source/error |
| native string failures; wrapper exception/stderr; DSH max-token/blocked/aborted reasons | native wrappers and DSH terminal projection | existing general kind is selectable; no invented provider code or prose matching |

`catalog.json` contains every exact ID. Inventory tests tie kind coverage to
`KINDS`, retain the exact previous ID vocabulary, exercise structured aliases
and real DSH/item sample projections. A code and native variant can both match
beside the kind; unknown native tags cannot masquerade as known generic codes.

Explicit producer boundary: DSH `map_event(tool_result)` preserves tool status
as a tool-result event; it does not itself create a `session_error_log` error
sample through the existing worker Codex item-error branch. Likewise stderr
without structured code does not supply a selectable specific code. No new
producer/classifier was added; any existing later terminal/error sample can be
filtered by its general kind. This repair covers the existing notification
admission chain rather than promoting arbitrary history/tool output to errors.

## Validation

| Layer | Exit | Result |
| --- | --- | --- |
| Isolated focused backend + error writer/worker regressions | 0 | 202 passed, 0 failed/errors/skipped |
| React/Vitest notification/settings regressions | 0 | 225 passed, 0 failed/errors/skipped |
| Fresh owned authenticated HTTP/Chromium | 0 | 2 passed, 0 failed/errors/skipped |
| `pnpm install --frozen-lockfile` | 0 | lockfile dependencies restored; no lock/package edits |
| `pnpm exec tsc --noEmit` | 0 | passed |
| `pnpm build` | 0 | production build + 90 precompressed assets verified |

Commands (frontend cwd `packages/web`, otherwise worktree root):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_notification_kind_coverage.py -q --junitxml=artifacts/notification-kind-coverage/backend-red.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_notification_kind_coverage.py tests/test_error_notification_exclusions.py tests/test_source_notifications.py tests/test_session_error_log.py tests/test_session_error_log_worker.py tests/test_notifications_reminders.py tests/test_notification_closed_browser.py -q --junitxml=artifacts/notification-kind-coverage/backend.xml
pnpm install --frozen-lockfile
pnpm exec vitest run src/components/layout/ErrorNotificationExclusions.test.tsx src/components/layout/SourceNotificationPanel.test.tsx src/components/session/PostboxModal.test.tsx src/components/layout/AppSettingsModal.test.tsx src/stores/appSettingsStore.test.ts src/hooks/useWebSocket.test.tsx src/stores/lockerUnreadStore.test.tsx --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/notification-locker-error-20261027/artifacts/notification-kind-coverage/frontend.xml
pnpm exec tsc --noEmit
pnpm build
python scripts/run_tests_isolated.py -- python -m pytest tests/test_notification_kind_http.py tests/test_notification_http.py -q --junitxml=artifacts/notification-kind-coverage/http.xml
```

The new React case searches literal `tool_failure`, selects it in Behavior,
checks the same saved state in Notifications and preserves a prior provider ID.
Existing 80-type bounded-scroll, failed-save and retained-selection tests remain.
The provider-only unknown-code tests now explicitly select provider IDs only:
selecting all IDs also selects the new known general kind and must suppress it.
Dedicated tests assert that new behavior; no assertion was dropped or skipped.

Fresh HTTP/Chromium evidence uses production built React with recording-only
QQ/System/Notification providers. It checks tool-kind exclusion in Session-only
and global-only modes, a known selected kind with unknown code, unmatched-kind
forwarding, disabling with retained selections, selection removal without old
event replay, both-route per-path dedup, reload and restart persistence. After
the browser closes, a new worker-crash kind forwards through QQ/System while a
tool kind remains excluded. CLI Worker spawn is forbidden. Both fixtures assert
owned process cleanup and port release; no real contacts/providers/profile/data
or protected ports were used. Original source/exclusion artifacts are untouched.

## Retained failures and verification limits

- Eight intentional pre-fix regression failures are preserved in `backend-red.xml`.
- `build-first-red.txt`: initial old node_modules lacked the new-main
  `remark-math` dependency. Frozen install fixed the environment, then build passed.
- `http-first-red.xml/txt`: the initial run was started before that fresh build
  and is not accepted as fresh-build evidence. The new kind flow passed, but the
  old probe's broad `/usageLimitExceeded/` selector became ambiguous because two
  sources now expose that literal. The probe now selects the exact existing
  `codex_error_payload` label; fresh-built rerun passed both cases.
- No retry, skip, xfail, weakened behavior check or generic configuration writer
  change was added. Existing Python dependency and bundle-size warnings remain.
- Not verified: full repository suite, remote CI, real CLI execution, real QQ
  delivery, actual OS toast display/human receipt, Linux/macOS runtime or deployed
  services. Source manifests and build/index hash bind the reported evidence to
  this candidate. Normal hook result and final SHA are supplied with delivery.
