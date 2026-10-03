# Queue composer edit TA handoff

Worktree: `D:/project/pan-worktrees/queue-composer-edit-20261003`
Branch: `feature/queue-composer-edit-20261003`
Base: `330f8a58cd0391feb2df5f4c274c48910e8f8ef1`

No applicable AGENTS.md was found in the worktree or its ancestor directories. The supplied MA/user brief governs this change. The prepared dependency junction and its untracked marker are preserved.

## Delivered frontend behavior

- All public pending queue kinds have an edit entry, subject to readonly Session and the current edit transaction guard. Normal user tasks skip confirmation. Other sources use the shared Modal/Button confirmation pattern with the exact text `是否确认修改agent/系统消息？`.
- Notification settings add `confirmAgentSystemQueueEdit`, default true with missing/malformed legacy values falling back to true. Uses the existing settings store and API. Notification writes include the full current notification object because `api_put_settings_ui` replaces nested objects.
- Queue rows no longer contain an editor. The main input area switches to a plain-text queue body editor with X cancel and √ save. The original rich composer remains mounted and hidden with its full value and attachments preserved. Attachment menus close when editing begins; queue edits never receive composer attachment parts.
- Enter saves, Shift+Enter inserts a newline, Escape cancels, and IME composition does not save. Existing imperative enqueue/Steer edit guards remain.
- Edits remain Session-scoped across switches. Another item cannot replace an active edit. Existing token identity, lease acquisition/renewal/release, revision checks and PATCH remain. Save failure retains the draft; lease failure or removal/delivery preserves the text with an error and blocks save. Cancel releases the token and returns to the original composer. Failed release retains the edit for retry. Readonly Sessions cannot begin or save an edit.

## Backend conflict: complete all-source editing is blocked

Read-only source audit: `queueStore.startEdit` -> `acquireSessionQueueItemEdit` (`services/api.ts`) -> POST `/api/sessions/{session_id}/queue/{item_id}/edit/lock` -> `api_session_queue_edit_lock` (`server.py`). It rejects any kind other than task or any task source other than user with `queue_item_readonly` / `Only user task queue items can be edited` before lease acquisition. `queueStore.saveEdit` -> `updateSessionQueueItem` -> PATCH same queue item -> `api_session_queue_update` repeats the same rejection. Therefore report/agent/system entries can request editing but cannot acquire an edit lease or save against this baseline. No backend file or protocol was changed.

Report text uses the existing `_serialize_queue_item` projection of `result`, never source/kind badges or other row decoration. The existing projection serializes non-string results and truncates long reports at `_REPORT_TEXT_MAX`; full long-report editing is consequently not established. Attachment-bearing queued tasks also retain existing backend restrictions on changing their projected text/parts.

## Validation and limits

- TypeScript `node node_modules/typescript/bin/tsc -b`: passed after minimal fixture field synchronization.
- ESLint for all changed TypeScript/TSX files: passed.
- Vite production build: passed; warns about chunks above 500 kB.
- `node e2e/verify-precompressed-assets.mjs`: 32 compressed production assets verified.
- `git diff --check`: passed.
- No test suite was added or executed. Existing tests only receive two required settings fixture fields, notification snapshot expectations, and the removed-item draft-retention contract update; assertions remain in place.
- Browser/mobile interaction, actual HTTP editing, concurrent clients, runtime lease expiry, persistence after refresh and network failure recovery have not been exercised. Static/type/build checks are not interaction acceptance.
- All-source edit remains blocked by backend policy. Settings persistence retains the existing best-effort failure behavior and multi-client write ordering limits.
- No integration, push, tag or release; no main/practical operation, stash, real 8767/8768 service access or user-data operation. Build output is local to this worktree and not committed.
