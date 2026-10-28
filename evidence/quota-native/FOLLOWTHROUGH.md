# Quota host binding diagnosis and canonical HTTP followthrough

Requested combination baseline: `ea0b71a888c9180401b2214ee517eca683ed98ac`.
Merged normally into the existing clean task branch, preserving both parents:
merge `9cc715137a1fab92a2e7cdac835db1f4b20ea55d`, prior task HEAD
`f33cddf93a48ad45b94f141025b0ea28dc7e30cb`. No reset/cherry-pick of quarantine.
All changes after that merge are confined to the owned quota fixture, dedicated
quota test and evidence. No product/shared host/API/executor/ledger change.

## Original host timeout: precise cause and bounded repair

The unchanged node
`tests/test_quota_host_http.py::test_owned_host_configuration_read_projection_and_restart_real_http`
failed at line 76 waiting for `hostObservation`: `followthrough-host-red.log`,
1 failed in 21.39 seconds. It had already authenticated, enabled the host and
admitted a canonical once Job. This is not a CLI-output mismatch or an observation
task exception. There is no automatic observation task in this binding.

Exact call chain on the combined baseline:

1. Owned `quota_host_http_server.py` replaced `quota_host.production` with an owned
   fakeCLI/read-cache factory before running the production launcher.
2. Production `AutomationService.__init__` constructs `quota_policy.Runtime`, whose
   default adapter is `Adapter`. Neither Service nor server calls
   `quota_host.production` on this baseline. Merely replacing that factory has no
   effect. Static search found its definition and no product caller.
3. `FinalService.bind` attaches the quota native provider to that Runtime. Its
   canonical once Attempt calls `Runtime.query` → default `Adapter.query` → explicit
   Unavailable → original unknown quota snapshot. HostAdapter.sample never runs,
   so `hostObservation` cannot appear and the original test times out.

The owned fixture now explicitly binds its existing `owned` factory after the
original Service initialization. It asserts the root is exactly the test's
temporary profile, binds `HostAdapter` with **zero mutation ports**, and retains
the default-off host/CAS gates. No dispatcher/watch/extra loop is started.
Instrumentation appends only fixed phase, PID, timestamp, type/status and port
count, delegating sample unchanged; no exception payload, secret or raw CLI output
is logged. Its before-binding record says Adapter, bound record HostAdapter,
single sample ends provider/native known, and second process binds disabled with
no sample. Raw bounded phases: `followthrough-host-binding.jsonl`.

The unchanged original node then passed, **1 passed in 12.03 seconds**, exit 0:
`followthrough-host.log`. Thus all original assertions remain exercised: exact
host authentication/type/CAS, provider command whitelist and both provider IDs,
native weekly 70, provider identity domain/current account, unknown native↔provider
binding, no business commands, host disable, independent service restart,
persisted login/logout rejection, unchanged CLI trace after disable/restart,
byte-identical original quota-cache file, no secret/private label in evidence.
Sanitized resulting policy/commands/cleanup: `followthrough-host-proof.json`.

**Product boundary remains explicit**: this repairs the stale owned fixture and
proves the existing read-only host code works when bound. It does not claim that
enabling host settings binds a deployed product adapter. The default product
adapter still intentionally stays unavailable. Restoring the retired automatic
policy dispatcher is prohibited and was not attempted.

Exact minimal shared-symbol proposal for MA (not implemented):
`packages/automation/api.py::install.service`, after obtaining the trusted
Automation Service, could invoke a one-time `quota_host.bind_readonly(service,
root)` host helper when explicitly enabled. That helper would construct existing
`quota_host.production(policy.store, service.worker, verified_profile_root)`, set
adapter.runtime and bind policy.adapter, keep ports empty and perform no I/O until
an admitted query. Root must be derived from the trusted service ledger/profile,
never request data. Alternatively MA can release the single construction seam in
Service.__init__. Disabling retains read-only host gates; no mode rewrite, grants,
poller, switch/restart/wake or scheduler reinstatement. This needs MA's shared
binding decision; automation/api.py, service.py and server.py are unchanged here.

## Exact old-node to canonical successor mapping

The protected original tests were rerun unchanged on the combined baseline:
`followthrough-original-red.log`, **3 failed in 10.42 seconds**, exit 1.

