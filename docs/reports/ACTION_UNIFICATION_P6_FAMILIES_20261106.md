# ACTION-UNIFICATION-20261104-P6 revision 4 — family continuation

Parent candidate: `10024df05f071cc26f114b150b6126a597f3876f`.
Branch: `implement/action-unification-p6-20261106`.
This is an additional reviewable candidate, **not P6 completion or migration acceptance**.
The earlier reports and red evidence remain historical evidence; this report
supersedes their statements only for the successors explicitly tested below.

## Authority and identity

No engine, ledger, scheduler, grant, or background service is added. Direct calls
use the existing `final_operations`; configuration and indexing join the existing
owner-fenced FinalService transaction. Jobs retain their existing Attempt/effect
identities. `TypedExecutor.execute_provider` shares the existing Session/assign/
channel providers with a direct operation identity rather than a fake Attempt.
Each provider call checks the owner, Action lifecycle and current permissions.
Broadcast/resume child receipts checkpoint before another target is attempted.
Unknown/partial calls are queried using their original operation and never retried.

Source enrollment still requires a genuinely fresh cooperative directory.
An unknown writer, unknown registry fields, mismatched release or unsupported
source graph blocks the entire handoff; no source JSON bytes or historical release
files are rewritten. System/non-Group Actions do not become old Group members:
the original Group validation (`automation=False`) is retained.

The public system Action management restriction is unchanged. An initial attempted
change broadened user system creation and failed the original P1 protection test;
that change was removed. Only exact mapped legacy system enable/disable uses the
internal lifecycle adapter, preserving the old lifecycle/manageSystemActions gates.

## Compatibility matrix and named evidence

All named tests below are in `tests/test_action_unification_p6_families.py`.
Mock/provider-port evidence is explicitly different from live Worker/QQ evidence.

| Original behavior / entry | Canonical successor and retained fields | Evidence / remaining boundary |
| --- | --- | --- |
| Fixed GET `/api/sessions`, `/api/models`, `/api/workspaces`; old Action CRUD; Group build/catalog/invoke | `api.get` calls original bounded `provider.execute`; old query defaults, business `requestKey`, `content`, `structuredContent`, error text; outer key remains separate | `P6_API_01` ×3: real isolated HTTP, adoption, edit, fixed original release, cold no-replay; `P6_API_02`: route/argument override denied |
| Adopted API Group standalone/native/gateway | Same original alias, flat schema, original release, operation receipt metadata | `P6_API_03` ×3: actual subprocess stdio + trusted IPC + random-port HTTP; repeated outer key causes one GET |
| Session Operation CRUD/lifecycle and direct invocation | `session.pause-everything` reuses `execute_dynamic` and production `automation.pause`; original deduped target selection, per-target `queueRevision/paused`, matched/succeeded/failed/targets | `P6_SESSION_OPERATION_01/02`: checkpoint, partial failure, owner loss, cold original identity; provider port only; actual Worker/GUI and Job/Trigger delivery still unverified/unwired |
| Product system message/broadcast/scheduled assign | Exact immutable `legacy.session-message`, `legacy.session-broadcast`, `legacy.scheduled-task` schemas; original typed Session/assign primitives and queue/task receipts | `P6_SYSTEM_01` ×3 and `02`: no fake Attempt/Job, fanout checkpoint, revoke between targets, no-replay; injected typed provider ports, not real Workers |
| Mapped standalone system Action old HTTP lifecycle | Old enabled projection, separate canonical lifecycle, same logical Action | `P6_SYSTEM_03`: fresh cooperative source with an actual immutable system Action, explicit standalone mapping, adoption, old HTTP disable, missing manageSystemActions denial, source bytes unchanged |
| Scheduled union `send_session`, `resume_legal_running`, `shell` | Existing Session send/Wake and `process.shell`; selected resume targets frozen in original operation; shell text remains opaque | `P6_SYSTEM_04` ×3: real shell/process cleanup + effect count, provider-port Wake/send, cold no-replay |
| Scheduled `send_qq` | Existing `_channel` and strict original typed target validator; raw provider detail/message/error retained | `P6_CHANNEL_01`: injected lost-response/unknown provider, one call, cold same receipt; actual QQChannelProvider/network delivery not exercised |
| Fixed template-producing Action | Explicit reviewed canonical TemplateRef plus original `jtpl_`/revision in `legacyTemplate`; existing `instantiate_template` transaction/composition; original `ok/jobId/created/jobCompleted/templateId/templateRevision` | `P6_TEMPLATE_01`: real canonical Job creation (one), cold no-replay, explicit raw schema projection, unmapped old CRUD rejection. **Not an old template-registry adoption test** |
| Mapped revision/lifecycle changed before early compatibility index exists | Read-only reconciliation from original handoff operation/plan/source hash, original fixed Action version, exact current canonical operation receipt, current head/stateRevision and global registry CAS | `P6_RECONCILE_01/02`: changed revision+disable, same logical identity, stale plan denial, missing receipt denial, cold same operation, no source write/effect; offline CLI exposes dry-run/apply |
| Multiple serial stdout values in an upgraded flat Group | Explicit `execution.config.legacyOutputMerge="concat/v1"`; same command primitive; stdout concatenated in ordinal order then parsed once using old JSON/text format; original per-command receipts retained; aggregate 512 KiB/encoded result limits retained | `P6_MULTI_01` ×2: actual processes, split JSON/UTF-8 and text bytes, canonical preview/apply/publication, old build entry for reviewed JSON successor, cold no-replay and old release pin |
| Existing single-command legacy CRUD view after multi-command upgrade | Old single-command schema cannot encode a list; preserve old raw descriptor with explicit `preservedActions` reason while the reviewed release remains callable/buildable | Original `P6_COMPAT_04` still proves undeclared merge blocks; no assertion removed. Full multi-command editing through old single-command GUI is a concrete representation gap |
| Existing command adoption, dynamic entry, cancel, notifications, history | Existing candidates retained | Original P6/P6-compat tests rerun, including all three stdio modes, crash/writer cuts, old release pin, original cancellation receipt, peer timeout, notification-once and history/source fields |

