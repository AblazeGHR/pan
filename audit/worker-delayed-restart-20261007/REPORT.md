# Worker delayed restart — TA delivery, 2026-10-07

Implementation is ready for independent MA acceptance. TA has not advanced main or practical. The final candidate SHA is reported with the delivery message; this report and evidence are committed with the implementation.

## Scope and boundaries

- Base: `839d5ff3b494ce4a76c8ababd03c2fd9828be162`, verified on local main and practical before worktree creation.
- Worktree: `D:/project/pan-worktrees/worker-delayed-restart-20261007`; branch: `feature/worker-delayed-restart-20261007`; started clean.
- Main: `D:/project/Pan-main`, branch main. Preserved untracked `docs/SCEPTER_SESSION_MAILBOX_ARCH_REVIEW_BRIEF_20261002.md`, `docs/design/Scepter-Session-Inbox-Architecture.md`, `packages/web/node_modules.backup-20261003/`.
- Practical: `D:/project/Pan`, branch practical. Preserved untracked `docs/SCEPTER_PHASE1_EXTERNAL_SESSION_POLICY_RELOAD_SPEC.md`, `packages/web/node_modules.backup-20261003/`.
- Initial/final OS listener observation: 8767 had no listener; 8768 remained PID 38428, created 2026-10-07 17:39:55 local, command `D:/project/Pan/.venv/Scripts/python.exe D:/project/Pan/main.py --pan-root-marker=D:/project/Pan --pan-port-marker=8768`.
- Read current constraints and repository Pan handbook. No stash/reset, branch advancement, push/tag/release, package installation, shared dependency junction, native CLI/transcript writes, protected Worker/service lifecycle operation, browser/API/WS test, or test suite execution. Durable Job control plane was used as explicitly requested, with terminal notices already subscribed; these are validation Jobs, not Worker/API acceptance.
- Frontend dependencies were physically copied into the isolated worktree. 1,319 dependency links resolve only within its own node_modules; none points at canonical/shared dependencies. Missing xterm bodies were copied read-only from practical, exact versions 6.0.0 and addon-fit 0.11.0. No manifest/lockfile change or install.

## Behavior and implementation chain

Priority is ONLY among actions waiting for the next valid idle boundary. It does not outrank running tasks, terminal saves/result/report publication, or immediate lifecycle operations.

1. Settings Worker section and TopBar clock button → workerStore.delayedRestart → POST `/api/sessions/{id}/worker/delayed-restart` → worker.request_delayed_restart.
2. Existing process-affecting PATCH settings/requireRestart → the same request_delayed_restart. One Session intent is authoritative; pending_restart is its existing Worker-local admission projection. Repeated requests merge while pending/restarting. Intent captures workerId and generation; the coroutine also captures the exact Worker object.
3. Running/terminal-processing requests remain pending. Terminal chain remains `_persist_terminal_state` → `_publish_terminal_events` → `_enqueue_report` → `_finish_terminal_bookkeeping`. `_terminal_finishing` blocks a temporary idle during save/publication/report. The accepted queue unit's `_queue_unit_active` and handoff latch keep its finally cleanup ahead of restart.
4. `_deliver_queue_unit` finally → `_maybe_restart_pending` → synchronous `w.status = restarting` → schedule `_respawn_worker(w, intent)`. This assignment is the idle-retirement linearization point: the old consumer cannot take a new FIFO unit before the coroutine runs.
5. `_consumer`, reservation and provider-handoff admission reject the retiring runtime. Preparation before running, such as memory projection, is cancellable rather than delaying restart; the existing kill helper awaits the old consumer's reservation unwind. A retracted pre-handoff unit is immediately eligible, without a retry timer. No running/handoff-accepted task is cancelled by delayed restart.
6. `_respawn_worker` takes the existing Session spawn lock; rechecks intent identity, status, exact Worker and captured generation; awaits `_kill_worker_unlocked(... preserve_restart_intent=True)`; confirms all old runtimes stopped; calls existing `_create_worker`. Its existing queue migration/recovery starts normal consumption on the replacement. A pending fallback system-prompt queue row is reused rather than reinjected.
7. `worker.delayed_restart` WS snapshots and GET of the same Session route expose pending/restarting/completed/failed/cancelled. Frontend merges by revision and rejects API responses from a changed server epoch; reconnect/resync and opening Settings refresh current status. Status remains readable after old Worker removal.

