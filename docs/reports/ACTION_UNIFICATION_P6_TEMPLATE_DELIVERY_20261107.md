# P6 revision 4 — Template graph, frozen family delivery and old Edit continuation

Parent: `502ed4556a0959c09af3871a788c7c48f7b07682`.
Worktree/branch remain the original P6 checkout and `implement/action-unification-p6-20261106`.
This candidate advances the approved matrix; **P6 overall is still incomplete**.

## What now has a working successor

| Original capability | Successor / exact retained semantics | Named evidence |
| --- | --- | --- |
| `jobTemplates` complete records and `templateVersions` counters | Whole-source reviewed adoption stores original config, graph, generation and counter/tombstone bytes in existing canonical documents; source handoff, native writer fence and operation are the original Group protocol | `P6_TEMPLATE_02/03`: actual `Templates.put/delete`, actual old template-producing Action, old Group, policies/maxRuns, tombstone, mismatch/unknown preservation |
| Template graph, nested Action → Template → Action references | Original `Templates.view`/`check_action_graph`; pinned current Action mappings; canonical definition checked against the existing mapper's pure `config_job` and `schedule` functions | `P6_TEMPLATE_06`: actual nested source records adopted, later old Action edit creating a cycle rejected by the original graph validator |
| Template policy compatibility | Exact overlap mode/concurrency, recovery attempts/backoff/reset, notification states/targets and run limit checks; retention and bounded queue context are explicit reviewed inputs, never invented writer attestations | `P6_TEMPLATE_02/03`; original raw config remains present alongside its verified canonical definition |
| Multiple once/interval/cron schedule compositions | Original native TimeProvider/composition; every schedule requires reviewed context; explicit anchors cannot replace existing anchors; disabled schedules remain in the original record and produce no Creator | `P6_TEMPLATE_04`: actual TimeProvider schema validation and two native Creators/Triggers from three original specs, including one disabled spec |
| Crash during Template handoff | Same source freeze, owner generation, Group operation and transaction; no template instantiation during migration | `P6_TEMPLATE_05` ×3: source-frozen/before-commit/after-commit, original operation recovery, zero created Jobs |
| Fixed Template Action creation | Existing `instantiate_template` composition; original `jtpl_` and revision remain in output; explicit original mapping/snapshot checked before creation | `P6_TEMPLATE_02`, `P6_DELIVERY_TEMPLATE_01/02`: one child creation per original direct/Attempt/Event identity, cold no-replay |
| Different source activation policy under a shared TemplateRef | Reject ambiguity before handoff; never choose the first source's enabled policy | `P6_TEMPLATE_07` |
| API GET through a canonical Job | Existing bounded API provider, exact frozen ActionRef, original Run/Attempt/effectKey and final ledger; family checkpoints are inside that Run plan | `P6_DELIVERY_API_01` ×2: real random-port HTTP, newer head does not replace frozen Step, actual lost HTTP response stays uncertain, cold no-replay |
| System Session/scheduled/channel families through Jobs | Same identity-neutral primitive now serves direct operations and original Job Attempts; no fake Attempt, helper Job or effect cache | Existing P6 family tests plus new delivery tests; raw provider receipts and target checkpoints retained |
| Original Session provider through real IPC | Actual canonical core stdio → trusted IPC → Job/Attempt → `WorkerSessionProvider` → original `worker.send_session` → persisted queue; paused synthetic target prevents CLI spawn | `P6_DELIVERY_SESSION_01/03`: original queueItemId, original Run/effectKey, one queue item after same-key repeat |
| Session Operation direct Trigger | Original Event/Trigger operation, frozen action/arguments/event, original `execute_dynamic`, production `api_session_queue_paused` | `P6_DELIVERY_SESSION_02`: real queue pause persistence, original selection checkpoint, cold Event dedup, zero helper Jobs |
| QQ scheduled union through a Job | Original QQChannelProvider → original scheduled sender → real loopback HTTP plugin → OneBot channel; original acknowledgement/error fields and actual Attempt-bound no-send proof | `P6_DELIVERY_CHANNEL_01` ×2: synthetic gateway acknowledgement and disconnected producer proof, one HTTP request/no replay; **not real QQ network delivery** |
| Scheduled shell cancellation | Original process primitive and cleanup proof settle the original canonical cancellation operation | `P6_DELIVERY_CANCEL_01`: actual owned long process, explicit Run cancel, confirmed tree cleanup, same cold receipt, no second Attempt |
| Family notifications and history | Original final outbox and `_send_final_notice`/`enqueue_notice`; same Run/outbox/recipient/queue association; original return shape retained | `P6_DELIVERY_HISTORY_01`: real durable notice queue, repeated drain once, cold Run notification projection; no invented `eventId` field on the ordinary enqueue return |
| Old GUI Edit for multi-command/advanced mapped definitions | Additive read-authorized `canonicalActions` projection supplies the full canonical head/definition; old Edit reuses `CanonicalDefinitionEditor`, preserves observed CAS and operation query identity, saves a new draft; old raw execution remains byte-preserved | `ActionsMappedView.test.tsx`: full descriptors/environment/timeout retained and refresh cannot silently advance CAS; discard behavior retained |
| Production old GUI edit | Real production assets, real isolated Pan HTTP and Chromium; genuine fresh cooperative old Group adoption followed by serial successor publication | `action-p6-mapped.e2e.mjs`: original logical ID, revision 2 CAS, both descriptors retained, no command execution, desktop/mobile screenshots and no browser exceptions |

## Migration input and authority

The existing offline `migrate_action_groups.py` dry-run/apply remains the entry.
Action mappings are unchanged. Each Template adds a reviewed mapping row:

