# Quota production restart port probe portability

Base `87c444ce896e768a75bc74fde1af834952f4875c`, fast-forwarded normally from
the prior task candidate `1ae04d9476b76cedfe3b56da79270f3c4f58db92` on the same
worktree/branch. No reset or replacement TA. This candidate changes only the
owned quota test and evidence, with no product/platform/shared-support edits.

## Exact remote red → local repair mapping

Remote run **40661181678**, source **87c444ce**, has two recorded failures:
`platform-fast-ubuntu-latest-py312/fast.xml` and
`platform-fast-macos-latest-py312/fast.xml`, both at
`tests.test_quota_native_admission::test_unknown_production_process_restart_real_http`.
Linux reports errno 98, Darwin errno 48, Address already in use, at the old
`stopped()` line 530 `probe.bind(('127.0.0.1',port))`. Exact original records and
inventory SHA256 are preserved in `remote-failures.json`; their remote paths
refer to MA's downloaded artifact inventory. CI failure is not confused with
successful artifact/result collection.

The raw trace places the bind error **after** `rt.stop()`, zero process exit,
each captured PID/birth-retirement check and connection-refusal assertion. Thus
the red does not establish a surviving listener or another effect execution.
It establishes that the extra fresh non-reusing socket cannot bind at that
instant, despite the previous process being gone and no connection accepted.
No remote socket-state table was captured, so TIME_WAIT is an explanation
inferred from these facts and TCP semantics, not a fabricated observed state.

The probe had different options from the production listener: Uvicorn delegates
to `loop.create_server`; the inspected Python asyncio implementation defaults
`reuse_address` to POSIX/non-Cygwin and sets SO_REUSEADDR before bind. Its actual
local implementation and Uvicorn call site are retained in
`local-listener-source.txt`. TCP accepted connections may outlive the listening
socket in TIME_WAIT after active server-side close. A bare bind can reject this
address while a normal production restart using address reuse can succeed.

`probe_owned_rebind` now applies that same address-reuse policy: SO_REUSEADDR
only on POSIX/non-Cygwin, original default options on Windows, **never
SO_REUSEPORT**. It performs one bind, no catch/retry/sleep. Bind errors remain
test failures. There is no attempt to occupy another service's listening port.
The preceding PID/birth, process exit and connection refusal assertions remain
unchanged; the actual second production OS process still must start on this same
port and complete all original unknown/reopen assertions. Each generation's
evidence now records the exact rebind policy used.

New `test_owned_rebind_probe_rejects_listener_and_accepts_closed_connection`
starts a real owned TCP listener with the same policy. A simultaneous probe must
fail with address-in-use/access-denied, proving reuse cannot substitute for
listener retirement. It then accepts a real connection, actively closes from
the server, observes both FIN/EOFs, retires the listener, asserts connection
refusal and requires a successful production-policy rebind, without sleeps or
blind retries. Protected ports are explicitly rejected. There is no skip/xfail
or suppressed bind error in the cleanup helper.

The existing process successor retains authenticated canonical once admission,
unknown-account monitor rejection, CAS/disabled-account persistence, retired-route
and unknown-recovery/manual gates, two actual OS generations, original immutable
Event/Run/Attempt/effect/effectKey, exactly two default query invocations (one
monitor binding + one once effect), zero wake and no extra query/effect after
duplicate admission/read/restart. Source, metadata and original assertions are
not weakened to accommodate a socket error.

## Local validation and limits

Windows, requested interpreter `D:/project/Pan-main/.venv/Scripts/python.exe`.
Working directory `D:/project/pan-worktrees/quota-native-admission-20261028`.

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_native_admission.py -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/ci40661181678-native.log
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_host_http.py tests/test_quota_host.py tests/test_quota_provider_source.py -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/ci40661181678-host.log
git diff --check
```

Host regression: **84 passed in 27.33 seconds, exit 0**, including the unchanged
original host real-HTTP test, readonly fakeCLI whitelist, cache-byte invariance,
auth/CAS/disable/logout and owned restart. `host-proof.json` preserves bounded
result/cleanup data; its old generated artifact was restored to committed bytes.
Final native matrix outcome and source/command hashes are recorded in
`source.json`; `../process-tcp.json` contains the latest owned process/port proof.

Final dedicated matrix: **21 passed in 95.86 seconds**, including all prior 20
native/seam cases and the new real-socket probe. Its completed pytest summary is
in `../ci40661181678-native.log`; after user continuation the exec session could
not be queried, so the overall process exit code is explicitly recorded as
unavailable. The probe node was independently rechecked: **1 passed in 1.09s,
exit 0**, `probe-final.log`. Host batch exit 0 was retrieved before continuation.
No completed-log outcome is silently promoted into a directly observed exitcode.

The final Windows process run used port **63922**, generations **37348** and
**3760**, both exit 0. All captured PID/birth identities were rechecked retired,
connection refusal/fresh bind passed, with reuseAddress=false and reusePort=false
as appropriate on Windows. Read-only original unknown identities and Event digest
remain unchanged with no repeated query/wake/effect. The process proof's inherited
sourceCandidate field names the initial quota feature anchor; the actual current
source snapshot is the base/hash manifest in this directory's `source.json`.

Probe-only recheck command:

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_native_admission.py::test_owned_rebind_probe_rejects_listener_and_accepts_closed_connection -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/ci40661181678/probe-final.log
```

The first local matrix had 20 passes and a new-test NameError because an adjacent
cold-reopen block was briefly placed under the new function during editing. It
is restored unchanged to its original test. `edit-placement-red.log` preserves
that local error; it is unrelated to the original remote bind failure. The full
matrix was rerun after correcting source placement, rather than dropping the
reopen assertions or hiding the failed experiment.

Windows results do not prove repaired Linux/macOS behavior. The POSIX option
branch and remote TIME_WAIT explanation need actual target CI rerun by MA;
neither WSL/simulation nor local Windows execution is labelled target acceptance.
No new CI/push was triggered here. All original three protocol-red assertions
and historical raw red evidence remain unchanged.

No product, shared platform, CP-X1/Card/support, API/server/worker/owner/ledger,
dispatcher or account-policy change. No real data/profile/account/provider,
protected port, existing service, main/practical movement, push or deployment.
