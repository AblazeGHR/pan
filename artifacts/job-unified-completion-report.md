# Unified Job completion candidate

Worktree `D:/project/pan-worktrees/event-trigger-core-20261022`, branch
`feature/event-trigger-core-20261022`. Starting HEAD was `c5ff36b78d9d1c42fbc76118e12fed83b06c1ed0`.
**Precisely tested product/test source: `10376d3329179d4d14d3526c83ee400e64b6a32e`.**
The final handoff commit adds evidence and documentation only; it does not change
the tested product/test source. This report supersedes the earlier stage report.

[Exact changed source/test file list](job-unified-source-files.txt) and
[machine-readable result/cleanup manifest](job-unified-validation.json).

## Implementation and executable acceptance matrix

| Requirement | Implemented files / behavior | Direct evidence |
|---|---|---|
| Unified Job, no purpose-kind new entry | `packages/jobs/action_templates.py`, `unified.py`, `packages/web/src/views/JobsView.tsx`, `components/jobs/UnifiedJobForm.tsx`: complete Action reference/declared arguments/trigger/completion/recovery/overlap/notification config; old kind is adapter metadata | `test_job_unified_contract.py`, `UnifiedJobForm.test.tsx`; real browser creates and runs System Action Job |
| System and user Actions, immutable product definitions | `packages/jobs/system_actions.py`, `packages/actions/store.py`, `executor.py`: five real registry definitions, protected edit/delete, trusted Job capability; no legacy Group bypass | `test_job_system_actions.py`, `test_action_ids.py`, `test_action_groups.py`; legacy Group/pin tests retained |
| Reference, per-Attempt snapshot, cleanup, Incomplete | `action_templates.py`, `action_runtime.py`, `core/background_jobs.py`: revision/digest/context/filled params; durable cleaning intent, cleanup-only recovery, terminal audit without definition; reference deletion/repair controls | `test_job_action_templates.py`: mutation/deletion barriers, storage failures, cancellation, orphan cleanup, independent Session progress; HTTP template test |
| Complete fixed Template CRUD/instantiate/delegated Action | Existing registry intents/revisions/idempotency plus `JobTemplateEditor.tsx`, fixed-template Action editor, protected API | `test_job_action_templates.py`, `JobTemplateEditor.test.tsx`, real browser save + fixed instantiate; unknown key reuse test |
| immediate/once/interval/cron/event/query | Existing scheduler entry conversion, same durable Event Store `_job` target admission/config revision/dedup; shared global scheduler gate; query checkpoint reused within Run | `test_job_completion_recovery.py`, `test_event_store.py`, `test_session_query.py`; actual running edge → persisted event → Job query → Pause |
| Ordinary vs monitoring + bounded recovery | Capability declaration; exact signal + tree-ended evidence; persistent backoff/count/stable reset; unknown never promoted to restarting | `test_job_unified_contract.py`, `test_job_completion_recovery.py`; real owned process monitor emits JSON signal and completes in one Attempt |
| Overlap/parallel | Existing per-Job claim/locks, forbid/queue/parallel1..32, bounded128 queued intents; limit reduction rechecked at final CAS; unrelated Jobs independent | `test_job_overlap.py` including deterministic validation/third-claim interleaving; Trigger concurrency/retry regressions |
| Result notifications | `action_runtime.py` durable per-Attempt outbox, existing Job events and `enqueue_notice`, independent recipients and ID/status-only body; legacy report routing retained | `test_notification_gate_retains_outbox_and_never_reexecutes`, legacy message/queue tests; repeat uses identical durable event key |
| Run/Attempt cancel/recover/uncertain | Stable Run + distinct Attempt IDs, receipt/backoff/cleanup audit; user-only failed retry and separate uncertain confirmation + expected revision; safe unknown process identity block | `test_job_completion_recovery.py`, `test_job_action_templates.py`, existing owner/PID lifecycle tests; no unknown automatic replay |
| All old entry effects admitted | `background_jobs.py` admits System Action Run/Attempt/snapshot before original process/message/scheduler/supervisor effects; original lifecycle/queue/identity gates retained; process child remains ordinary generic Job | `test_background_jobs.py`, `test_jobs_api.py`, `test_session_message_jobs.py`, `test_scheduler_dispatch.py`, `test_main_lifecycle_jobs.py`; isolated HTTP executes old process/message/broadcast/scheduled send |
| API/MCP/production UI | `action_api.py`, `mcp/server.py`, API/types/auth store, UnifiedJobForm/TemplateEditor/JobDetailDrawer, Action editor/Groups restrictions; disabled drafts only, 401/409 retained drafts, dirty protection, revision/Incomplete/cleaning/restarting/Attempt display |45 Vitest tests; `test_job_mcp_config.py`, schema/permissions tests; Chromium153.0.8010.12 at1280/390/320px with no horizontal overflow/page errors |
| Migration/docs | v0.4 contract, v0.1 core contract, HTTP tool guide and historical plan annotation | Conservative missing-policy defaults and malformed configuration tests; no implicit recovery or profile downgrade |