```json
{
  "kind": "template",
  "templateId": "jtpl_original",
  "revision": 1,
  "definitionDigest": "<digest of complete original Template record>",
  "templateRef": {"id": "<published canonical Template>", "revision": 1, "digest": "<digest>"},
  "policies": {"overlap": {}, "recovery": {}, "notification": {}, "retention": {}},
  "schedules": [{"policy": {"providerRef": {}, "configSchemaRef": {}, "requiredCapabilities": [], "advanceMode": "grid", "dstPolicy": "gap-skip-fold-first", "graceSeconds": "60", "pausePolicy": "advance"}}]
}
```

The example is a shape, not an executable attestation or a usable plan. Empty
policy objects are rejected. `schedules` has exactly one context per original
spec (including disabled specs); an absent interval anchor needs explicit
reviewed context. Canonical Template/config/reference equality is checked on
dry-run and again under the original handoff fence before apply. Provider and
timezone availability are checked before ownership changes. Unknown writer,
unknown registry fields, damaged/unknown graph, changed generation, mismatched
policy/ref/counter or a missing mapping preserves the entire source and blocks.

`templateVersions` was verified from actual source: it is a monotonic revision
and deletion tombstone map, not historical definition storage. No nonexistent
version snapshots are claimed. Canonical published Templates are immutable;
the attempted test edit correctly failed and that red is retained. No public
Template body field, system grant or Action publication permission was added.

## Verification boundaries and original reds

The Windows PATH Python lacked `nonebot` and IANA tzdata. Original errors were
retained. QQ requirements plus tzdata were installed only in the ignored owned
`.tmp/p6-env`, inheriting existing test dependencies; no base environment was
modified. Earlier timezone validation used an existing environment's zoneinfo
path; final family/QQ tests use the owned environment's tzdata.

The broader migration suite initially failed 19 times because two test
Collectors did not accept the existing `publish_action(enabled=...)` signature.
The product registration method's AST is identical to parent `502ed455` (hash
recorded in `templates-collector-baseline.json`). Both Collectors now record the
real enabled value. No behavior assertion or authority check was removed.
The red combination is retained alongside the successful rerun.

The first browser run rejected Windows venv launcher PID versus actual Python
PID before any page edit. That owned process exited; direct interpreter launch
then retained exact PID/runtime/checkout ownership checks. A mobile test then
tried the desktop-only Edit icon: the actual visible Action-name entry was used
on the successful rerun. Original browser red JSON/screenshots are retained.

Production build includes TypeScript, Vite and compressed asset verification;
the ordinary hook is required. Final XML/counts, commands, package versions,
source hashes, screenshots and unchanged protected refs are recorded in
`template-delivery-validation.json`. Source, injected/provider tests, actual
IPC/HTTP, production Chromium and external-network acceptance remain distinct.

Final combinations: **367 Python tests passed** (90 + 137 + 93 + 47), plus
**28 frontend tests passed**, with no failed/error/skipped cases in the final
groups. Two additional targeted malformed-reviewed-record checks passed after
strengthening their input to match the malformed record's actual digest, rather
than merely triggering a missing-mapping rejection. Production Chromium passed
on the final sources; the old JSON bytes/Group release remained unchanged, and
the new draft was correctly projected disabled at revision 3. Build verified
90 compressed assets. IANA/provider availability and the raw counter/generation
types remain fail-closed migration prerequisites.

## Remaining approved work — still blocks overall completion

1. **Old Template HTTP/MCP CRUD and instantiate entry transfer is now covered for
   the reviewed compatibility shape.** The old `jobTemplates/templateVersions`
   ID/revision/generation envelope, display overrides, delete/retire alias,
   published-successor edit and instantiate replay now use the FinalService
   owner/receipt path; focused HTTP and stdio MCP evidence is in the follow-up
   report. Remaining work is full inventory of retired display/return aliases and
   complex legacy fields outside this reviewed shape. Published canonical Template
   immutability remains enforced through successor allocation.
2. **Event Template composition:** explicit Creator-ID selectors and scalar query
   fields now map to canonical reference Creator specs and Trigger eventFilter.
   Complex/unknown selectors or query objects remain preserve/blocked; broader
   external creator composition coverage still needs a reviewed mapping.
3. **System background-process/main-lifecycle:** retained old background-process
   GET/cancel now projects canonical FinalService process Jobs, Run history and
   owner-fenced cancellation; the existing independent process/log/recovery and
   fixed main-lifecycle supervisor remain authoritative. Full old writer creation
   transfer and every legacy monitoring field still need a separate acceptance
   pass. No synchronous command wrapper is presented as replacement.
4. **Complete old entry cancellation/notification/history aliases:** the original
   canonical controls/outbox/history now work for newly wired families and have
   named evidence above. Full legacy Job/Scheduler/Trigger API/MCP field and entry
   inventory, including live retained old jobs, still needs transfer/acceptance.
   The Scheduler follow-up is a partial candidate only: see
   [retained Scheduler report](ACTION_UNIFICATION_P6_SCHEDULER_RETAINED_20261107.md).
   It adds reviewed successor queries/explicit Run cancellation, does not adopt
   old Trigger executions, and retains seven reproduced Template baseline reds.
5. **Old UI advanced operations:** Edit now preserves the full definition. The
   complete old duplicate/new/delete/lifecycle/Template flows for advanced schemas
   need their own compatibility checks; full parity is not claimed from Edit.
6. **Acceptance:** the real Session queue backend/IPC and QQ plugin HTTP chain
   are proven with owned synthetic targets/gateway. Actual model CLI consumption,
   real QQ account/network delivery, real data migration, Linux/macOS, remote CI,
   deployment and human acceptance remain unverified.

No push, main/practical advance, real profile/data access, protected-port operation,
or second engine/ledger/scheduler was performed.
