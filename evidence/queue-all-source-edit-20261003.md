# All-source pending queue editing — incremental TA handoff

Worktree: `D:/project/pan-worktrees/queue-composer-edit-20261003`
Branch: `feature/queue-composer-edit-20261003`
Increment base: `9fad594c23f5b6bf81f637f9daa924751f51a980`
Authorization: MA/user follow-up explicitly permits the necessary backend scope. No new applicable AGENTS.md found. This increment supersedes the backend-blocked limitations in `queue-composer-edit-20261003.md`; the earlier report remains historical evidence.

## Category matrix (source and call-chain audit, not runtime proof)

| Pending category | Full lease editor body | PATCH storage | Production consumption |
| --- | --- | --- | --- |
| User/agent/system task | Entire `text`, without preview labels | `text`; text-only parts synchronized | `_deliver_queue_unit` reads task text and projects parts for the adapter |
| Legacy untyped text task | Entire `text`; same interpretation as legacy migration | `text`, no identity/type migration by editing | Existing `_migrate_legacy_task_items` before delivery |
| String report / zombie | Entire `result`, no 200-character truncation | `result` | `_format_report_batch` reads result |
| Agent/system notice, background Job terminal, legacy automation | Entire `result` | `result`; envelope and routing fields untouched | Existing notice/system formatter reads result and existing metadata |
| QQ / WeChat channel reminder | Entire `text` | `text`; channel/target/bot fields untouched | Existing channel formatter reads text |
| Non-string report result | Faithful JSON, including null/objects/arrays/scalars | Parse JSON into `result`, rather than storing JSON as prose | Existing report formatter retains its original value formatting |
| Structured attachment task/channel | JSON parts with text slots around attachment occurrences | Only text values editable; attachment occurrences/order/metadata preserved, registry validation and canonical text regeneration retained | Existing part projection uses preserved registry-backed references |
| Unknown/malformed kind, non-JSON Python value, invalid structured parts | Explicit error | No mutation | No new consumption/migration model |

JSON editing rejects duplicate keys, NaN/Infinity, overflowing exponents and malformed JSON. Source values that would require lossy coercion (e.g. tuples or non-string object keys) are rejected before lease acquisition. Valid JSON edits may intentionally change the result's JSON type. Report string bodies may be empty; task/channel still require nonblank canonical text. The frontend no longer silently substitutes the original body for an empty attempted save.

Attachment boundary: only text/value fields on text parts may change. Synthetic empty text slots allow prose before/after/between attachments, including attachment-only messages. The editor never exposes `__serverPath`, and the backend preserves private original fields. No addition/removal/reordering of attachment references or changing their metadata is accepted. Original extra metadata survives canonical normalization. Existing registry availability, Session ownership and 512-part normalization limits still apply; missing registry entries or excessive part counts yield an explicit save error, retaining the draft.

## Implementation and invariants

- `api_session_queue_edit_lock`: removes user-task-only policy, checks readonly inside the Session queue lock, queued state and expected revision, and captures the one item's full body/format/revision under that lock. Initial frontend acquisition requests `includeBody: true`; renewals request false, avoiding body transfer on each renewal. List and event previews remain bounded/unchanged.
- `acquireSessionQueueItemEdit` and `queueStore.startEdit`: initial acquiring editor is disabled and empty until the full body arrives. Missing/malformed full-body responses fail closed and release this token. Body/revision are scoped to the edit transaction; renewals cannot overwrite typed text or adopt a newer list revision. Cancellation/release failure preserves the acquired full body.
- `api_session_queue_update`: continues to validate queued state, readonly, token digest/expiry and revision. It writes only the true body field, optional synchronized parts, revision and updatedAt. Existing ledger update, lease removal, receipt persistence, rollback and wake/broadcast sequence remains. Item id/type/source/taskId/eventId/envelope/routing/lock flags/order stay intact.
- `_select_queue_unit`: an edited report/channel follower ends a batch. Existing reservation already checks leases on every selected item; the new selection guard avoids blocking an earlier unedited report behind a leased follower.
- `_deliver_queue_unit` -> `_reserve_queue_unit`: captures item revisions before asynchronous body/memory projection, then compares them inside queue_lock before reservation/history/hand-off. If editing finishes and releases its lease during the await, stale projected text is not sent. Existing finally/wakeup selects again using the new body. Direct legacy helper callers retain an optional revision parameter; the production delivery path supplies it.
- The reminder setting, exact confirmation wording, X/√ composer behavior, original rich draft/attachments, Session isolation and edit-error handling remain.

## Validation

- `python -m py_compile packages/web/server.py packages/core/worker.py`: passed.
- `node node_modules/typescript/bin/tsc -b`: passed.
- Changed-file ESLint (`InputRow.tsx`, `services/api.ts`, `queueStore.ts`, `types/index.ts`): passed.
- `node node_modules/vite/bin/vite.js build`: passed, with >500 kB chunk warnings.
- `git diff --check`: passed.
- No test was added, modified or executed in this increment. No compressed-asset verify script was run, per follow-up scope. Build compression generation is not a verify result.

## Unverified boundaries and delivery scope

No browser/mobile interaction, actual HTTP editing, restart/persistence, forced persistence failure/rollback, concurrent clients, attachment registry failures, provider dispatch or real item dequeue was exercised. The matrix reports production source paths and resulting code semantics, not measured runtime outcomes. Existing test mocks that return only expiresAt or assert the old user-task policy/request shape will need contract updates before a future authorized test run. Backend/frontend must deploy together; older backend responses lacking full body cause an explicit edit failure rather than preview overwrite.

Single-item full-body responses intentionally scale with that body's size; lists remain bounded. JSON editing and fixed attachment structure require user care; save errors retain content. Frontend settings keep the previously disclosed best-effort persistence/multi-client ordering limits.

No stash, integration, main/practical operation, push/tag/release, real 8767/8768 service access or user-data operation. Build output stays local/uncommitted; prepared dependency junction/marker remains untouched. MA owns review, integration and practical build.
