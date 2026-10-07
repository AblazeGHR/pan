# Delayed restart: MA review 1 incremental rework

Task scope is only delayed restart. The visible current task context contains
no phase2_workspace_diff/MEMORY/NCM/LCA execution or report; its reported
misrouting cannot be explained from this context. No memory materials were
read, organized or modified during this rework.

Initial HEAD: `4e757eadaa2c803d8775c19aded192737b944674` on
`feature/worker-delayed-restart-20261007`, original isolated worktree.
At the latest clarification checkpoint the only modification was this TA's
in-progress `packages/core/worker.py` exception repair. No stash/reset or
other worktree edits. Integration target explicitly updated from d6ce74f to
`92770472624077264c7210e49ca6ba8f74888f7a`; both main/practical refs were
observed at that SHA before integration. Only feature is to be advanced.

## Actual defect and minimal repair

The former terminal wrapper set `_terminal_finishing=True` before lock/save
and cleared it only through successful `_signal_task_done`. The former queue
finally cleared `_queue_unit_active` after awaited requeue/legal-state/broadcast
operations. Exceptions/cancellation could skip those clear operations.

The terminal wrapper now catches an escaping BaseException, clears its marker,
marks this exact generation's active delayed intent failed/cancelled, and
bare-raises the original exception. The existing persistence ticket's
rollback versus committed outcome, terminal key, cursor and dedup remain owned
by the existing implementation. Success still holds the marker through result
and report completion. This does not manufacture an idle boundary on failure.

Queue finally now clears its marker in an unconditional inner finally. An
exception in cleanup marks the exact intent failed/cancelled and propagates;
restart/wake is executed only after successful cleanup. Existing requeue save
errors were logged and swallowed: when delayed restart is pending they now
also fail the intent and propagate, preventing retirement on an uncommitted
requeue. The legacy behavior without a pending intent is preserved.

The synchronous failure decision sets pending false and runtime error, retains
the observable intent error with `use Restart to recover`, and does not spawn.
It matches Worker ID and generation; superseded/finished intents are unchanged.
An immediate Restart remains available through the existing Session spawn lock.
No timer, polling, new lock, global lock, process wait or network await is added.
The successful-path added cost is try/finally bookkeeping; failure uses constant
identity checks and the existing status-event mechanism. No new latency benchmark.

## Direct exception matrix (actual incremental experiment)

`python audit/worker-delayed-restart-20261007/exception_experiment.py`:
11 new scenarios pass. Original fixtures are loaded without running their main.
The terminal and receipt cases use the production save ticket/executor and
actual Task.cancel; only the final filesystem writer is an in-memory operation.

| Boundary | Interleaving | Observable result | Recovery proved |
| --- | --- | --- | --- |
| Terminal save | writer raises OSError | failed; same exception object propagates; marker cleared; rollback | actual terminal retry commits once |
| Terminal save | real caller cancellation while writer later fails | cancelled; CancelledError propagates after writer retires; rollback | actual terminal retry commits once |
| Terminal save | real caller cancellation while writer later commits | cancelled; CancelledError propagates; committed terminal retained | actual duplicate terminal retry does not add cursor/result |
| Queue finally requeue | operation throws | failed; same OSError propagates; active marker cleared | row retained, actual requeue succeeds |
| Queue finally requeue | actual Task.cancel at awaited operation | cancelled; cancellation propagates; marker cleared | row retained, actual requeue succeeds |
| Queue finally receipt save | actual requeue/ticket writer throws | failed; same OSError propagates (previously swallowed) | row retained queued, later actual requeue save succeeds |
| Queue finally receipt save | actual Task.cancel while shielded writer commits | cancelled; cancellation waits for writer, propagates | row retained queued, subsequent requeue succeeds |
| Queue finally legal state | operation throws | failed; same OSError propagates; active marker cleared | row retained/requeue succeeds |
| Queue finally legal state | actual Task.cancel at await | cancelled; cancellation propagates; marker cleared | row retained/requeue succeeds |
| Queue finally worker.status broadcast | operation throws | failed; same OSError propagates; active marker cleared | row retained/requeue succeeds |
| Queue finally worker.status broadcast | actual Task.cancel at await | cancelled; cancellation propagates; marker cleared | row retained/requeue succeeds |

