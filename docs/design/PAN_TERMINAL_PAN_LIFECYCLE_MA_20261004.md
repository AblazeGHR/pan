# Real Pan lifecycle acceptance

The fixture starts `packages.web.server.app` and its real lifespan in a separate
owned process, an empty temporary data root, and a reserved loopback ephemeral
port. It does not replace the Terminal service/runtime/backend/emulator or their
identity/durability probes. Configuration disables optional plugin manifests and
contains no Sessions, model work, credentials, QQ account, or scheduled Jobs.
Session/attachment/Job/scheduler/terminal roots are isolated. Port 8768 and the
running user's Pan are untouched.

## Measured scenarios

Two real terminals are created through production REST. The second receives a
shell variable through the real WS claim/input protocol and is detached. The
owning Pan process then either:

* takes its actual registered Uvicorn graceful shutdown path; or
* is terminated through its original owned Popen process handle (crash case).

The managed terminal's retained handle becomes signaled; the detached terminal
remains alive. A new actual Pan process starts on the same temporary persisted
root. Reconcile reports the managed record exited and deletes its secret, and
reattaches the detached original PID/raw FILETIME. The old shell variable is
read through a file produced by a new WS input command. Explicit REST close then
converges and the detached process becomes signaled. The second Pan exits cleanly.

Verifier-retained handles allow kernel signaled/identity proof throughout this
test. This does not claim that an arbitrarily late restart can prove death after
every process-object reference has disappeared: UNKNOWN still retains the record
and credential, never becoming a fabricated DEAD result.

## Fix and evidence

Graceful service shutdown previously tried each managed terminal only once.
A normal stop reply could precede the launcher's engine cleanup/status/exit
evidence. It returned unconfirmed and allowed Pan to exit while keeping the
secret, even with time left in its cleanup budget. Shutdown now re-evaluates the
same owned state within its original total deadline. `_close_state` still owns
single-flight execution and bounded stop resends. Persistent failure keeps the
owner/secret and reports budget exhaustion; no missing-owner state is fabricated.

`pan-lifecycle-direct.xml`: two fixture errors (incorrect WS event name; cleanup
then encountered an unattached state). `pan-lifecycle-fixed-direct.xml`: one
passed crash/restart chain, one genuine graceful-cleanup failure above.
`pan-lifecycle-shutdown-fixed-direct.xml`: **4 passed**, including both real Pan
chains and two deterministic late/persistent shutdown gates.
`pan-lifecycle-shutdown-final-uv.xml`: **14 passed / 73 deselected**, including
both real Pan cases and related original shutdown tests, 101.85s.
All historical failures remain; no failed history was rewritten.

This is actual Pan application/lifespan plus terminal acceptance with optional
integrations disabled, not provider/QQ/remote-authentication acceptance, deployment,
or restart of the practical service. Cross-user/host and long-running stress remain
unverified. The bilingual user manual describes the real feature and these limits.
