# B1 repair checkpoint — 2026-10-21

Stopped expansion immediately on the user's 95% quota notification. No new tests
were started after that notice. This is a repair candidate for MA acceptance;
the previous **do not integrate / not accepted** disposition remains in force.

The isolated tree merged authorized baseline `d5fd2cca4d0dba336ca47142530411d5b1b6c180`
as `cea01e15`, without conflicts. main/practical were not moved by this work.
They were subsequently observed at `978beef4680f33bed05c156a7592e6b075db3b46`
(MA's separate model-catalog work); that newer baseline has not been integrated or
validated here. No service operation, push, release, subagent or paid model was used.

## Implementation and order

- Removed the single global async gate. The event-loop-local Coordinator atomically
  records shared Group resources and exclusive Session resources, without any await
  in registration/removal. Conflicting operations wait on completion events outside
  that atomic section; unrelated resources continue. Nested same-task claims are
  reentrant. Cancellation while waiting acquires nothing; acquired tokens always
  release in `finally` and wake waiters.
- Normal startup, settings and final queue/native admission atomically reserve
  their Session plus current/requested Groups. After waiting, Session selection is
  re-read so a changed binding cannot escape an incomplete resource set.
- Unbuild reserves the Group first, then enumerates references and reserves their
  Sessions. Existing Group readers complete before this Group claim is admitted;
  no Session token is held while waiting for those readers. Existing per-Session
  settings/spawn/queue locks remain in their lifecycle paths. Release never acquires
  a spawn/queue/settings lock: no opposite-order wait cycle is introduced.
- With admission reserved, unbuild checks live running status, stages detach via
  `commit_adapter_config_async`, then publishes registry retirement. Only then does
  it request normal delayed restart and clean release files. Cleanup failure is
  reported after safe retirement, with restart already requested. It does not make
  detached old tools eligible again.
- The actual `_queue_handoff` (both stream and retained one-shot callers) owns
  admission through write/spawn and receipt. It checks detached loaded pins before
  touching CLI input. Native turn admission uses the same reservation rule. If
  unbuild wins, even a stream consumer that already prepared `running` cannot cross
  the actual write boundary. If handoff wins, unbuild waits for that admission to
  retire and then rejects a now-running Session.
- Startup capture and adapter spawn argument generation, Action registry/build/
  cleanup, plugin validation and runtime capability document reads use off-loop IO.
  The Action registry still has its own off-loop serialized atomic writer; that is
  not the former global lifecycle gate. Session writes keep their existing ordered
  persistence tickets. Unrelated Session saves do not wait for these Group claims.
- Release uses a shielded, owned transaction task. Request cancellation waits for
  that task's commit/rollback before propagating; it cannot detach bindings and
  release admission while an uncancelled writer is still publishing. IO helpers
  likewise await the writer's retirement before cancellation escapes.
- Registry failure restores all live configs first and attempts every durable
  restore. Repeated restore failures still report Session IDs and do not claim
  durable success. Existing storage errors/owner/generation checks are preserved.
- `/api/spawn` and legacy Worker settings now reuse Session settings admission.
  New Session creation reserves its selected Groups, recompiles after waiting and
  protects duplicate names. Existing reference-copy paths (branch, rewind child,
  import and the handoff handler) participate so they cannot publish copied enabled
  references during unbuild. No handoff operation or handoff test was invoked.
- Immutable release definitions and three-mode Worker pins remain intact; generic
  gateway plugins retain their dynamic directory policy. All six prior fixes remain.

## Evidence and boundaries

New deterministic barriers exercise slow launch, Action save/build/unbuild disk,
Session metadata disk, unrelated settings and scheduled heartbeat progress while
the slow operation is still held; both handoff/unbuild orderings; all three Group
modes with waiting startup/settings; real stream-consumer entry after the idle
check; startup error/cancellation; multi-file cancellation/rollback; new Session
creation and source-Session branching during unbuild. The test file has 16 new
cases compared with the preceding repair candidate. No skip/xfail was added.

The original timing observation probe now records unrelated settings proceeding
before launch release, and a heartbeat near 25ms during injected 200ms Action IO
(previous observation 222ms). These timings are observations, not performance
contracts; the deterministic barriers are the acceptance evidence.

Completed results and raw JUnit/runtime records are in `b1/` beside this report.
The final in-flight targeted backend run retired with **250 passed, 0 failed,
0 skipped**, exit 0, in 55.41 seconds. It includes the original ten-file selection,
all new Action admission cases and Task A's `test_history_read.py` coexistence tests.
Prior completed checks: lifecycle/queue/native/settings tests **215 passed,
3 skipped**; copy/import/rewind regressions **69 passed**; targeted Vitest
**47 passed**; targeted ESLint exit 0; typecheck/production build exit 0 with
**74 compressed assets**. The three skips are existing retired-one-shot cases
(`test_one_shot_spawn_success_dequeues_after_argv_handoff`,
`test_one_shot_spawn_failure_requeues_same_item`,
`test_retired_oneshot_adapters_consume_effective_prompt`).

The final owned HTTP/IPC/production-browser probe completed successfully before
shutdown: two actual stdio Action calls per mode, **6 total**, CRUD/binding/mode/
idle unbuild/delete, zero page errors, no mobile overflow. Its model Sessions had
no paid Worker; consumer/startup lifecycle evidence uses controlled Worker/process
fixtures, not a paid CLI restart. Existing warnings are retained in the test records.

Still pending: independent MA source/acceptance review and any integration against
the later `978beef4` baseline. No full suite, remote CI, live paid CLI restart,
POSIX process cleanup or human acceptance is claimed. Claims are process-local;
multi-process registry access is not newly supported. Repeated disk failure and
process crash across multiple files do not gain a durable recovery journal here.
