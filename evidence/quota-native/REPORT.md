# Quota thin native admission/query candidate

Delivery branch: `r6/quota-minimal-closure-20261028-a`.
Base: `00593314f3ac093eab43544ce5f4964258486bcd`.
Superseded implementation is preserved separately at
`quarantine/quota-superseded-20261028-a`,
`0ec9f09273a65bf1712e8039c595fa3a6f5e148f`; it was not cherry-picked.
Resolve the candidate commit with `git log -1` on the delivery branch.

## Implemented boundary

| Original call | Delivered mapping | Preserved business behavior |
| --- | --- | --- |
| Authenticated `quota_query_job` / `Runtime.create_monitor` | Host-derived principal → canonical create operation → existing composition coordinator → immutable quota Action + time Creator + Trigger + Job | Exact policy CAS for new admissions; an accepted request's original CAS can retrieve its original receipt after lost response. Unknown account still cannot bind monitor95. |
| `ensure_job` / `_ensure_job` for monitor95, once, reset | Canonical operation key scoped by profile, principal, policy epoch and purpose; accepted policy revision and business identity frozen in canonical graph | Monitor interval 60 seconds; reset/once one admission; expected account and original wake target retained. Duplicate requests reuse the operation rather than reserving a second schedule. |
| `tick_jobs` in a canonical-bound Runtime | Owned future-source cleanup through canonical lifecycle | Manual/epoch/disabled-account changes disable owned Creator/Trigger/Job definitions, without cancelling a frozen Attempt. Original pre-cutover path remains behind its existing owner fence. No canonical call enters it. |
| Existing query execution | Default `TypedExecutor` exact `quota-query.v1` branch → bound `NativeQuota.query` → original `Runtime.query` → existing adapter.query/normalization/policy snapshot | Default adapter explicitly unavailable; known/stale/unknown behavior remains normalization-owned. No account implementation or provider protocol was added. |
| Original threshold/account/wake block in `tick_jobs` | Extracted `Runtime.native_result` called once by original canonical execution | 95% threshold, account mismatch, policy epoch gates, original Worker/task/restart-proof eligibility, deterministic business `wake_ma` command key. No receipt-replay business callback. |
| Unknown outcome and cold re-open | Original canonical Run/Attempt/effect queried from canonical stores | Unknown blocks execution; new source Events do not cause another query. Cold host reconstruction retains original Attempt/effect and effectKey. |

`nativeJobs` is only Job ID → canonical composition key, with the existing
`jobs[purpose]` reference. Execution resolves the frozen composition key directly,
so a lost policy-index write is not a missing execution receipt. An accepted retry
repairs the index from the canonical operation. Cleanup can discover this adapter's
canonical Job graphs even when that index was lost. No nativeEffects,
nativeAdmissions, reservation ledger, QuotaExecutor, recovery protocol or provider
query/reconcile method was added.

Canonical composition principal metadata contains only type/id. The actual
host-derived authority, including Agent permission revision, is retained in
canonical authorization evidence/authorities and checked by existing gates.
No new grants or USER execution shortcut were introduced. Product Action
publication uses the existing trusted immutable publication hook.

An overdue reset is mapped to the next host tick (at least host-now + 1 second),
preserving fire_now intent rather than creating an expired canonical once rule.
If storage delay expires that point before Creator initialization, the entire
canonical admission transaction rejects and rolls back; it cannot return an
accepted Job with no future occurrence. Scheduler calculations were not changed.

## Actual validation

