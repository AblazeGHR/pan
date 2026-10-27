# History terminal events candidate

- Worktree: `D:/project/pan-worktrees/history-terminal-events-20261027`
- Branch: `feature/history-terminal-events-20261027`
- Baseline: `7f303faa355bfc763f0452ad68d31b920e4dc6e1`
- Source SHA: `23740cf040e347c65774a7c75aaf551d7b69bbf2` (19 source/test files).
- Subsequent evidence commit contains only this directory. Obtain the final candidate SHA with `git rev-parse HEAD`.
- Candidate only; MA acceptance, integration and protected-ref advancement remain independent.

## Contract and implementation

`packages/core/worker.py` appends one `role: terminal`, `kind: terminal`, empty-content history row for done/error in the existing terminal base commit. Its stable messageId/eventId derives from terminalKey. `terminal` contains terminalKey, status, occurredAt, order (existing resultCursor), fixed anchorMessageId or null, taskSeq/taskId/executionSeq and Worker/generation identity. Error diagnostics use the existing redacted allowlist: actual source/code/message; no classification inferred from prose and no opaque CLI payload copied. The existing bounded terminal replay records and last_result carry historyTerminalId references and the post-append history epoch/revision. No sidecar fact store or migration was added.

The execution start bounds the anchor search. Stream error detection's frozen previous-message snapshot wins over later appends while awaiting terminal locks/logging. Done fallback assistant text retains its existing behavior. Failed base commits retry the same history event/body; committed duplicate or conflicting later outcomes cannot replace the original event, including after cold reopen. No-body executions have independent terminal rows. Cancelled behavior is retained.

`packages/core/session.py` retains known terminal rows at the existing assign_pan_message_ids import/reimport boundary, after body identity matching. Incoming omission is valid; known facts override conflicting copies. Anchors never retarget to another turn; missing anchors leave independent events near their original surviving successor. Legacy history without metadata loads/imports normally. No scan/backfill migration, external-format requirement or import gate was added.

React uses `HistoryTerminal.tsx` through MessageDisplayItem. Done is a small check; error has its own icon/title/plain summary/time and expandable source/code/plain diagnostics. Provider Markdown/body rendering is not reused. `sessionStore.ts` places canonical terminal rows after their fixed server anchor and orders same-anchor events by persisted order, never time or arrival. Missing/paged-out anchors remain independent. New terminal worker.result/reconnect receipts suppress temporary text markers and request canonical history, including stale Worker results that cannot mutate the new live execution. Error result text cannot replace the assistant body. Old receipts without historyTerminalId retain legacy compatibility behavior.

History-bound manual-report tail resolution follows the persisted event's explicit assistant anchor; no-body/non-assistant anchors remain unbound. The old behavior remains for history without terminal rows.

## Shared-file scope for MA coordination

`worker.py`: Worker._execution_history_start; _persist_terminal_state_locked; _publish_terminal_events (historyTerminalId field only); _admit_native_turn; _deliver_queue_unit; task_tail_block_id. No notification dispatch, rotation or unified Job semantics were changed. No other candidate was merged.

Frontend paths: types/index.ts; hooks/useWebSocket.ts; stores/sessionStore.ts; components/chat/MessageBubble.tsx; new HistoryTerminal.tsx; components/chat/manualTaskReport.ts. Corresponding store/component/transport/manual-report regressions and CBC/Codex history expectations were updated to assert both original body and independent terminal identities/anchors.

## Verification

Tests ran against the same source bytes subsequently committed as the Source SHA above. No product/test source changed after that commit. All commands below exit 0. Python commands use the canonical interpreter and isolated runner, owned temporary profiles and assigned random ports.

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_history_terminal_events.py tests/test_terminal_report_diagnostic.py tests/test_report_error_context.py tests/test_codex_reimport_origins.py tests/test_reimport_confirmation.py tests/test_manual_task_report.py tests/test_worker_history.py tests/test_delivery_semantics.py tests/test_session_unread_ack.py tests/test_legal_offline.py tests/test_backend_perf_opt.py tests/test_codex_worker_integration.py tests/test_codex_native_completion.py tests/test_worker_oneshot_usage.py -q --junitxml=artifacts/history-terminal/python-regressions.xml

# cwd packages/web
pnpm exec vitest run --cache=false src/stores/sessionStore.historyTerminal.test.ts src/components/chat/HistoryTerminal.test.tsx src/components/chat/manualTaskReport.test.ts src/hooks/useWebSocket.test.tsx src/stores/sessionStore.terminalTransient.test.ts src/stores/sessionStore.interleaving.test.ts src/stores/reaudit.history.test.ts --reporter=default --reporter=junit --outputFile=../../../artifacts/history-terminal/react-all.xml
pnpm exec tsc -b --pretty false
pnpm build

