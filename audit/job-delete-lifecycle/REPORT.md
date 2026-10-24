# Background Job delete lifecycle audit

## Scope

This candidate is based on `886842338c623b73bac8f16a12f4675879664674` in
`D:/project/pan-worktrees/event-trigger-core-20261022`. It changes only the
background Job deletion path and its focused regression tests. No main,
practical checkout, protected service, provider, account, OAuth, or port
8767/8768 was used.

## Behavior fixed

`packages.core.background_jobs.delete_job` now treats `starting` and `running`
records as process-owning records. It persists `cleanup_pending` before any OS
operation, validates every recorded PID against its persisted process creation
time, terminates the runner and recursively captured descendants, and waits up
to two seconds for those identities to disappear. The JSON record is unlinked
only after a confirmed receipt. Unknown/reused identities, a live process after
the bound, termination errors, and child-record cleanup errors retain the Job
with a bounded `cleanupError`; a repeated delete fails closed while cleanup is
pending. Scheduled shell children are included in the same termination gate.

Terminal `completed`, `failed`, and `cancelled` deletion remains the existing
single-record unlink behavior.

## Evidence

Focused command (isolated environment):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_background_jobs.py tests/test_jobs_api.py -o addopts= -q --junitxml=artifacts/job-delete-junit.xml
```

Exit `0`; `105 passed`, `2 warnings`, `0 skipped`. The JUnit result is
`artifacts/job-delete-junit.xml`. The added tests cover confirmed active
deletion, unknown receipt retention and repeat-delete behavior, and terminal
compatibility. The API route also returns `job_busy` and retains the
`cleanup_pending` record. Existing cancellation, orphan recovery, runner
identity, and scheduled Job tests ran in the same set.

The extra Action HTTP file was intentionally not run: its own first assertion
requires a test port different from 8767/8768 but resolves to 8767 in this
checkout (exit `1` before exercising product code). No protected port was
opened or contacted.

Windows process-tree smoke evidence used only a self-owned temporary Python
parent and child process:

```text
{'confirmed': True, 'killed': 2}
{'parent_alive': False}
```

The parent and child were both gone before the command exited. No real Pan
worker or service was involved.

`python -c "import ast; ..."` syntax check exited `0`; `git diff --check`
exited `0`.

## Limits

The two-second bound is deliberately fail-closed; a process that ignores
termination remains `cleanup_pending` for later operator reconciliation. This
patch does not introduce a new recovery worker, does not silently retry a
failed deletion, and does not change the existing `cancel()` API semantics.
The protected Action Job delete endpoint and independent Job service remain
behind their existing gates and were not broadened here.
