# Session product closure candidate

Base: `67d125de794132f72011012d8c6814110aafe8ef` (fast-forward from
`84617fce8153d3b44c530b6f2053a751e5000b04` in the original clean worktree).
Branch: `r6/job-source-receipt-20261029`.

## Source mapping

| Product entry | Existing business boundary | Canonical identity / evidence |
| --- | --- | --- |
| `product_session_message_v2` | `WorkerSessionProvider` -> `worker.send_session` | Original clientMessageId derives from effectKey; recover omitted queueItemId through existing worker index. Source labels reuse worker.SOURCE_TYPES. |
| `product_assign_v2` | Default host now binds `WorkerAssignProvider` -> `worker.assign` | Explicit taskId is retained; absent taskId derives from original effectKey. Missing Session is rejected before spawn. |
| `product_session_broadcast_v2` | Existing ordered/deduplicated fanout -> `worker.send_session` | One Run/Attempt/parent effect. Child keys and original queue IDs remain inside its receipt; no extra ledger rows. |
| `FinalRuntime.query_original` | Original canonical aggregate | Resolves parent/child effect digest, worker client ID, queueItemId and taskId to original Run and parent receipt. It never sends. |
| `FinalService.recover_session_receipts` | Existing `_find_queue_item_by_idempotency` / original worker Queue | Default local host only. Uses frozen target/text/task identity, existing queue lock and original Attempt fence. Lost responses can settle durable enqueue from original rows; absent/unreadable/colliding evidence stays unknown. No remote provider protocol, new queue item or effect ledger. Mixed partial results stay uncertain under existing aggregate policy. |
| `FinalRuntime.handoff` | Declared top-level literal target of typed Session Actions | Resolves actual Action operation/schema, changes future targets only. Business JSON, sourceSessionId, input mappings, frozen Runs and notification identities are untouched. |

The old product Session releases remain immutable. New Session schemas admit
optional `source` / `sourceSessionId`, assign additionally admits `taskId`, and
the broadcast release accepts duplicates for the existing first-occurrence
dedup semantics. No frontend code changed: these fields use the product Action
schema/binding surface.

## Source and authority

Bearer authentication and existing capability/revision checks determine
authority, independently of source metadata. Authenticated users can select
existing message context; the original worker validates Session metadata and
queue gates. The trusted IPC currently authenticates a generic Agent, with no
bound Session identity. Such callers can submit automation messages with their
existing grants, but cannot assert user or Session source identity. Payload or
HTTP headers cannot construct the private IPC scope marker. No new grant or
Session ACL is introduced.

## Coordinated process publication only

MA supplied and P23 Session `ses_08c3e58bdc8a1fa2` directly confirmed this delta:
`product_process_logs_v1` copies `product_process_cleanup_v1`; only providerRef
changes to `pan.typed`, digest of `pan.typed.process-logs.v1`, generation 3, and
outputSchema adds optional object `properties.logRef`. No required field,
input/capability/effect/execution/completion/cancel/reconcile change. Existing
publication documents and frozen plans remain equal on repeated install.

P23 owns the runner/log transport and `read_process_log`/bounded GET methods.
Those changes are not present in this tree. No `_process` code, process helper
or import changed, and custom/in-process provider receipts gain no log fields.
Publication tests do not establish actual process log acceptance. Combine by
method/symbol ownership, not by copying either entire shared file.

## Verification

Final command (exit 0):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_session_product_closure.py tests/test_job_final_executors.py tests/test_job_final_http_service.py tests/test_job_runtime_registry.py tests/test_job_final_transports.py tests/test_source_metadata.py tests/test_unified_server_queue.py -q --tb=short --junitxml=evidence/session-product-closure/final-combined.xml
```

`final-combined.xml`: 244 collected, 242 passed, 2 existing explicit skips,
0 failures/errors. The skips are the two one-shot spawn tests retired by user.
The 12 new tests in `test_session_product_closure.py` use an owned Uvicorn TCP
listener on a random non8767/8768 port, authenticated HTTP requests, temporary
canonical/auth stores and real Session queue files. The real default worker
boundaries run against paused owned Sessions; spawn is forbidden. Supplier
connection/unknown/ack-loss conditions are injected at the worker boundary.
Server threads/listeners close in fixture teardown.

Compile check and `git diff --check` both exit 0. No React changes/build needed.

## Retained iteration evidence

- `focused.xml`: earlier 101-test green subset, before adding original queue-ID assertions.
- `affected.xml`: original-queue-ID query red in message/broadcast and a test assertion expecting a detailed HTTP auth message (the API intentionally sanitizes it). The former drove the adapter fix; the latter is now checked by structured denial plus a positive same-transport authorization test.
- `product-http.xml`: auth message assertion red retained.
- `product-http-final.xml`: test reused a create requestKey for a different Job, correctly rejected by canonical idempotency; test now uses distinct keys.
- `final.xml`: 243-test prior green (before the extra connection-loss test and source-enum contract assertion).
- `connection.xml`: isolated connection-loss/cold-original-query test, exit 0.

## Limits and boundaries

Durable queue admission is proven; supplier CLI consumption is unconfirmed.
No real supplier accounts, existing service/profile, 8767/8768, main/practical,
push, deployment or real-data migration was used. Linux/macOS/remote CI,
production browser interaction, combined P23 log execution and human acceptance
remain unverified. Custom remote providers have no invented reconciliation
protocol; unknown receipts are queried by original canonical identity and are
not automatically replayed.