## Offline reconciliation usage

Use the same explicit offline roots as adoption. Default dry-run:

```text
python scripts/migrate_action_groups.py --source-root <source> --canonical-root <canonical> --authorization-root <auth> --reconcile-actions --output <outside-roots-plan.json>
```

Apply requires `--reconcile-actions --apply --plan <reviewed-plan> --request-key
<original-key> --expected-owner-epoch <epoch>`. `--status-key <key>` queries the
existing operation. A cold host may also query a completed same-key reconciliation
without claiming writer ownership. Different content under that key conflicts.
No reconciliation can enroll unknown source writers or invent a missing receipt.

## Still required for the full approved matrix

These are implementation/acceptance gaps, **not a declaration that simple records
are complex or that unsupported is a valid replacement**:

1. Complete legacy `jobTemplates/templateVersions` registry/config/reference-graph
   adoption. A real old template Action cannot be saved without that graph. The
   first test exposed this; it was retained as red evidence, and the successful
   template test is accurately limited to the explicit canonical successor. The
   whole-source handoff continues to block such a registry. Existing template
   CRUD/schema, scheduling/completion/notification policies must be translated
   through original composition primitives, not omitted or replaced by a fixture.
2. System background-process monitoring/log/history/recovery and protected
   main-lifecycle must reach their existing canonical Job/independent executor or
   lifecycle supervisor. This candidate does not pretend the synchronous direct
   command port preserves those contracts. Full real sources containing these
   records remain blocked pending their explicit mapping; this is unfinished work.
3. API GET/session-operation/system/template direct successors need full canonical
   Job/Trigger frozen-Attempt delivery wiring and per-entry legacy Trigger/Job CRUD,
   cancellation, notification and history mapping. The newly shared typed provider
   primitive is reusable; adding a second dispatcher/ledger is not authorized.
4. Complete the old API/GUI/MCP entry inventory for these families, including real
   core stdio/direct successors, GUI editing, real Session queue behavior and actual
   QQ provider receipts. API Group three-mode stdio is proven; injected Session/QQ
   provider tests do not establish those acceptance layers.
5. Explicit new Template mappings/config edits and old multi-command GUI editing
   require reviewable schema representation. A fixed canonical TemplateRef whose
   current revision/digest changes blocks creation instead of silently selecting
   latest; retention of old canonical Template versions needs its existing storage
   contract addressed. Do not overwrite the old mapping or replay a prior creation.

No real writer eligibility, real profile migration, production service/browser,
model Worker, Linux/macOS, remote CI, deployment or human acceptance is claimed.
No React source changed; no frontend build is required for this backend slice.
Normal hooks, source hashes, exact final test counts and protected refs are recorded
in `evidence/action-unification-p6/families-validation.json`.