| Original node / assertion | Current red and strict successor |
| --- | --- |
| `test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown` | Old 409 writer-fence assertion sees canonical 200 admission. `test_unknown_production_process_restart_real_http` validates authenticated once admission, CAS, persistent disabled account, unavailable original execution and canonical reads. |
| `test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | Accepted fixture typo corrections in baseline now reach line 82: legacy `denied['ok']` is absent. The strict process successor asserts the canonical address does not accept legacy PATCH (405); `/api/legacy/jobs/{id}` PATCH/DELETE and run-now require auth (invalid Bearer 401, authenticated 410 with detail.code=retired). Original assertions are preserved, not reinterpreted as passing. |
| Same real-HTTP node's terminal/history/restart assertions | Strict process successor reads `/api/jobs/{id}/runs` and `/api/job-runs/{runId}`, supplements with read-only canonical SQLite Attempt/effect/Event facts, and compares original Run/Attempt/effect/event ID+digest after actual OS process replacement. There are no legacy jobs/quotaResult reads. |
| `test_job_retirement_quota_transport.py::test_quota_real_tcp_cas_reopen_and_legacy_writer_fence` | Bound monitor95 returns account-unknown 409 rather than fence. Strict process successor proves monitor binding query rejects unknown account without Job creation; `test_unbound_legacy_admission_still_fenced` separately proves retired unbound execution is fenced before query. |
| Original manual-confirmation/authorization intent | Current canonical recovery control is authenticated and CAS-bearing. Strict process successor rejects invalid Bearer 401 and unknown retry 409/reconciliation_required before and after OS restart. It switches policy to manual using exact CAS (invalid Bearer 401, stale CAS 409), rejects subsequent query admission, and preserves original Run/effect/Event and zero wake. It does not invent an obsolete user_confirmation_required code for canonical definitions. |
| Existing Agent grants | Dedicated `test_agent_authority_revocation_real_tcp` asserts denied admission with absent grants, actual Agent createdBy/authority, grant revision revocation stopping new Event publication, zero query/effect, and authenticated user cleanup. Existing test remains in the focused matrix. |

The strengthened strict process node passed **1 passed in 13.05 seconds**,
`followthrough-process.log`, exit 0. It observes the original two default query
invocations only (monitor binding + once effect), no inventory/perform, no new
query/wake/effect after duplicate reads/admission, process restart or rejected
recovery/manual controls. Original single immutable Event and factDigest survive.
`process-tcp.json` records actual listener PID/birth, all captured process identities,
both zero exits, port release and final fresh-bind assertion. No existing service
or protected port is operated. The shared production-process launcher remains
unchanged; it wraps default unavailable calls only to record bounded observations.

First followthrough process attempt incorrectly assumed the retired PATCH still
occupied the canonical Job address and expected 401; actual response was 405.
`followthrough-route-red.log` retains that strict failed assumption. The successor
now checks exact canonical and legacy namespaces separately, with exact statuses;
it neither mounts old routes nor changes an original assertion.

## Actual commands and validation scope

Cwd: `D:/project/pan-worktrees/quota-native-admission-20261028`.
Interpreter: requested `D:/project/Pan-main/.venv/Scripts/python.exe`.
Commands all use this interpreter for outer runner and child. Full commands and
source hashes are recorded in `followthrough-source.json`. Examples:

Final targeted regression log: **99 passed in 88.18 seconds**
(`followthrough-regression.log`). After the continuation, its original exec
session was no longer queryable, so its process exit code is recorded as unknown
rather than invented. The completed pytest terminal summary is preserved. The
original host green node and strict process green node each have directly
retrieved execution-tool exit code 0; the protected original-red batch has exit 1.

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_host_http.py::test_owned_host_configuration_read_projection_and_restart_real_http -q -o addopts= --tb=short -W ignore --basetemp=evidence/quota-native/host-owned-temp
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_policy_http.py tests/test_job_retirement_quota_transport.py -q -o addopts= --tb=short -W ignore
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_native_admission.py::test_unknown_production_process_restart_real_http -q -o addopts= --tb=short -W ignore
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_native_admission.py tests/test_quota_host.py tests/test_quota_provider_source.py -q -o addopts= --tb=short -W ignore
```

Output captured with `2>&1 | Tee-Object evidence/quota-native/<log>`; actual child
exit codes are reported separately by the execution tool. Tests' generated old
artifact paths were copied into bounded evidence, then restored to their exact
pre-test committed bytes; the original historical evidence is not overwritten.
The self-owned basetemp includes a local ignore file, is not staged, and contains
no user profile. No key/token/database/raw temporary profile is committed.

No fresh browser/build, full suite, Linux/macOS, remote CI, real accounts/native
CLI, actual wake/switch/restart, abrupt crash/power loss, deployed host enablement
or production acceptance is claimed. This line does not edit Session/card code.
No shared API/FinalService/executor/ledger/owner/server or original assertions
changed. No new effects ledger, provider protocol, retry/policy system, TA,
main/practical move, push or deployment.
