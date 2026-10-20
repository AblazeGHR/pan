# Auto Force sources — implementation evidence

- Task: `T-AUTO-FORCE-sources-20261020`
- Worktree: `D:/project/pan-worktrees/auto-force-sources-20261020`
- Branch: `feature/auto-force-sources-20261020`
- Base: `6e479f205a81f85b9c76f1779e1141ee498517db`
- Implementation commit: `038aae03`; follow-up `ad9c32d5` (clear the recovery marker after an automatic restart)
- Evidence commits: `b0786600`, `957b7eaf`, and the commit containing this report
- Strategy: fast-forward policy, MA acceptance pending. No main/practical move, push, release, or service restart was performed.

## Behavior

Three independent persistent switches live at `config.json → ui.autoForceSend`:
`system` (only `background_job_terminal`), `ma` (manager/ancestor agent messages), and
`report` (`source=report` completion/manual reports). All default off.

Every durable task/notice/report enqueue boundary schedules one event-driven attempt
(`_persist_task_item`, `enqueue_notice`, `_enqueue_report`, `enqueue_manual_report`).
Classification uses structured fields only (`type`/`kind`, `source`, `sourceSessionId`
+ manager-chain ancestry, `noticeKind`/legacy job envelope, `taskIdSource`). It never
reads the body prefix.

Routing (explicit Force and automatic Force share it):

- no live Worker, `idle`, `queued`, `held`, empty queue → ordinary durable send; no Steer, no restart.
- `running` + adapter/stream supports native Steer → Steer the original queue item by ID, no reorder.
- `running` + no Steer → persist/reuse the original item once, move that row to the queue head, then restart; the replacement consumer sends it. No second queue identity.
- `restarting`, delayed-restart intent without Steer, Pause, read-only, Illegal, any edit lease, or a locked/stop-report row → leave queued; no bypass.
- `accepted` remains CLI acceptance only; `consumption` is unconfirmed. `rejected`/`unknown` leave the original row and are never automatically retried.

## Existing Force audit (real entries)

- `POST /api/send` (`force=true`), `POST /api/sessions/broadcast` (`force=true`), MCP `agent_send_force` / `worker_send_force` → `worker.send_session`. These previously did **not** always move the item to the head: the Steer branch persisted the item at the tail and addressed it by ID; the restart branch restarted first and then enqueued at the tail.
- Non-running behavior was capability-dependent: idle capable already used normal send, but idle **non-capable** restarted. It now always uses normal send.
- `POST /api/task`, `POST /api/assign`, legacy worker send, notices, channel reminders, and reports never had Force routing. Automatic Force hooks their common enqueue boundaries; formal assigns (`taskIdSource=assign`, `/api/assign`, `agent_assign`) are deliberately excluded.
- `self_queue.consume` also calls `steer_delivery_route`, but its explicit idle/non-capable branch still restarts; that separate self-consume contract was not changed.
- Changed Force behavior: restart now preserves the original queue ID and head position, re-checks gates after the durable write, and leaves the item queued if the restart fails (previously the message was only enqueued after a successful restart).

## Illegal / Wake coexistence

Read-only comparison against `legal-offline-illegal-lock-review-20261018` commits
`2f1c2b3b`/`e160782e` (Illegal locks all new messages; Wake atomically unlocks its own
row and inserts at the head). This change does **not** modify `legal_offline.py`, does
not implement Wake, and does not clear Illegal locks. `steer_delivery_route` returns
`queue` for Illegal; automatic Force skips at schedule and execution; explicit Force
re-checks Illegal and only enqueues. The future merge must keep
`restart_or_start_worker(..., allow_illegal_recovery=False)` in the explicit Force restart
path (and the staged writer's new-row Illegal lock); the pre-checks here are compatible.

## Verification

Command wrapper: `scripts/run_tests_isolated.py` (inherited `PAN_*` removed, owned temp roots).

- Targeted pytest batch: **561 passed** (`targeted-pytest.xml`). Includes:
  - `tests/test_auto_force_sources.py`: 24 (switch matrix, idle, Steer, non-Steer head+FIFO, attachments, delayed restart, Pause/read-only/Illegal/locks/stop reports/edit lease, rejected/unknown, duplicate event/concurrency, Force restart identity).
  - `tests/test_auto_force_http_e2e.py`: 5 real isolated HTTP/WS fixture tests (MA Steer, report Steer, idle normal send, non-Steer head+restart, real background Job terminal Steer).
  - `tests/test_auto_force_ui_e2e.py`: 1 real Chromium test (UI save, real reload, save-failure alert, UI-triggered MA Steer).
  - Existing Force/Steer/report/Job/queue/scheduler/settings regressions: the remainder.
- Existing real queue E2E: **4 passed** (`tests/test_real_queue_http_e2e.py`, allocated test port).
- Frontend vitest: **88 passed** (`appSettingsStore.test.ts` 29, `AppSettingsModal.test.tsx` 59).
- Frontend `tsc -b`: passed. Targeted ESLint: passed. `pnpm build`: passed (pre-commit React check also passed).
- Real service layer uses a dynamic non-8767/8768 port, owned PIDs, deterministic `fake_stream_cli` and a control-wire/receipt fixture for Steer. This proves Pan routing/identity/gates, **not** a real provider receipt.
- Chromium evidence: `auto-force-settings-saved.png`, `auto-force-settings-save-failure.png`, `auto-force-ui-triggered.png`, `chromium-result.json`.

## Unverified / boundaries

- No real CBC/Codex/Claude provider admission/consumption test; Steer receipts are fixture-level.
- No real QQ/WeChat bridge, independent Job service, practical service, or user data.
- No merge/rebase with the in-flight Illegal/Wake commit was performed; integration must re-run the Illegal-focused tests.
- Auto Force is intentionally one attempt at enqueue time. A Steer `unknown` keeps the original row locked for a late receipt; `rejected` leaves it for ordinary FIFO. Neither is auto-retried. Items that cannot be Steered losslessly (e.g. stale attachment paths) stay queued rather than falling back to restart.
- `xterm` was supplied by a gitignored `node_modules/@xterm` junction to the read-only practical dependencies for build/typecheck; no package/lock/shared dependency was changed.
- Test updates: the existing idle one-shot Force test now models a running one-shot turn (idle is intentionally normal-send), and the `assign` send-failure fake accepts the new `auto_force` keyword. No assertions were removed or skipped.
