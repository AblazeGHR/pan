# P6 legacy scheduled-task configuration and provider return candidate

Baseline: `1118134cfe7e9dfa5a24d8eec55e6cd6234072b1`.
Branch: `implement/action-unification-p6-20261106`; original isolated worktree.
This is a partial candidate for MA review. It does not claim overall P6
completion or legacy capability retirement.

## What changed

The old scheduler HTTP `POST /api/scheduler/tasks` and `PATCH
/api/scheduler/tasks/{taskId}` paths now use the existing canonical owner,
FinalService operation receipt, Job aggregate, Event Creator and Trigger.
The old validation and response field names remain: targetSessionId/text,
name/description, schedule, enabled/paused, maxRuns and misfirePolicy.
Creation requires an already-held canonical owner and allocates a new
canonical identity; it cannot enroll or adopt an unknown legacy writer.
Repeated requestKey returns the original operation and task identity.

The old pure task builder and patch/cursor calculation were extracted into
`scheduler.store.prepare_task` and `scheduler.store.plan_update`. A patch is
applied through one canonical Job configuration CAS and, when schedule state
changes, the Creator revision/lifecycle CAS. Existing frozen Runs/Attempts/
effects remain unchanged. The source handoff binding keeps the original JSON
bytes and identity; its `currentSource` records the latest compatibility
projection without rewriting the original file. GET/list/runs include newly
created canonical tasks alongside retained historical records.

MCP `scheduler_create` and `scheduler_update` are registered again with the
old flat argument names and map to the same HTTP routes. The transport allowlist
admits only these exact POST/PATCH paths; owner, permissions, requestKey and
revision checks remain in the canonical handler. Other retired legacy roots
remain fail-closed. Existing no-service behavior remains the old 401/410
retirement response.

Default legacy `run-now` now waits for the original admitted Run through the
existing dispatch lock and returns actual provider facts. HTTP still accepts the
additive `waitForDispatch` boolean; MCP defaults it to true. Repeated or unknown
request keys stay query-only and never re-submit a provider effect.

Provider projections preserve old fields while retaining canonical receipts:

* session assign/send returns the old dispatched/queued envelope;
* QQ returns targetType, targetId, botUin, messageId, providerReceipt and
  qqErrorCode/error when present;
* resume_legal_running returns matchedCount, per-session results and the
  canonical receipts from the existing dynamic selector.

These adapters call the existing TypedExecutor and provider paths. They do not
create a second queue, scheduler, receipt ledger or retry engine.

## Original identity, cwd and active resource reconciliation

The old shell validator still requires a non-empty cwd and resolves a relative
cwd against the original old process's working directory. A new canonical
mapping may use a relative cwd only when the existing durable process receipt
proves one consistent absolute effective cwd and identical argv for every
counted dispatch. The current process cwd is never used as historical proof;
omitted cwd remains blocked because the old validator rejected it.

An active old process can be reconciled only when the same canonical Run,
effect admission, `pendingProcessAttempt`, durable execution document, original
effect key, Attempt owner/generation/lease fence and supervisor plus child
process identity are present. The supervisor must still be alive and the
receipt must be in the original running phase. The handoff retains that Run and
does not start another process. Cold recovery continues the original execution
and settles it once. A journal row, PID, path, or copied command without this
identity proof remains unknown/blocked.

Pending old dispatch, notification/outbox, recovery intent, abandoned lease or
unselected sibling intent still blocks the profile writer handoff. The source
JSON and journal bytes remain unchanged. No automatic redelivery or settlement
was added.

## Evidence

| Layer | Result | Evidence |
| --- | --- | --- |
| New configuration + receipts + old scheduler + HTTP API | **75 passed**, 0 failure/error/skip | `evidence/action-unification-p6/task-config-final-combination.xml` |
| Old scheduler store CRUD/calendar regression | **37 passed** | `evidence/action-unification-p6/task-config-store.xml` |
| Real HTTP | Owned dynamic loopback: create, same-key replay, patch, stale revision, run-now, history, cold FinalService query | configuration tests above |
| Real standalone stdio/IPC | Create/update and default synchronous run-now through `scheduler_create`, `scheduler_update`, `scheduler_run_now` | `test_P6_CONFIG_05_real_standalone_stdio_create_update_default_sync` |
| Active original process | Relative cwd proof, supervisor identity, pending process admission, cold recovery and no replay | `test_P6_CONFIG_06_actual_original_process_relative_receipt_and_live_handoff` |
| Source/static | AST for 9 changed Python/test files, final contracts check, `git diff --check` passed | commands in delivery log |
| Prior QQ/real provider evidence | Existing owned OneBot HTTP/QQ suites remain the transport reference; this slice’s extra QQ probe reached the fixture assertions but its imported fixture closed a pooled client after its event loop, so it is not claimed as a clean new result | `evidence/action-unification-p6/task-config-providers-old.xml.gz` preserves the failed fixture run |
| Build/Chromium/remote CI/platforms | Not run in this slice | unverified |
| Real data/migration/deployment/manual acceptance | Not performed | unverified |

The old baseline red for create/update remains at
`evidence/action-unification-p6/task-config-original-red.xml`; it proves the
previous 410/retired route behavior. The implementation now restores the route
only when a canonical service is present, while the old no-service retirement
tests remain green.

## Remaining gaps

Pure legacy logs still cannot invent a missing canonical Run/Attempt/effect.
Active resources and pending dispatch/notification are only transferable with
the explicit retained identity/receipt contract above. Full multi-entry legacy
schedule editing, all historical provider-specific notification timing and
every old CLI/QQ Worker consumption path still need separate evidence. QQ
real-plugin acceptance remains bounded by the existing owned fixture and is not
real-account acceptance. Linux/macOS, remote CI, production build/browser,
real-data migration, deployment and MA acceptance remain unverified.

No push, merge, deployment, protected-port operation or real data/profile
operation occurred. `main` and `practical` remain protected and unchanged.
