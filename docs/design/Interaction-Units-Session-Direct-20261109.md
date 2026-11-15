# Interaction units: Session Action direct invoke closure — 2026-11-09

This follow-up extends stage `79071ee95c953e6a8de4efa43c3a8446201cd960` in the same worktree/branch. The original 107 backend / 42 frontend / production-browser evidence remains under `_e2e_tmp/interaction-units-20261109/`; it is not counted as this follow-up's tests. No protected ref, push, DS Action/Jobs UI, agentflow or real profile operations.

## Finding and implementation

The earlier blanket Session direct-invoke limit was too broad. `TypedExecutor.execute_provider` already accepts the original `CommandContext` operation identity without a Job, Run, Attempt or effect cache. `_invoke_provider` invokes its boundary before each provider call. `_session_broadcast` deduplicates targets and checkpoints each fanout receipt before the next call. `WorkerSessionProvider` and `WorkerAssignProvider` retain original queue IDs, client-message/task identity and Worker gates.

The thin adapter in `packages/actions/session_invocation.py` validates literal Session arguments through the existing canonical source policy, target existence and provider availability. `packages/actions/invocation.py` adds the ordinary Session operations to canonical direct invoke and passes the prepared plan to the existing provider port. It persists the existing final_operations prepared/calling/settled record, original providerReceipt/providerReceipts, and checks owner epoch, ActionRef/version, current grants, source and targets at each provider boundary. The Card adapter supplies an internal effect_guard that rechecks its frozen source managed scope between fanout calls. This callback is not a wire field, stored authority or new protocol; cold unknown operations remain query-only.

Capabilities are available before creation through the same MCP:

`interaction_card(operation="capabilities")`

It returns each current fixed ActionRef, operation, inputSchema, bindable boolean and concrete reason under the current stdio Agent's grants. The existing `/api/interaction-card-tools/capabilities` trusted caller gate is reused; there is no parallel tool. Creation independently validates the fixed ref and arguments, read/invoke/manageSystemActions, job.execute for Session effects, source identity, source managed scope, target existence and provider availability. The UI marks a trusted fixed execution capability and disables historical options missing proof with an explanation. An unsupported runtime release cannot be persisted as a newly selectable Card choice.

## Exact product compatibility matrix

| Action ID | Direct fixed arguments | Admission/receipt |
|---|---|---|
| product_session_message | sessionId, text | Original WorkerSessionProvider, canonical operation receipt |
| product_session_broadcast | unique sessionIds, text | Original deduplicated fanout and per-target checkpoint |
| product_session_message_v2 | sessionId, text, optional source/sourceSessionId | Original source policy: Agent cannot assert user or another Session source |
| product_session_broadcast_v2 | sessionIds, text, optional source/sourceSessionId | Original fanout; source scope/grants checked between effects |
| product_assign_v2 | sessionId, text, optional source/sourceSessionId/taskId | Original WorkerAssignProvider; omitted taskId derives from original operation identity |
| product_session_message_runtime_v1 | **Job-only runtime contract**, rejected at Card creation | See runtime fields below |
| product_session_broadcast_runtime_v1 | **Job-only runtime contract**, rejected at Card creation | See runtime fields below |
| product_assign_runtime_v1 | **Job-only runtime contract**, rejected at Card creation | See runtime fields below |
| process / process.command / process.shell / api.get | Existing fixed arguments | Existing canonical direct adapters preserved |
| job.create | Fixed `jobDefinition` and optional `start` | Original canonical Job creation/admission transaction; creates the explicitly selected Job itself |
| template.instantiate | Fixed reviewed TemplateRef and original composition mapping | Original Template composition/creator/trigger adapter with source mapping and lifecycle grants rechecked |

These Session effects still require the existing action read/invoke, system-action management when applicable, and job.execute grants. There is no new permission shortcut. Source transport identity is not inferred from BTW or action arguments. Explicit rejection creates no invoke intent. Accepted Card delivery proves the original canonical operation exists; an unknown/partial provider outcome remains visible in that operation and cannot trigger a resend.

## Why the runtime releases cannot be called as ordinary fixed Session Actions

The three `*_runtime_v1` releases declare `runtimeBeforeAcceptance`:

- owner: delivery-manager; phase: before-queue-acceptance;
- fields: `[sessionId,text]` for message/assign, `[sessionIds,text]` for broadcast;
- fence: per-target-attempt-effect; unknownPolicy: query-original.