# cwd repository root
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_history_terminal_http.py -q --junitxml=artifacts/history-terminal/http-final.xml
```

| Layer | Final result | Evidence |
| --- | --- | --- |
| Python focused/regressions | 301 collected: 295 passed, 6 existing skipped; 0 failure/error; exit 0 | [JUnit](python-regressions.xml), [log](python-regressions.log) |
| React focused/regressions | 213 passed, 0 skipped/failure/error; exit 0 | [JUnit](react-all.xml), [log](react-all.log) |
| TypeScript | Explicit tsc and normal source pre-commit hook passed; exit 0 | Source commit hook output in task transcript |
| Production build | Passed, 90 compressed assets verified; exit 0 | [log](build.log) |
| Authenticated HTTP + actual WS + production Chromium + cold host reopen | 1 passed, 0 skipped; Python and Node exit 0 | [JUnit](http-final.xml), [log](http-final.log), [browser facts](http/browser.json), [owned runtime/cleanup](http/runtime.json) |

The 6 pre-existing skips all state `Oneshot mode retired by user; implementation retained`: 2 in test_report_error_context.py and 4 in test_worker_oneshot_usage.py. No skip/xfail or relaxed assertion was introduced.

Browser checks use the actual FastAPI routes and built production assets, Cookie login with the owned profile key, actual /ws frames, deterministic local CLI processes and Chromium. The harness delays the old error's real WebSocket callback until the next execution's done, then delivers that old callback twice. It does not synthesize product events or replace HTTP responses. It checks actual diagnostic classification, original assistant text and fixed anchor, exactly one independent error/done with no text markers, diagnostics expansion, A-to-B-to-A, refresh/reconnect, 1280/390px rendering, 130 subsequent provider body blocks, real older-page HTTP and virtualized DOM, and cold server reopen of unchanged terminal IDs. All three provider inputs are checked as text-only (no terminal rows). Page errors are empty. Runtime evidence records the random port, retained owned process identities, providerTextOnly, coldReopen and portReleased=true.

- [Desktop screenshot](http/desktop.png)
- [Mobile screenshot](http/mobile.png)
- [Error recovered through older-page scrolling](http/paged-error.png)

## Red evidence and environment

- [Dependency red](react-dependency-red.xml): original prepared canonical junction lacked @asamuzakjp/css-color; 32 store tests passed but 2 jsdom workers could not start. No canonical dependency files were written. A frozen-lockfile install with ignore-scripts was made under this tree's ignored `.cache/history-terminal-deps`, using this tree's `.cache/pnpm-store`; this worktree's dependency junction was repointed to that owned installation. Tests use --cache=false. Build/cache outputs stay in this tree.
- [Initial changed-contract red](python-contract-red.xml): 4 assertions expected history to contain only bodies. [Codex changed-contract red](python-codex-contract-red.xml): 2 further CBC/Codex/native assertions had the same assumption. They were updated to explicitly assert the exact new event count, status, task identity, original body and fixed anchor. Final 301-test XML is green apart from the existing 6 skips. These are intentional feature-contract changes, not claimed baseline-equivalent failures; no baseline runtime run was performed.
- [HTTP first red](http-first.xml): owned Cookie writes lacked the required same-origin Origin; fixed in the harness. [HTTP second red](http-second.xml): fixture Session names contained forbidden spaces; fixed in the harness.
- [Pagination red](http-pagination-red.xml), [log](http/pagination-red.log): programmatic scrolling supplied no real user gesture, so the existing pagination gate correctly refused it. Final test uses actual Chromium mouse-wheel input and asserts older-page HTTP. No product pagination gate was weakened.
- Vite reports existing large-chunk/dynamic-import warnings; build succeeds. Python reports existing Pydantic/Starlette deprecation warnings. Mocked hook tests print their existing loadSessions-failed diagnostics while all assertions pass; this is separate from the real HTTP/browser check.

## Boundaries and remaining acceptance

No full repository suite, remote CI, Linux/macOS runtime, real external model/account, real-user data migration, deployment or human acceptance was performed. The real-chain fixture proves Pan parser/persistence/HTTP/WS/browser behavior with a local deterministic CLI, not provider availability. No real data/profile, protected port 8767/8768, existing service, merge, push or release was operated.

Protected refs read at delivery: main `7f303faa355bfc763f0452ad68d31b920e4dc6e1`; practical `5b6165ca7d1b2751b5ddf736a9679bcefb24a068`. This task issued no ref-advancement command. MA independently accepts the candidate and chooses main/practical advancement. All owned runtime instances were stopped and their random listener released.
