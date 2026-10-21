# TA lifecycle isolation rework

Worktree: `D:/project/pan-worktrees/event-plugin-adapter-20261022`  
Branch: `feature/event-plugin-adapter-20261022`  
Input candidate: `ac95f3818011f84edceb01658a41c189c680af85`

## Reproduction and repair

MA identified that EventRuntime held its single lock while awaiting peer startup
and close. The server's Session mutation middleware consequently inherited peer
handshake latency. The old reader's finally block revoked ownership but did not
retire its process. This is a correctness gap in the input candidate despite its
earlier happy-path/transport regressions passing.

The corrected six-test reproduction on the exact input product source fails
**6/6, exit 1**: [before-corrected.log](before-corrected.log),
[JUnit](before-corrected.xml). Five tests use explicit entered/release barriers;
they hold A indefinitely while observing B registration, B revocation, unrelated
Session mutation response, B close, and a reconcile snapshot intersecting close.
The sixth runs a real Node peer with malformed oversized stdout and proves its
process survives reader failure without a watcher. All test-owned barriers are
released and owned peers cleaned in finally blocks.

The first red run's Session request fixture lacked `headers`; this was fixed,
then the original product file was restored temporarily from Git and the six
corrected tests rerun. **before-corrected** is the authoritative red evidence;
the first run and its fixture error remain visible in before.log/XML. Product
source was restored to the in-progress repair immediately after that experiment.

The repair removes the global lifecycle lock. Reconcile loads configuration off
loop, uses a monotonic ticket to reject stale snapshots, records current intent
without external awaits, and fences invalid connections immediately. It waits
only for trusted local Service/Bus revocation before returning binding changes.
The existing publish/deliver identity, membership/revision and generation checks
remain, including a second current/stopping check after the off-loop read.

Each plugin has its own lifecycle task, which coalesces updated intent and awaits
only that plugin's start/close. Same-plugin replacement waits for its old owner;
other plugins progress independently. Server middleware's awaited reconcile can
no longer await an external peer handshake or process close. Startup readiness
is now observable asynchronously; Session selection success is not peer ready.

Close sets a terminal latch and fences every current connection before its first
await. In-flight snapshots cannot install new tasks; startup checks after awaits
cannot republish readiness. Startup cancellation retains the handle returned by
any in-flight OS spawn before cleanup. Terminal reader errors/cancellation and
slow delivery create a connection-owned cleanup task immediately. It kills the
tree, revokes, settles startup, handles a late spawn/activation, cancels and joins
pipe tasks with a bounded deadline. No 2-second watcher is needed for cleanup.
A failed startup EOF waits for a later reconciliation rather than hot-looping
process restarts.

True parallel startup exposed the pre-existing Windows path canonicalization
race while `plugins/` is created by another thread. The common data helper now
stabilizes that parent before resolving per-plugin targets; all original owner,
symlink/path and data-version checks remain. The diagnostic failed transport run
is retained in transport-first.log/XML and startup-diagnosis.log. No path check
or assertion was weakened.

## Passing checks before frozen-SHA run

`python scripts/run_tests_isolated.py -- python -m pytest
tests/test_plugin_event_lifecycle.py -o addopts= -q
--junitxml=artifacts/event-plugin-rework/barriers-final.xml`

**17 passed, 0 failed/skipped, exit 0**, 4.04 seconds:
[log](barriers-final.log), [JUnit](barriers-final.xml).
These include the original six, real Node A withholding its initialize response
while B registers/revokes, cancellation at spawn and each handshake/readiness
stage, newer-vs-older snapshot ordering, concurrent data directory resolution,
real reader cancellation/slow consumer process and stderr retirement without a
watcher, and real startup EOF without hot respawn.

`python scripts/run_tests_isolated.py -- python -m pytest
tests/test_plugin_event_lifecycle.py tests/test_plugin_events.py
tests/test_plugin_events_http.py tests/test_plugin_panels.py
-o addopts= -q --junitxml=artifacts/event-plugin-rework/transport-second.xml`

**43 passed, 0 failed/skipped, exit 0**, 14.69 seconds:
[log](transport-second.log), [JUnit](transport-second.xml). This earlier batch
predates the four final added cases; do not add overlapping counts together.
It covers true Node publication/fanout/Action, three generated MCP projections,
multi-Session sharing, HTTP lifecycle/restart and Panel/data regression.
Tests now explicitly observe async peer readiness, without weakening immediate
Bus revocation checks or the requirement that requests progress while A is held.
The re-enable test additionally waits for both durable Runs to succeed.

compileall and diff-check pass; no automation engine/store/events or React change.
Full final targeted union (previous 168 + 17 lifecycle cases) runs **after commit**
at the exact candidate SHA. Actual command, exit, XML, log, SHA and refreshed
process evidence go to `.validation/event-plugin-rework-final/`; the final TA
message reports that outcome. Test-rewritten tracked artifacts are copied there,
then restored to the committed bytes, preserving a clean candidate.

## Evidence preservation and boundaries

At receipt, the three existing mode transport JSONs were already dirty. Their
exact incoming bytes are preserved as `ma-input-*-transport.json` in this folder.
The follow-up transport/HTTP outputs are saved with `transport-*` names. Existing
input-candidate artifacts retain their committed versions.

All process tests use owned temporary data and Node/fakeCLI, and no paid provider.
The final HTTP evidence records the allocated non-8767/8768 port, server PID and
creation times, owned descendants, and asserts no remaining owned processes and
port release. The adjacent simulated-practical sentinel is unchanged. No real
data, existing service, shared dependency, main/practical, push/release, or child
task operation occurs. No all-tests run or frontend edit is part of this work.

Remaining untested boundaries: real user browsers, paid providers, production
services/data, POSIX process-group cleanup, reliable delivery/replay and large
ledger operation. Existing pytest-timeout/dependency warnings remain visible.
Candidate completion awaits MA acceptance and integration.