System mapping (actual execution admission, not projection-only):

| Old kind | Product Action ID | Existing effect retained |
|---|---|---|
| background-process | system_background_process | Owned runner/process-tree lifecycle |
| session-message | system_session_message | Ordinary single-session send/queue |
| session-broadcast | system_session_broadcast | Ordered ordinary per-target sends |
| scheduled-task | system_scheduled_task | Existing assign/send_session/resume_legal_running/shell/send_qq capabilities |
| main-lifecycle | system_main_lifecycle | Protected supervisor intent and lifecycle transitions only |

New generic Job/Template creation **rejects** the main-lifecycle Action; it does
not delegate supervisor authority. The protected legacy supervisor entry admits
its System Action audit/snapshot before effects. The retained `NewJobForm` supports
legacy record editing and compact legacy message composers; the primary New Job
route is the complete unified editor. Existing Actions/Groups routes remain.

## Commands, results and exact source

All commands ran in this tree, with read-only reused dependencies. No global
PYTHONUTF8/encoding override was applied. New harness text reads/writes and child
decoding explicitly use UTF-8. Python is the existing
`E:/software/miniforge/python.exe`; frontend cwd is `packages/web`.

1. **559 passed, 0 failed/errors/skipped, exit0**:

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_job_completion_recovery.py tests/test_job_overlap.py tests/test_job_system_actions.py tests/test_job_unified_contract.py tests/test_job_action_templates.py tests/test_job_mcp_config.py tests/test_jobs_api.py tests/test_background_jobs.py tests/test_session_message_jobs.py tests/test_scheduler_dispatch.py tests/test_main_lifecycle_jobs.py tests/test_scheduler_mcp.py tests/test_action_ids.py tests/test_action_groups.py tests/test_session_query.py tests/test_automation_concurrency.py tests/test_automation_retry_parallel.py tests/test_event_store.py tests/test_independent_job_service.py tests/test_independent_job_bridge.py tests/test_background_job_api.py tests/test_agent_notify.py tests/test_job_lifecycle_prevention.py tests/test_plugin_event_lifecycle.py tests/test_action_persistence.py -o addopts= -q --junitxml=artifacts/job-unified-final-python.xml
```

One existing Pydantic `IncompleteFieldDefinitionWarning` remains; it is not a skip
or suppressed failure. [JUnit](job-unified-final-python.xml), [log](job-unified-final-python.log).

2. **2 passed, exit0**, owned actual HTTP/fakeCLI/production Chromium:

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest tests/test_job_action_http.py tests/test_job_unified_http.py -o addopts= -q --junitxml=artifacts/job-unified-live/junit.xml
```

[JUnit](job-unified-live/junit.xml), [command log](job-unified-live/final-http.log),
[HTTP/Run/monitor/Pause/cleanup](job-unified-live/http.json),
[Template HTTP](job-unified-live/template-http.json),
[query HTTP](job-unified-live/query-http.json),
[browser receipts](job-unified-live/browser.json),
[browser command log](job-unified-live/browser.log).
The browser is spawned by that Python test using
`node packages/web/e2e/job-unified-live.mjs <owned-loopback-url> <owned-input-json> <artifact-dir>`;
its temporary test key is passed in process environment, never recorded here.
Screenshots: `job-unified-live/jobs-{1280,390,320}.png` and
`jobs-policies-{1280,390,320}.png`. Actual actions exercised: query Pause, legacy
ordinary messages/broadcast/process/scheduled send, new System process, structured
monitor completion, production UI create/run and complete Template instantiate.
Only deterministic fake CBC CLI and local Python commands are used, no provider.

3. **45 tests /7 files passed, exit0**:

```powershell
pnpm exec vitest run components/jobs/UnifiedJobForm.test.tsx components/jobs/JobTemplateEditor.test.tsx views/JobsView.bulk-search.test.tsx views/JobsView.schedule-shapes.test.tsx components/jobs/NewJobForm.selected-broadcast.test.tsx components/jobs/NewJobForm.resume-legal-running.test.tsx components/jobs/NewJobForm.scheduled-qq.test.tsx --reporter=default --reporter=junit --outputFile=../../../artifacts/job-unified-final-ui.xml
```

[JUnit](job-unified-final-ui.xml), [log](job-unified-final-ui.log).

4. `pnpm exec tsc -b` **exit0**; `pnpm build` **exit0**,84 precompressed assets
verified. Existing chunk size/dynamic-import warnings remain. [Build log](job-unified-build.log).
5. `git diff --check -- packages tests docs` **exit0**. Raw saved pytest/build
evidence retains tool-emitted trailing whitespace; it is not source formatting.
No full-repository test run was performed.