Working directory for every command below:
`D:/project/pan-worktrees/quota-native-admission-20261028`.
Interpreter: `C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`,
Python 3.14.5, Windows AMD64. `source.json` records file hashes and interpreter.
The isolated runner scrubs inherited PAN_* and allocates temporary data roots.

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_quota_native_admission.py -q -o addopts= --tb=short -W ignore
python scripts/run_tests_isolated.py -- python -m pytest tests/test_quota_policy.py tests/test_quota_policy_safety.py tests/test_quota_policy_idle.py tests/test_quota_host.py tests/test_quota_provider_source.py -q -o addopts= --tb=short -W ignore
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_final_executors.py tests/test_job_final_runtime.py tests/test_job_final_creators.py tests/test_job_final_http_service.py tests/test_session_product_closure.py -q -o addopts= --tb=short -W ignore
python scripts/run_tests_isolated.py -- python -m pytest tests/test_quota_policy_http.py tests/test_job_retirement_quota_transport.py -q -o addopts= --tb=short -W ignore
python scripts/run_tests_isolated.py -- python -c "import packages.web.server; print('server import passed')"
git diff --check
```

Each pytest command was piped as `2>&1 | Tee-Object evidence/quota-native/<log>`.
The isolated child exit status is reported by the execution tool; no assertion was
weakened or error swallowed. Logs:

| Log | Result |
| --- | --- |
| `focused.log` | 16 passed; dedicated quota matrix |
| `business-regression.log` | 225 passed; original business rules/identity/host/source |
| `canonical-regression.log` | 98 passed; default typed effects, Run/Attempt, Creator, HTTP and Session closure |
| `import.log` | Isolated `packages.web.server` import passed; exit 0 |
| `original-red.log` | 3 failed; protected original tests left untouched |
| `red-revocation-and-once.log` | Retained earlier experiment: 2 failed / 11 passed, fixed without weakening assertions |

Dedicated tests use authenticated actual loopback TCP and production `bind` plus
default TypedExecutor. They create only temporary stores, bind random ports and
assert neither 8767 nor 8768; finally stop their own server and verify its listener
is gone. Recording adapter data includes synthetic account/Worker/task identities;
no actual account is connected. `tcp.json` contains default unavailable/cold-host
identity statistics and the owned ports from the last run.

The dedicated matrix proves default unavailable, known 94/95, mismatching account,
CAS/type rejection, invalid authentication, four simultaneous duplicate admissions,
lost reference-persistence response, reset admission, epoch/manual/disabled-account
gates (including lost index), Agent denial/revocation and user cleanup, query receipt
replay, original Attempt continuity during manual change, no legacy mutation calls,
unknown monitor across new Events and cold host reconstruction, no duplicate query
or business wake command, and original legacy owner-fence rejection when unbound.

## Preserved red evidence and limits

Two original assertions still demand old-writer fence rejection:
`test_quota_policy_owned_http_default_unavailable_is_unknown` now receives 200 for
canonical once admission; `test_quota_real_tcp_cas_reopen_and_legacy_writer_fence`
receives account-unknown 409 for monitor95 rather than a legacy fence message.
These expectations conflict with the authorized migration; JOB-RETIRE/MA owns
their revision. The exact original real-HTTP gap test raises NameError at
`monkeypatcreal_http.setattr(h, ...)` before launching a server. That line also
exists at the supplied baseline (verified with git show); no baseline full test
run or patched original test is claimed. The dedicated TCP successor is separate.

The earlier two retained experimental failures exposed invalid Agent principal
metadata (permission revision in Creator metadata) and an expired once timestamp.
The fixes separate principal from authority and map overdue once intent at graph
creation. Earlier monitor publication-order timeouts were not saved as raw files;
their cause was publishing before subscription refresh. The restored strict TCP
monitor cases now cover publication/admission/execution and business mapping.

Cold re-open here means new app/service/executor objects with real startup/shutdown
and reopened durable stores in the same test process, not OS-process crash or
power-loss injection. Canonical read/recovery does not invoke the query provider.
No real supplier, account switch, CLI, Worker restart or actual Session wake was
performed. Wake verification concerns the unchanged business command ledger;
the wider retired automatic-account recovery dispatcher was not restarted or
rewritten. Browser/UI, full backend suite, deployed server startup, real-data
migration, Linux/macOS, remote CI and human acceptance are untested in this task.

No edits to shared worker/server/process/log/pin/cancel/fence/contracts/scheduler-model/shared
conftest or original TA test changes are included. Shared edits are limited to
the explicitly released quota symbols in TypedExecutor and FinalService plus
automation API/policy. No main/practical update, merge, push or deployment.