Already idle, or queued but not running, immediately marks retirement and schedules the safe lifecycle transition. No Worker follows existing Restart's start policy, reports action=start, and deduplicates concurrent accepted starts. A tracked held/takeover Worker is rejected even if its process/consumer is already stopped. Settings with no Worker keep their existing next-spawn semantics.

Immediate Restart/Kill/Takeover, force-send through restart_or_start_worker, generation changes and process/Session cleanup cancel the old intent. Session-level immediate requests can cancel before acquiring a busy spawn lock; the delayed coroutine checks again before replacement spawn and publication. Native Interrupt keeps its existing interrupt semantics; its restart fallback cancels the intent. Legacy `/api/worker/{id}/settings` remains an immediate respawn and supersedes a pending delayed intent.

## Linearization, locks and failure boundaries

- Acceptance: synchronous intent installation and workerId/generation capture, with no await between liveness decision and idle admission fencing. No timer or polling is added.
- Retirement: synchronous status=restarting, before consumer wake/selection. An accepted running queue unit reaches this point only after result/report and finally cleanup.
- Replacement authorization: identity/generation/status check under the existing per-Session spawn lock. The existing lifecycle lock spans stop/create, to prevent another creator opening a second writer. No new global lock; no process exit/network wait inside a global lock. Existing queue_lock handoff/save ranges are unchanged; new checks add only decisions.
- Failed stop: do not spawn; retain the old runtime as a fenced registry entry with error state and no consumer. Automatic recovery cannot create another writer. Immediate Restart/Kill remains the explicit recovery route.
- Failed spawn/exception: expose failed/error text, including after old Worker removal. An explicit new request can retry when there is no live runtime. A registered unhealthy runtime requires immediate Restart, rather than a permanently pending idle request.
- Explicit cancellation: cancelled state does not schedule a later restart on another generation. Cancellation during shutdown is reported by the coordinator; intents are process-local and are not durable requests across Pan service restarts.
- Queue pause/manual locks/FIFO and receipt/handoff identities use existing implementations. New delayed restart logic does not edit accepted sent_to_cli rows or terminal payloads.

## Deterministic experiment matrix

These are direct one-off interleaving experiments, with no pytest/Vitest/browser test runner or new test suite. Production consumer/reservation/handoff/terminal-publication/coordinator code is exercised. Provider spawn/kill, disk saves, completion-notification and report transport are replaced with in-memory boundaries. The fallback prompt scenario also executes actual `_create_worker` with an in-memory provider. They are not live provider or durable storage acceptance.

| Scenario | Observed result |
|---|---|
| Running request plus 1,000 repeated coordinator requests | One pending intent, no interruption or lifecycle operation |
| Temporary idle during terminal publication / accepted-unit cleanup | No restart until valid boundary |
| Actual consumer handoff + gated result/report + queued successor | Old writes task 1 only; replacement writes task 2 once; locked task 3 remains; one result |
| Already idle + paused queue | Immediate scheduled restart; pause/FIFO intact |
| queued before running | Replacement takes the next item; old consumer does not |
| Request inside reserved receipt save | Retract old reservation; zero timer backoff; one new handoff/history receipt |
| Slow memory preparation before running | Cancel preparation without releasing its gate; replacement takes original FIFO item once |
| Report queue item | Same admission fence; replacement takes report once |
| Actual create with queued fallback system prompt | Reuse existing prompt row; one handoff, no reinjection |
| One-shot idle with process=None | Coordinator executes despite no resident provider process |
| No Worker + repeated request | action=start; one creation |
| Generation changed before spawn lock acquired | Cancel old intent; no kill/create |
| Immediate lifecycle supersession | Old pending intent cancelled; no later idle restart |
| Immediate request while old exit is gated | No replacement spawn after cancellation |
| Injected spawn refusal | Observable failed state survives removal; explicit retry completes |
| Old process refuses exit | Failed; retained fenced runtime; no replacement writer or unsafe automatic retry |
| Held, including stopped consumer/process | Reject; no replacement writer |

