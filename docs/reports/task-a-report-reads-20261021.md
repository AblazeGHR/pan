# A8 partial candidate: own report subscription status and read-only next

Worktree `D:/project/pan-worktrees/task-a-history-read-20261021`; branch `feature/task-a-history-read-20261021`; base `978beef4680f33bed05c156a7592e6b075db3b46`; initial status clean. This is a fast-forward candidate for the two determined read operations only. **A8 ack is not complete and is not exposed.** No other A item or Action change is included.

## Definition and existing lifecycle audit

PAN_SMA action-dispatch-20261021.md identifies historical A8 as report_next/ack. The main workflow overview describes report subscription status and explicit ack/next tools, without defining an acknowledgement state transition. Current source confirms:

- report_subscribe claims/maintains the managed relation and persists the manager's report_subscriptions; unsubscribe removes only that subscription.
- Worker completion writes an existing q_... id/queueItemId to the manager's canonical queue_pending, subject to existing locks/policy. queue_pending is the only pending-body store.
- queue_delivery_ledger/sent_to_cli proves actual handoff, independently of provider/model later consumption or MA business review. It is not an MA processed/ack state.
- self_queue_read projects eligible own rows; self_queue_consume can Steer, wake, start or restart and actually hand off the original item, returning consumed only with a durable receipt. It is not a read/ack alias.
- There is no report-specific acknowledged/handled state whose meaning, persistence, retention and auto-delivery interaction can simply be reused.

No ack mutation was inferred from a queue-delete API or invented by changing deliveryState. A concrete decision question was presented while implementing the independent read work; no answer arrived before this candidate was prepared.

## Delivered read operations

`report_status()` returns own sessionId, sorted persisted reportSubscriptions and agentReportsPaused. These are configuration facts, not target liveness, report production or delivery guarantees. It neither claims nor repairs/subscribes targets and does not inspect their Sessions.

`report_next()` peeks the first visible, addressable child-agent report in current queue order, returning `{ok:true,report:{queueItemId,...,status,result}}`; otherwise report=null. The existing child-report classifier excludes ordinary tasks, notice/Job and channel records. It uses the persisted ID (including existing legacy id aliases), skips rows without an identity, and never allocates an ID. The returned body/identity metadata are private copies of the canonical own queue row; no source Session history/name is fetched.

Both tools have no Session arguments. They extend the existing /api/self-queue/{operation} bridge with report-status/report-next and reuse bound stdio identity, loopback/local-process checks, no browser Origin/Fetch and resolvable own caller. New operations require exactly an empty JSON object, rejecting Session/item override arguments. Remote/SSE cannot borrow a local Worker's identity.

Next reuses self_queue._eligible and its actual handoff visibility gates, including readonly, Pause, native/Manual owner, Illegal/manual/Stop-report row locks, edit leases (including another row's lease on the native running path), protocol holds, non-queued delivery states and retry backoff. It does not infer new locks from the future report policy. Eligibility uses a candidate copy so legacy alias insertion does not mutate a live queue row.

Reading does not reserve, advance, ack, delete, unlock, reorder, persist, enqueue/re-enqueue, wake or send. Repeated next returns the same first visible row until the existing queue lifecycle advances. Already handed-off reports are not replayed from the ledger/history. Hidden/missing/legacy-unaddressable/already-removed states do not reveal queue IDs/counts/positions; report=null only states that no visible addressable report was found. Normal report subscription events remain the wakeup path; this tool does not introduce polling.

## Ack proposal and decisions for MA

Recommended immediate acceptance scope: accept/review the two read tools independently and keep ack deferred. Do not use self_queue_consume or deletion/unlock to make next advance.

Before implementing ack, choose its business meaning:

1. **MA processed marker only:** record processing against the original queue ID in the existing canonical row/receipt lifecycle, without another inbox/body store. This does not by itself stop automatic CLI delivery; queued reports could still be handed off after a tool read. Decide whether queued ack is allowed, where the marker survives row removal, and retention/idempotency rules.
2. **Finish pending report delivery after explicit tool read:** requires an explicit distinct MCP/MA acknowledgement terminal receipt plus atomic retirement of that same canonical pending row, without pretending sent_to_cli or sending again. This would be a newly authorized queue lifecycle transition, not current behavior; it must preserve visibility/locks and race safely with automatic reservation/writing/unknown handoff. No such transition or deletion is implemented in this candidate.

For either choice, define behavior for already handed-off reports, hidden/stale/unknown IDs and duplicate ack; whether a successful read proves enough to permit ack; the durable receipt/retention owner; and precedence against concurrent automatic handoff. These are the exact gaps preventing a safe report_ack implementation from existing contracts alone. No report_ack placeholder tool or pretend-success response is registered.

## Evidence

Final isolated command, exit 0:

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_report_reads.py tests/test_self_queue.py tests/test_mcp_handbook.py tests/test_sma_manifest_prompt.py -o addopts= -q --junitxml=artifacts/task-a-report-reads-junit.xml -o junit_family=legacy
```

**88 passed, 0 failures/errors, 0 skipped**: 23 new read/gate/protocol cases, 50 existing self-queue consumption/gate/bridge cases, 13 handbook cases, 2 manifest cases. [JUnit](../../artifacts/task-a-report-reads-junit.xml), [test output](../../artifacts/task-a-report-reads-tests.txt), [new regressions](../../tests/test_report_reads.py).

Coverage proves repeated peek identity/body stability, private response copies, unchanged queue/ledger/history/revision/subscriptions and on-disk bytes, cold reload, missing caller, all listed gate classes, visible report selection among other message kinds, no replay after canonical removal, no invented legacy identity/alias mutation, own loopback identity and body validation, remote/unbound MCP rejection, and existing consumption compatibility. Test guards prohibit provider spawn, wake/start/restart, receipt save, enqueue and self_queue.consume from the new read paths.

Real integration used owned Uvicorn loopback listener **127.0.0.1:64869**, test PID **38848**, the actual self-queue HTTP handler and installed MCP client initialize/list_tools/call_tool over SDK in-memory transport. MCP used real urllib HTTP with bound own identity. It verified no-argument schemas, absence of report_ack, repeated same-report reads, locked-report hiding and byte-identical storage. Owned thread/socket stopped and port closure asserted. Production lifespan was disabled; no provider was started. External stdio/SSE deployment is not claimed.

Compileall, manifest JSON parsing and final diff whitespace checks pass. Two existing warnings remain (pytest-timeout config unavailable; Pydantic lifespan forward reference). No full suite, paid model, real provider, production service lifecycle or human acceptance was run.

## Documentation and boundaries

Tool descriptions, manifest hints, repository Pan skill and HTTP reference document the precise read/consume/ack distinctions and partial A8 status. User-level skill/reference sync remains MA work after acceptance; no installed skill was edited.

No main/practical movement, push/release, existing-service restart/operation, access to 8767/8768 or sub-agent dispatch occurred. No new inbox, acknowledgement store, polling or report lifecycle mutation was created. The remaining work is the ack decision and its separately authorized implementation/verification, plus MA acceptance/integration of this read-only candidate.