Final Python/HTTP/UI validations are on `10376d3329179d4d14d3526c83ee400e64b6a32e`.
Implementation commits: `c5c701d2` unified backend/UI and System Action execution,
`53157af7` shared scheduler gate, `e5171b75` same-Run confirmation after out-of-order
parallel completion, `8a9f0ea8` independent SQLite process host snapshot/cleanup,
`10376d33` bounded atomic registry sharing retry/storage503 distinction.
The evidence-only final commit retains the exact same product/test bytes.

## Failure evidence and corrections

Working experiments are retained separately and are not claimed as tested clean
SHAs. `job-unified-live/failed-browser-label`, `failed-textarea-label`, and
`failed-dirty-after-save` record real browser failures before explicit accessible
labels and parent dirty reset were fixed. The final browser passes both create
and the subsequent New Job/Template transition.

`job-unified-mcp-before-fix.xml` records collection failure from a newly introduced
MCP indentation error (fixed). `job-unified-compat-before-fixture.xml` and
`job-unified-recovery-before-fixture.xml` preserve failures from stale Event Store
worker substitutes missing the already-required host Session-root identity; both
substitutes now explicitly use owned temporary roots. Assertions were retained.
All these cases are included in passing final suites.

`job-unified-gates-before-fixture.xml` and `.log` retain the additional compatibility
failures:16 plugin tests used an obsolete worker substitute without its owned
Session root, and one notification test spied on `save_async` after the existing
queue writer moved to `_save_body`. The substitutes now declare their temporary
root; the persistence spy wraps the real ordered writer and retains the exact
single-save assertion. Product queue semantics were not weakened.

Additional safety fixes have explicit final regressions: unknown monitor receipt
cannot become automatic restart; frozen query is reused across monitor Attempts;
known/uncertain target receipts are not resent by System message confirmation;
lowering parallel capacity rechecks concurrent claim; event dispatch honors the
shared scheduler disabled gate. No skip/xfail/assertion weakening was added.

`job-unified-live/failed-registry-sharing` preserves a later actual Windows
registry replacement denial (WinError5), its failed JUnit and successful owned
cleanup. The old API misclassified that storage PermissionError as401. Registry
replacement/read now retry transient denial for at most90ms off-loop; persistent
write denial keeps canonical bytes unchanged and returns503, not an authorization
result. `test_action_persistence.py` deterministically verifies both transient
success and exhausted failure/cleanup as well as503 vs real401. Synthetic owned
Bearer text in that failure trace is redacted; no real credential was involved.

Independent process host admission uses the same `system_actions` definition and
snapshot/audit helpers in its existing SQLite transaction (`execution_audit`
additive column), without adding a registry or Pan runtime dependency. Terminal
runner writes atomically clear the executable snapshot along with status/outbox;
dead/unknown ownership also cleans without replay. Tests prove SQL admission
failure causes zero spawn, real owned immediate completion/cancellation cleanup,
live-runner restart continuity and UNKNOWN retry refusal. Existing rows gain no
invented Attempt or recovery eligibility during column migration.

## Migration, limits and unverified boundaries

- Existing APIs/kinds/data remain supported; no destructive migration or scheduled
  compatibility removal is introduced. Missing policy fields default to ordinary,
  no recovery, forbid overlap. Unknown policies are Incomplete/blocked. Mixed old/
  new writers or automatic downgrade are unsupported; back up profiles first.
- System definitions install only at owned runtime startup. Conflicting IDs fail
  closed. Product code owns their schema; user/Agent cannot promote system flags.
- Cross-file Action-reference creation uses the existing durable intent/recovery
  protocol, not a claimed cross-file atomic transaction. Cleanup failure remains
  visibly cleaning/pending; recovery retries cleanup, never the effect.
- A detached trusted child process keeps its original safe runner lifecycle after
  a host interruption; its parent becomes uncertain and deletes the executable
  snapshot. It is not automatically reattached/replayed as a new Attempt. Unknown
  process identity blocks explicit recovery until a trusted terminal receipt.
- Queue128, Attempt/event receipt10,000 per Job, live snapshot128, template256 and
  document1MiB bounds are enforced. Automatic receipt archival/operator reclamation
  remains outside this task; capacity is an explicit rejection, not silent eviction.
- Real main-service restart/shutdown, user Workers, real models/provider effects
  and user-profile migration were deliberately not executed. Protected lifecycle
  behavior is covered by deterministic existing supervisor/API regressions.
- OAuth/keyring/private exporter/official credential paths were not changed or
  connected. Account switching/quota production side effects are excluded.
- Production browser passed at desktop/320/390; full human interaction/accessibility
  acceptance and MA independent review are **pending**, not claimed by tests.

Both owned HTTP harnesses report `ownedProcessesRemaining:[]` and
`portReleased:true`; unified registry snapshot count at the end is0. Chromium is
closed in the harness finally path. Test-generated tracked legacy evidence was
copied into this batch and then restored; no other tree was changed. No protected
ports, existing services, main/practical/integration refs, push or release were used.
This batch stops here for MA review.