All eleven also call the production public immediate `restart_or_start_worker`
wrapper and real Session spawn lock with an injected provider restart body:
the runtime becomes idle and is recoverable without a stranded guard. This
proves control-path admission, not native process restart or disk durability.
No result broadcast or automatic replacement is generated at these failure
boundaries. Failure state remains synchronously queryable even if WS is broken.

Three applicable original scenarios are rechecked using their original bodies:
running + 1000 duplicate requests; temporary idle during terminal/accepted-unit
cleanup; actual consumer/FIFO handoff with gated result/report publication.
Those pass. Old Worker writes task 1 only, new Worker writes task 2 once, locked
task 3 stays queued, and report completion precedes restart. The other fourteen
are not rerun; prior evidence remains in REPORT.md, with its original scope.

One initial incremental harness run failed because slicing at its third fixture
cleanup included four old scenarios instead of the intended three. Corrected
to the second cleanup, then rerun passed; this was a harness selection error.
The injected terminal-write failure during cancellation produces the existing
shielded-future diagnostic traceback in exception.log; it is deliberate failure
evidence, not a real filesystem fault or an unhandled production test failure.

## Boundaries not verified

No added/executed pytest/Vitest/E2E suite; no real service HTTP/WS, browser,
8767/8768 operations, provider CLI, native user data or filesystem fault.
Cancellation before lock acquisition and repeated cancellation are inferred
from the same wrapper catch, not separately measured. Completion/report errors
outside the requested persistence and queue-finally boundaries, daemon shutdown,
real watchdog recovery and process-exit duration remain unverified. Automatic
retry of a failed delayed intent is intentionally not introduced: the error is
explicit, terminal persistence can be retried by its caller, and immediate
Restart is the recovery control. No claim of OS/native recovery acceptance.

## Combination and commits

- Exception repair: `1254b131b2a64ebca0b8df45196f50dea2ef552e`, parent original
  `4e757eadaa2c803d8775c19aded192737b944674`.
- History-preserving merge: `21d1cced5985345e3beb2f91964e0f91c3563f7c`, parents
  repair commit and exact main target `92770472624077264c7210e49ca6ba8f74888f7a`.
  Docs auto-merged; both delayed restart API guidance and the new OS-background
  Job no-polling rules are present. No source conflict or dependency change.
- Final evidence-only commit follows the merge; its complete HEAD is given in
  the delivery response. It does not change production source.

Short validation ran in foreground, not a notified background Job (expected
well below the new three-minute general threshold). Actual combined checks:

| Command/check | Actual outcome |
| --- | --- |
| Python py_compile worker.py / web/server.py / mcp/server.py | all pass; bytecode only in ignored isolated scratch |
| node node_modules/typescript/bin/tsc -b | exit 0, 9.298 seconds |
| node node_modules/vite/bin/vite.js build | exit 0, 6.565 seconds |
| node e2e/verify-precompressed-assets.mjs | exit 0, 56 compressed artifacts verified, 0.130 seconds |
| git diff --check | pass |

Commands/results are in validate_combination.py, combination-results.json and
combination.log. Existing worktree-local dependencies from the original
delivery were reused; no installation or shared junction was introduced.
Vite reports its existing large-chunk advisory; the build succeeds. No suite
was run; inherited main test-file changes are simply part of the merge history.

Both protected branch refs remained `92770472624077264c7210e49ca6ba8f74888f7a`.
Existing main/practical untracked Scepter documents and node_modules backups
remain untouched. No main/practical build or service operation, push/tag/release,
memory material access, native provider/user-data write, or stash/reset.
