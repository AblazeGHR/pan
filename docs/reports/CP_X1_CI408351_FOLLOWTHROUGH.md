# CI 408351 lifecycle and event teardown followthrough

Freeze `1665eedaae2dd97a76e41f54ce0ce6a2f9a9317b` produced green three-platform
fast, two Windows owned jobs, and Ubuntu browser. Windows full remained red.
The unchanged original artifacts are in the MA `remote-ci-40835126735` folder.
The direct XML comparison against 408221, including source hashes and new/removed
nodes, is `evidence/cp-x1-ci408351-comparison.json`. Same node and failure kind
do not establish the same cause.

The 3.12 wait-error runner fault previously fired immediately after all tree
files appeared; cleanup could win the race against OpenProcess. The owned
observer now retains and birth-verifies every original handle, confirms the
receipt is unsettled, then releases the runner's injected fault. The original
unknown, error, single outbox, tree-death, and desktop-death assertions remain.

The 3.14 plugin reader returned immediately after scheduling cleanup; waiting on
the reader and process did not guarantee the stderr task was joined. A terminal
reader that initiates cleanup now joins that cleanup, while `_close` excludes
that reader from its own join to avoid self-deadlock. The dedicated deterministic
test holds cleanup with an Event and proves the reader cannot complete early;
existing cancellation, revoke, process, and peer lifecycle tests also run.

The genuine-main harness now captures bounded startup log tail and reports
child PID/birth/return code, owned listener/port/config and source SHA if the
original 25-second readiness limit fails. Its isolated local run succeeded;
the two prior CI failures discarded child output, so the target-environment
startup cause remains unknown and needs a fresh target run with this diagnostic.
No restart success is inferred from those earlier failures.

At actual source `b54654c6ac9ae6d673813ddd6c0101d469a497e6`, focused isolated
Windows regression `tests/test_cpx1_genuine_restart.py`,
`tests/test_cpx1_canonical_tree_exit.py`, `tests/test_plugin_event_lifecycle.py`
passed 22, exit 0 (`evidence/cp-x1-ci408351-final.xml`). The broader prior
source `dfd8345fb67b11ea686b505550944a1bbd395b2a` ran the two tree cases,
plugin lifecycle and plugin event suites: 30 passed, exit 0
(`evidence/cp-x1-ci408351-focused.xml`). Earlier XML is not attributed to the
later diagnostic-only source change. Owned restart proof reports source, original
effect/attempt, verified new owner, readiness and released port.

Direct XML comparison yields 3.12: 234 same node/kind, one added and one
removed; 3.14: 234 same node/kind, one added and **zero removed**. The latter
differs from the brief's one-removed description: prior XML has 234 failures
plus errors, current XML has 235, and the new reader failure accounts for the
increase. Full red remains open. MA owns the next exact-ref remote verification.