The settings API's use of the same coordinator, real `_enqueue_report` durability, watchdog/recovery behavior and frontend stale-response handling are source-inference evidence. They were audited but not independently exercised with real provider/storage/browser boundaries.

## Validation facts and cost

- Final Python compilation: worker.py and server.py pass py_compile, with output confined to ignored `_e2e_tmp`.
- `git diff --check`: pass.
- Final frontend durable Job `job_b396224b88a0bba5dac886d2`: completed, exitCode=0, notificationState=delivered. Runner PID 38704/createdAt 1791373973.3589027; task PID 1536/createdAt 1791373973.727095. Changed TS/TSX lint: 0 errors, 4 existing SettingsPopover hook warnings. Exact parent file lint reproduces all four warnings. Full `tsc -b`: 10.14 s, exit 0. Vite production build: 8.20 s, exit 0. Compressed asset verification: 50 assets, 0.11 s, exit 0. Existing chunk-size warning remains.
- Final direct-experiment Job `job_dab052814127815d5ee2ab2b`: completed, exitCode=0, notificationState=delivered; 17 scenarios passed. Runner PID 12888/createdAt 1791374413.5160048; task PID 40620/createdAt 1791374413.9576936.
- Baseline type-check Job `job_d569913341f8be3b9e3ca4dc`: same missing @xterm/xterm/@xterm/addon-fit and consequent implicit-any diagnostic as initial candidate run. Attributed to the copied canonical dependency environment, not this change. Corrected only by copying existing exact package bodies into isolation.
- Hot-path cost is constant-time boolean/status checks plus Session intent lookup. The synthetic running guard averaged 39.174 ns/call (100,000 iterations); repeated merged request averaged 225.43 ns/call (10,000 iterations). This is in-process Python cost, not endpoint/browser/provider latency.
- Memory: one small process-local status record per Session that has requested delayed restart, two Worker booleans, and short-lived broadcast/lifecycle tasks. Broadcasts occur on state transitions; duplicate requests do not add tasks. No recurring timer, added sleep, idle polling or intentional retry delay. Stop/spawn, receipt cleanup and existing per-Session serialization dominate actual restart duration and were not timed against a real provider.

Evidence alongside this report: `experiment.py`, `experiment-results.json`, `experiment.log`, `validate_frontend.py`, `frontend-results.json`, `frontend.log`, `baseline-typecheck.log`. Scripts are reproducible direct experiments/build preparation, outside the test suites. Final frontend artifacts describe unchanged TS/TSX code; subsequent source changes were confined to backend retirement/recovery boundaries and comments and were recompiled/re-experimented.

## Unverified and MA acceptance

No Python/frontend regression suite was added or run, per the explicit task restriction. No live isolated Pan HTTP/WS/provider, browser/mobile layout, real settings update, real watchdog timeout/recovery, real queue/persistence fault, real process-exit timing or native adapter restart was exercised. In-memory race experiments cannot establish fsync durability or native context continuity.

MA must independently inspect the candidate SHA/diff and the narrow idle-only priority definition. TA done is not MA acceptance or integration. After acceptance, the user-selected fast-forward policy permits MA to safely advance local main/practical and perform practical build after rechecking dirty/ancestry constraints. Those actions have not been performed by TA.
