# Unified Job abstraction completion candidate

- Tree: `D:/project/pan-worktrees/event-trigger-core-20261022`
- Starting candidate: `8b48238aea497af85fd0d1de0a6b42d92d60eb99`
- Final source candidate: recorded by the commit containing this report.
- No OAuth, keyring, private exporter, official credential, real provider,
  account, user Worker or protected service was used.

## Implemented

The existing Action/Template/Job registry remains the single persistence path.
`packages/jobs/action_templates.py` now validates and persists the shared Job
configuration fields `trigger`, `recovery`, `overlap`, `notification`, and
`completion`, while accepting old `schedule`/legacy kind records with conservative
defaults. Event triggers call the existing bounded structured Session query
validator. Recovery defaults off; only monitoring completion permits recovery.
Parallel limits are 1..32 (default 3), monitoring+parallel is rejected, and
notifications are job-scoped terminal allowlists rather than Session message
targets.

`packages/jobs/action_runtime.py` persists Run/Attempt identity, count,
Action/reference/template audit, backoff, receipt and side-effect uncertainty in
existing Job records. Every attempt still uses the existing temporary Action
snapshot and terminal cleanup. Monitoring Jobs without their structured signal
enter `restarting`; declared backoff and max attempts are handled by the same
scheduler tick, with exhaustion becoming failed. Ordinary Actions complete from
normal returned results. `packages/jobs/action_api.py` exposes the shared schema
and a protected `/api/jobs/action-jobs/{id}/runs` audit view.

`packages/jobs/unified.py` provides a projection-only compatibility mapping for
background-process, session-message, session-broadcast, scheduled-task,
main-lifecycle, and unknown legacy kinds to conservative system Action views.
`packages/jobs/api.py` includes this `unified` projection without rewriting old
records or removing old APIs. `packages/actions/store.py` marks product-managed
system Actions and blocks user deletion. Frontend types expose unified config,
policy and Attempt fields; the existing Job store/API remains the source of truth.

## Verification

Python command:

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_job_unified_contract.py tests/test_job_action_templates.py tests/test_job_action_http.py tests/test_action_ids.py tests/test_action_groups.py tests/test_jobs_api.py -q --junitxml=artifacts/job-unified-final.xml
```

Result: **173 passed, 0 failed, 0 errors, 0 skipped; exit 0**. JUnit:
[job-unified-final.xml](D:/project/pan-worktrees/event-trigger-core-20261022/artifacts/job-unified-final.xml).
The earlier full HTTP action test was rerun after a transient timeout and passed
exit 0; it uses only its owned HTTP process and deterministic fake CLI.

Additional checks:

- `python -m compileall -q packages/actions/store.py packages/jobs/action_templates.py packages/jobs/action_runtime.py packages/jobs/action_api.py packages/jobs/api.py packages/jobs/unified.py tests/test_job_unified_contract.py` ！ exit 0.
- `pnpm --dir packages/web exec tsc -b` ！ exit 0.
- `pnpm --dir packages/web build` ！ exit 0; Vite built and verified 80 compressed assets.
- `git diff --check` ！ exit 0 before commit.

The new deterministic contract tests cover policy defaults/invalid combinations,
structured query, Run/Attempt audit, terminal snapshot cleanup, monitoring no-
signal restart, recovery attempt admission, legacy projection and unknown-kind
fail-closed mapping. Existing HTTP/template tests cover revision, permissions,
Action edits/deletion, idempotent instantiation, cleanup failure, and old Job
compatibility.

## Compatibility and explicit boundaries

Old Job APIs and stored `kind` values remain. The compatibility projection is
read-only; it does not auto-migrate old records or enable recovery. Legacy message
queue, Pause, Illegal, managed, owner/generation, Group release/pin and unknown
receipt gates remain in their existing executors. System Action deletion is
blocked for non-product writers. Existing schedule presets remain distinct from
complete Job Templates.

The backend and TypeScript contract are updated, but the React `NewJobForm` still
contains legacy kind-specific creation controls; a full visual unified Job/
Template editor and desktop 320/390px Chromium interaction pass remain separate
UI work. Full automatic migration of every legacy process/message/main-lifecycle
executor to actual system Action definitions is not claimed: the projection is
compatibility-only until those executors can share their existing security gates.

No real service, 8767/8768, real account/credential, user Worker, paid model,
main/practical/integration tree, push or release was used. Human/MA acceptance
remains pending.
