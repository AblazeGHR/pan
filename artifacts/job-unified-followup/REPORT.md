# Unified Job abstraction follow-up

- Tree: `D:/project/pan-worktrees/event-trigger-core-20261022`
- Baseline and source candidate: `b95bb2fbe5601b618c88bce695e7f1e5a86de44f`
- Worktree was clean before verification. This follow-up required no product
  source change: the first-phase unified Job/Action/Template implementation was
  already present in the candidate and was verified against its design contract.
- New commit contains only this report and JUnit evidence.

## Existing implementation verified

The existing `packages/jobs/action_templates.py`, `action_runtime.py`,
`action_api.py`, `packages/actions/store.py`, and generic background Job registry
provide the shared path. It uses Action references rather than copied Action
definitions in Job config, complete template revisions and durable idempotent
instantiation intents. Existing scheduler entries remain the trigger basis;
legacy background/session-message/process kinds remain untouched.

The verified contract includes strict unknown-field rejection, Action declared
parameter/default/type validation, empty-schema `{}` only, missing-reference
`incomplete` diagnostics with action ID and reference position, template revision
copying, and display-only instantiate overrides. Actual attempts claim the generic
Job, validate the current Action/permission/reference generation, create a
private executable snapshot, and clean it for completed/failed/cancelled/
uncertain outcomes. Terminal audit keeps digest/revision/context metadata but not
the executable definition. Cleanup failure remains visibly `cleaning` and is
recovered by the existing scheduler path.

System `job_template` Actions are product-controlled fixed capabilities with no
input properties; user/Agent Action editing/deletion and Group/provider legacy
entry points retain their existing gates. Monitoring completion uses checkpoint
signals; ordinary completion Actions use their returned result and do not fail
merely because no monitoring signal was emitted. Recovery and overlap constraints
are validated conservatively; missing/legacy strategy fields do not enable new
automatic behavior.

## Verification command and result

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_job_action_templates.py tests/test_job_action_http.py tests/test_action_ids.py tests/test_action_groups.py tests/test_jobs_api.py tests/test_background_jobs.py -q --junitxml=artifacts/job-unified-followup/job-action-templates.xml
```

Result: **208 passed, 0 failed, 0 errors, 0 skipped; exit 0**. JUnit:
[job-action-templates.xml](D:/project/pan-worktrees/event-trigger-core-20261022/artifacts/job-unified-followup/job-action-templates.xml).

The selected tests cover schema and API contracts, Agent/user permission gates,
empty Action schemas, missing references and restoration, template revision copy
and idempotency, snapshot stability under Action edits/deletion, all terminal
cleanup states, cleanup/persistence failure recovery, fixed template delegation,
legacy Group gate, overlap/capacity behavior, and existing background/message Job
compatibility. Tests use temporary Action/Job roots and deterministic local
functions; no real provider or model is invoked.

## Migration and boundaries

Migration is additive and conservative: old Job kinds and schedule preset
`/api/jobs/templates` remain; existing records are not rewritten on import, and
missing/unknown strategy fields do not enable recovery or broaden permissions.
No React controls or external plugin transport were added. Query semantics,
full production UI acceptance, real provider execution, and future migration of
all legacy kinds to system Actions remain outside this phase.

No private exporter/OAuth/keyring path participates in this Job runtime test.
No real service, 8767/8768, user Worker, account, credential, paid model,
main/practical/integration tree, push or release was used.
