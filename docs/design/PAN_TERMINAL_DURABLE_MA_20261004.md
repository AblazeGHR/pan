# Durable detach: launcher Job independence and real reattachment

The service requests `DETACHED_PROCESS | CREATE_BREAKAWAY_FROM_JOB` for its
new, owned launcher. This changes launcher placement, not the PTY or sidecar
guards: both still forbid breakaway and retain kill-on-close tree ownership.
`DETACHED_PROCESS` alone does not establish independence from a host Job.

An access-denied CreateProcess attempt falls back to DETACHED_PROCESS, with a
static diagnostic. Other errors propagate. Success of either spawn attempt is
not durability proof: the runner checks its actual ambient-Job membership
before allowing detach. Restricted/unknown layouts continue to refuse detach.
No foreign Job handle or flags, service configuration, account, or broker is used.

When stderr logging is enabled, the parent closes its own file object after
spawn. With logging disabled, stderr is explicitly DEVNULL, rather than an
inherited host pipe whose lifetime would otherwise be extended by the launcher.

Cross-process reconcile rebuilds the attachment channel proxy after verified
IPC reattachment. It does not start another PTY, manufacture a process identity,
restore old control tokens, or change the persisted terminal's identity.

## Actual Windows proof and failures retained

`test_terminal_durable_ma.py` runs a separate real service host, starts the
production launcher/default shell, sets a shell variable, detaches, then exits
the host without shutdown. After host exit and an interval beyond lease grace,
a new service reconciles the original PID/raw FILETIME, obtains a new control
lease, and reads the old shell variable through a file written by that shell.
Explicit close subsequently removes the secret and the retained process handle
is signaled. No durability or identity probe is injected.

The first run failed waiting for inherited stderr EOF (`durable-direct.xml`).
The second failed because reconcile had not rebuilt the attachment channel
(`durable-fixed-direct.xml`). Both are preserved. The final six focused tests
pass in `durable-final-direct.xml`, including a real owned restrictive Job
whose launcher membership is checked by the kernel and whose detach is refused
without changing identity or deleting the secret. The earlier service run
(`breakaway-service-direct.xml`) is 86 passed / 1 failed: its unconditional
ambient-refusal premise became false after successful breakaway; that test now
explicitly creates its own restrictive host Job and keeps its refusal assertions.

This is acceptance of the measured production layout on this Windows build,
not a claim that every ancestor Job permits breakaway or that detached objects
survive explicit user termination, logout, machine restart, or sidecar restart.
On hosts that prohibit breakaway the positive test explicitly skips after
confirmed safe refusal, rather than reporting a false durable pass.

Reproduce with declared requirements and installed exact-pinned sidecar deps:
`python -m pytest tests/test_terminal_service.py tests/test_terminal_service_rework.py tests/test_terminal_breakaway_ma.py tests/test_terminal_durable_ma.py -o addopts= -q`.

The final combined uv service selection is **88 passed, zero skipped**, 104.85s
(`durable-service-final-uv.xml`). The additional focused uv rerun verifies the
final test cleanup path separately; historical failed XML files are retained.
