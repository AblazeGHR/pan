# CI 408221 lifecycle fast followthrough

Freeze `b5b3f81d57e56b735d65d18d4446ae29c4cb39c6` was merged normally into
the task branch. Original CI artifacts remain at the MA evidence root. The
exact per-node POSIX and Windows comparison, including hashes of both source
XMLs, is `evidence/cp-x1-ci408221-inventory.json`.

Both Ubuntu and macOS had the same 21 lifecycle failures. The actual route
returned a 501 unsupported capability response before any admission, gate or
task. The shared `prepared` fixture now injects only the service.lifecycle
decision when absent; all old assertions remain (AST assertion comparison in
`evidence/cp-x1-ci408221-assertions.json`). Real detached-supervisor tests
execute only when the actual host capability exists: two explicit skips on
unsupported hosts, since a parent monkeypatch cannot authorize a child process.
The existing real-host 501 tests remain in the fast gate. Two additional
forced-negative tests check 501 and no effect on Windows without changing host
identity.

Source `a1d854eb3d8cb84db819833701b8e19ada9b0ac3`: isolated Windows focused
group 51 passed, 2 real-unsupported-host skips, exit 0
(`evidence/cp-x1-ci408221-local.xml`). Source
`4192a2bdd6284acba811673460232a531b9718d6`: with a test-only plugin forcing
the capability decision false, the 19 shared preparation/strict cases passed,
exit 0 (`evidence/cp-x1-ci408221-forced-capability.xml`). Only this diagnostic
selection excludes the two actual detached supervisor nodes; no test was
removed from the gate. Neither test claims actual POSIX lifecycle support.

Compared by testcase and result kind against 40760691765: Python 3.12 has
233 unchanged red records, two added failures and one removed failure; Python
3.14 has 233 unchanged red records, one added and one removed. The genuine
restart fixture failed before original main readiness on both versions; its
old child stdout/stderr were discarded, so the startup cause is not established.
The additional 3.12 independent-service test timed out submitting after a
healthy response, with the process still alive and no durable execution seen;
the cause remains open. Removed terminal and Locker nodes are not relabeled as
full-suite acceptance. There is no product change in this batch.

MA should run the exact combined ref through all three platform-fast jobs,
including real POSIX 501 checks and the two explicit real-supervisor skips,
and retain import/full collection/fast artifacts. Windows owned/full and Ubuntu
browser remain independent gates. Remote validation of this candidate is pending.
