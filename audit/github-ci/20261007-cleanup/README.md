# Cross-host test cleanup repair

Parent SHA: `b83cb5f4b3c328206dbb58a42cfaccbc2d73ed5d`.
Failing exact-parent run: https://github.com/AblazeGHR/pan/actions/runs/37503444960.
Both legs: 2900 passed, 17 skipped, one failed. The archive/kill failure is repaired. The remaining full trace explicitly shows the original skip at host returncode 23, followed by finally's TerminalNotAttached, which masks the skip.

Only `tests/test_terminal_durable_ma.py` changes; production code, manifests, dependencies, workflow, thresholds and existing skip conditions are unchanged.

## Contract and cleanup

The child publishes its runner identity while alive and waits for the parent's acknowledgement before detach or refusal cleanup. The parent opens and validates the exact runner handle (PID + raw creation time) before acknowledging, retaining that handle through child host exit. Atomic replacement protects report publication from partial reads. The original 35-second host deadline now covers both identity publication and communicate; the handshake does not enlarge it. The child returns 23 only after service.close succeeds and publishes its closed view.

For a refused detach, the test requires detached=False, child close status exited with no cleanup pending, retained runner DEAD and secret absent before considering skip. Final cleanup additionally requires identity-bound runner cleanup and engine convergence records, registry EXITED/no cleanup_pending, and secret absence. Only after all checks and resource releases succeed does the existing ancestor-Job skip execute. Parent no longer blindly calls close on an unattached already-cleaned record.

For successful detach, every original same-PID, same-identity, lease grace, reconciliation, reconnect and shell-value assertion remains in `_verify_detached_reconnect`. Cleanup reconciles the actual owner, then closes it. If the host fails while a managed runner is still alive and unattached, the test uses the existing identity-verified bounded persisted-stop recovery path; no new runner is spawned, no fresh bare PID is killed and no death is inferred from PID absence. UNKNOWN identity, mismatched identity, missing engine/runner proof, remaining secret or cleanup failure still fails. The existing 25-second cleanup deadline and close retry behavior remain.

Host termination uses Popen's original handle. Host, retained runner handle and both stdout/stderr pipes are released even when terminal cleanup fails. If an original assertion/timeout and cleanup failures coexist, a BaseExceptionGroup preserves the original exception and every release error. Startup failures without a published view inspect the exclusively owned registry rather than silently ignoring a terminal. No blanket TerminalNotAttached catch/pass exists.

## Regression and evidence

Five new deterministic cases: the exact remote refused-host sequence retains its supported skip only after verified cleanup and releases; live managed owner is recovered/stopped; UNKNOWN death fails; missing engine proof fails; timeout plus cleanup failure preserves both errors while releasing host/handle/pipes. The refusal regression fails against the exact parent's original test function with TerminalNotAttached; fixed test passes. Baseline method injection is confined to an isolated test process, not source changes.

Pan matrix Job `job_6963835dd1318c8e067ca138` completed exitCode 0 and delivered notice. Each Python 3.12/3.14 full CI: 2910 passed, 13 existing skips (2923 identities), zero failures/errors. Each affected service/rework/archive/durable suite: 102 passed, 1 existing ancestor-Job skip. Full durations: 1072.78s / 1083.00s. All 2914 original identities remain plus nine total regressions (five from this follow-up); original 40 failure outcomes pass per leg. Current full skips: seven Claude probes, three Kimi probes, one CodeBuddy checkout sync copy, one absent timezone database, one same-service ancestor-Job restriction. Cross-host success branch executes locally; GitHub's restricted host is expected to exercise its existing refusal skip after proofs, so local green is not remote acceptance.

Final repeat Job `job_51c442a2036755c5b2b866ca` completed exitCode 0, five fresh rounds per Python. Every round's original JUnit has seven passed, one existing same-service ancestor-Job skip, zero failures/errors (includes real external kill, cross-host success and the five regressions). Evidence archive retains full remote logs/JUnit/CRC archive, scripts, latest repeat logs/XML, baseline failure probe, matrix logs/XML, test identity mapping, candidate hashes and Registry/runner logs. Scratch per-round filenames are reused by the repeat script and represent the latest run; earlier Job runner logs retain their baseline stage facts.

All long tests run as Pan durable Jobs with isolated child profile/AppData/tmp and dynamic non-8767/8768 HTTP ports. No main/practical advancement, stash/reset, remote push, tag/release, deployed-service or user-profile access. Latest remote main remains exact parent; shutdown is explicitly deferred by MA.
