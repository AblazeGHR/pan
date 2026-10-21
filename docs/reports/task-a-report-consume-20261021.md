# A8 candidate: report status and original-ID batch consumption

> Historical c5a9140 evidence. The Action-integrated successor and read-only receipt correction are documented in [integration candidate](task-a-action-report-integration-20261021.md).

Worktree: `D:/project/pan-worktrees/task-a-history-read-20261021`. Branch: `feature/task-a-history-read-20261021`. Base: `1e74c3742561ce28b5b2385d4a94bcc430bf988b`, verified clean before edits. Fast-forward candidate for MA acceptance only; no main/practical movement, push/release or existing-service operation.

The latest user decision supersedes historical PAN_SMA A8 next/ack: **no report_ack; remove report_next; add report_consume** (canonical spelling for the user's report_comsume, no alias). report_status remains. This is the complete requested implementation candidate, not an MA acceptance claim. The previous report-reads report and artifacts are historical baseline evidence only.

## Existing source evidence and resulting change

At base 1e74c374, `worker._select_queue_unit` (line 3584) already batches adjacent report/qq/wechat rows for ordinary delivery, stopping at task/lock/lease boundaries; selfConsumeRequested selects a single item. `_deliver_queue_unit` and `_queue_history_row` already format a unit and retain its IDs/delivery keys/origin envelopes. In contrast, `_steer_queue_item` at line 9970 uses `_format_report_batch([item])`, and its reservation/settlement acts on one item. Thus ordinary batching existed, but native queue Steer was single-item.

The change extends that same Steer transaction to resolve a frozen group of original rows. One native control write uses the existing `_format_report_batch(items)`. One successful history row uses `_queue_history_row(items, text)`, preserving each queueItemId, source Session, task and envelope. Each original item gets its own existing sent_to_cli ledger receipt; there is no synthetic report, copied enqueue or parallel inbox.

`self_queue._report_consume` selects processable child-agent reports under the original queue lock. It excludes Job/notice/channel rows, missing-ID legacy rows and blocked rows. It moves selected original objects to the front in original relative order; the other objects retain their relative order. Existing IDs are recorded as reportConsumeIds on the original rows, alongside the existing self-consume intent. Selection/reordering/intent is one ordered persistence ticket, rolled back together if saving fails. No gate is unlocked.

`worker._report_consume_unit` and `_select_queue_unit` resolve exactly these members for ordinary consumption, without splitting the batch or merging unrelated notifications. Reservation still reselects the same unit and verifies revisions under lock. Native Steer reserves all members with the same request/runtime identity, rechecks policy/lease/runtime gates after the persistence await, and performs one write. Automatic Force skips frozen members, including a second check inside its restart queue lock, so it cannot independently reorder one member. A previously selected stale ordinary unit fails reservation after regrouping.

The wrapper reuses the existing self-consume Force route: running/capable prefers Steer; running/non-Steer uses restart-or-start with expected Worker/generation; idle/queued wakes ordinary delivery regardless of Steer capability; absent Worker uses existing creation. Delayed restart remains prior to ordinary work. These routes do not add a provider call, timer, poller or recovery engine.

## Public contract and privacy

- `report_status()` reads only the own persistent subscription configuration, as before.
- `report_consume()` groups/processes the current eligible reports. Concurrent calls share a server-owned, shielded attempt. Request cancellation does not cancel a committed attempt.
- `report_consume(item_id=original_id)` only inspects that batch's receipt, using any original member ID. It never retries a write or creates another batch. An unfinished frozen batch takes precedence on subsequent no-argument calls.
- Metadata-only receipt: ok/status/itemId/queueItemIds/retryable. `accepted` proves durable CLI admission, never model consumption, task completion or MA review. queued/pending/unknown/unconfirmed are not success. Actual policy/edit locks still hide the batch identity with item_unavailable. Missing/expired/non-batch IDs are unavailable; incomplete receipt proof is unknown. Existing TTL/count retention applies; metadata compaction retains the member IDs.
- Unknown native admission retains queueSteerPending on every member. Ordinary handoff persists selfConsumeAttempted before a write; ambiguous failure/restart becomes selfConsumeUncertain. Neither path automatically replays or regroups an uncertain batch. Explicit rejected native admission keeps the same queued unit available to the normal consumer. Original-ID query remains observational.
- Only successful CLI handoff adds the report body to history and removes originals. A later persistence failure does not resend: it returns unconfirmed until the receipt can be durably verified.
- `report_next` MCP tool and HTTP operation are removed. `self_queue_read` returns only identity/type/source for child-agent reports, with no result, content, text, snippet, envelope, parts or attachment text. Non-report read behavior is unchanged. Legacy report reads do not allocate IDs/aliases. Consume is recommended before reading context from real handoff/history.
- Existing bound stdio/loopback identity is required. No Session argument or remote/SSE identity lending. Illegal/manual/stop-report row locks, Pause, readonly, edit leases and native owner boundaries remain. Unprocessable items reveal no identity/count.

## Validation

Final command (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_report_reads.py tests/test_self_queue.py tests/test_force_steer.py tests/test_auto_force_sources.py tests/test_steer_restart_priority.py tests/test_steer_history_consistency.py tests/test_queue_migration.py tests/test_queue_restart_hardening.py tests/test_unified_server_queue.py tests/test_cbc_steer.py tests/test_mcp_handbook.py tests/test_sma_manifest_prompt.py -o addopts= -q --junitxml=artifacts/task-a-report-consume-junit.xml -o junit_family=legacy
```

**243 passed, 2 skipped, 0 failures/errors**, 7.98s. The skips are existing retired oneshot tests in test_unified_server_queue: test_one_shot_spawn_success_dequeues_after_argv_handoff and test_one_shot_spawn_failure_requeues_same_item. Existing warnings: missing pytest-timeout config plugin, Pydantic lifespan forward reference, Starlette/httpx deprecation. No skip/assertion weakening was introduced. An older self_queue assertion expecting report text was updated to require only original identity and no body.

The 42 report cases cover read/privacy and stored-body side channels; mixed queues and original ordering; 14 locks/state gates; true one-control batch Steer with accepted/rejected/unknown native receipts; idle capable/noncapable and running non-Steer routing; queue intent and Steer/ordinary attempt persistence failure; final Pause/readonly/lease/owner changes; cancelled request and stale concurrent normal selection; original history identity; cold Session hydration preserving prior history; ambiguous ordinary write across reload/migration without regroup/replay; delayed restart; missing legacy IDs; receipt compaction/partial proof; post-admission persistence failure; local-only HTTP/MCP identity and schema.

Real protocol fixture: owned Uvicorn loopback **58237**, test process **25016**, temporary pytest Session/config stores, no application lifespan. MCP SDK initialize/list_tools/call_tool uses the actual MCP server and actual urllib HTTP wrapper to the own-queue route. A fixture adapter/stream routes native acceptance receipts through the real Worker transaction. It verifies absent report_next/report_ack, report_consume schema, metadata-only responses, one actual fixture control write, and same-ID receipt without replay. Socket/thread closure and connection refusal are asserted. This is real HTTP/MCP protocol evidence, not a real provider/CLI model execution claim.

Evidence: [JUnit](../../artifacts/task-a-report-consume-junit.xml), [test output](../../artifacts/task-a-report-consume-tests.txt), [report tests](../../tests/test_report_reads.py). Changed Python compileall, manifest JSON parse and git diff --check also passed.

## Boundaries and MA handoff

No full suite, paid model, real adapter process spawn/restart, production stdio/SSE deployment or operating-system service crash was run. Restart evidence is cold Session reload plus the real migration of persisted attempted/reserved/unknown state; Force process lifecycle routing is fixture-verified. Native control/history/ledger transitions use real repository helpers with fake provider I/O. This does not prove any provider's semantic processing of the report.

Consume can hydrate the existing full own history before capturing row references because existing native runtime validation/Worker lifecycle requires the canonical full Session; this is not a bounded-memory history-read API. Report response bodies remain metadata-only. Structured report parts unsupported by existing native Steer are skipped on that route. No new attachment transport was introduced.

After formation a batch is indivisible: a member lock/lease/reorder blocks it until existing gates/order are restored. The tool does not unlock, delete, detach members, or resolve unknown CLI writes. After existing receipt retention expires it cannot re-establish prior acceptance and never re-enqueues the old body. No new retry/ack semantics are introduced.

Repository MCP descriptions/manifest, Pan skill and HTTP reference are synchronized. **MA should sync the user-level Pan skill and HTTP reference**: replace next/ack guidance with consume-first, identity-only own report reads, CLI accepted vs processed distinction, same-original-ID receipt lookup and unknown no-replay contract. User-level skill was not edited. No other A item/Action change or subagent was used.