The consuming Job must opt into `deliveryRuntime.phase=before-reservation` and named inputFields; Step bindings identify which target/text fields resolve from current Job defaults. `SessionDelivery(service, job_id, run_id, attempt, key)` attaches to the existing canonical frozen Run plan, `runtimeDelivery[stepId]`, pendingSessionAttempt and persisted child reservations. Its identity checks require the real attemptId/ownerEpoch/generation/leaseId and original effectKey; runtime receipt projection preserves each child's effective target and acceptance.

A Card supplies a fixed ActionRef, literal arguments and operationId. It has no Job defaults, input binding declaration, frozen Run, running Attempt, lease or child reservation. Calling these releases as an ordinary provider plan would silently ignore their runtime declaration; constructing those fields would fabricate Job/Attempt identity and violate the no-hidden-Job boundary. The discovery and create errors name `runtimeBeforeAcceptance`, `Job deliveryRuntime`, `frozen Run` and `Attempt/effect reservations`. Their existing Job path and immutable releases remain unchanged.

`job.create` and `template.instantiate` are explicit supported effects. Their fixed graph is fully proved during card creation, including source references, nested explicit creation nodes, reviewed Template mapping, creator/trigger composition and current grants. Delivery reuses the existing `job_creation.create` or Template instantiate transaction and binds its own receipt to the Card operation. A requested `start` may be rejected by the original lifecycle gate after the Job itself is durably created; the canonical receipt retains both facts. No hidden wrapper Job or second engine is introduced. Other exclusions are semantic: legacy.scheduled-task still requires its scheduler lifecycle; session.pause-everything/resume-legal-running.v1 select global/current Session state without fixed Card target IDs. Unknown operation/config contracts cannot prove all effects from the Card's fixed arguments. Discovery reports each exact operation/reason. No Action identity is replaced with another product ID.

## Follow-up validation

Initial focused red: 9 failures / 1 pass at stage 79071ee9, preserved in `session-direct-red.xml`: five ordinary products rejected prematurely, three runtime errors lacked the precise explanation, and Session rejection could not be created.

Follow-up targeted backend command:

`python scripts/run_tests_isolated.py -- python -m pytest tests/test_interaction_session_invoke.py tests/test_interaction_units.py tests/test_interaction_canonical_binding.py tests/test_action_unification_p2.py tests/test_action_unification_p4.py tests/test_job_final_executors.py -q --tb=short --junitxml=_e2e_tmp/interaction-units-20261109/session-increment-backend.xml`

Result: **138 passed, 0 failed/error/skipped**. This is a related subset for the changed canonical invoke/CommandContext/provider paths, not a rerun of the prior 107-test selection or the full repository suite. New tests cover all five ordinary products against real paused Worker queues, lost canonical receipt then actual canonical-host/Card-store reopen and cold query only, no Job/Run/Attempt creation, runtime-release pre-create rejection, invalid ref/source/target/scope, scope revocation between fanout effects, unknown provider result with one call only, explicit reject with no provider call, full answer/reject BTW for a two-unit notification, four-unit summary excluding answers/BTW and exact submission-only reads.

Frontend related test command remains the two InteractionUnits/LockerView files: **43 passed**, including a new disabled-capability explanation test. TypeScript/Vite production build succeeds, with 90 compressed assets checked. Existing bundle/dynamic-import warnings remain.

Extended owned HTTP/stdio/production Chromium verifies capability discovery, all five ordinary products selected/submitted through the production UI, original queue bodies each once before and after server restart, all three runtime versions rejected by real MCP creation before display, and the previous single/batch/reject/BTW/V1/V2/TriggerRef/concurrent-CAS/submission-ID assertions. The fixture uses only an ephemeral non8767/8768 port, owned profile, fake CLI, and closes its browser/server/listener. Exact final-SHA rerun artifacts carry that SHA in their names under the same ignored evidence root.

Remote CI, other OS runs, real-provider consumption, real-profile migration, deployment and MA/human acceptance remain separate and unclaimed. Original stage evidence and red evidence remain intact.

The added cold-host fixture initially attempted epoch 0 against an existing epoch and correctly hit owner CAS rejection (five cases). The corrected fixture reads the durable current epoch before claim; no owner fence was bypassed. The red XML is retained as session-direct-cold-host.xml.
