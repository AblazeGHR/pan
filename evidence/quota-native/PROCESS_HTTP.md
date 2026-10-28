# Owned production-process HTTP successor

Parent source candidate: `a66edbaa94c1a12b22915267e22346a255f3740f`.
Same worktree and branch: `r6/quota-minimal-closure-20261028-a`.
This addition changes only the dedicated test and its evidence/fixture. Product
sources, shared support, shared conftest and original red tests are unchanged.

## Exact successor mapping

All new nodes below are in `tests/test_quota_native_admission.py`.

| Preserved original node / requirement | Strict successor and replacement observations |
| --- | --- |
| `tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown` | `test_unknown_production_process_restart_real_http`: policy CAS/disabled-account configuration and authenticated canonical once admission; `/api/jobs/{jobId}/runs` and `/api/job-runs/{runId}` replace legacy quotaResult reads. The original 409 fence assertion is preserved as red evidence. |
| `tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | `test_unknown_production_process_restart_real_http`: real production server OS process, unavailable quota execution, original canonical Run/Attempt/effect, owned shutdown and second OS process startup on the same temporary profile, original identity/policy reads and no resend. The original fixture typo remains unchanged and red. |
| `tests/test_job_retirement_quota_transport.py::test_quota_real_tcp_cas_reopen_and_legacy_writer_fence` | `test_unknown_production_process_restart_real_http` covers invalid auth, CAS, persistent disabled account, canonical reads and independent process reopening; `test_unbound_legacy_admission_still_fenced` preserves the unbound old-writer fence requirement. The original bound-legacy fence assertion remains unchanged and red. |
| monitor95 with unknown default account | `test_unknown_production_process_restart_real_http` asserts business 409 with account-unknown reason, no canonical Job created, and one default query during account binding. This is followed by a fresh-CAS once admission. |
| retired unbound writer still fenced | `test_unbound_legacy_admission_still_fenced` asserts owner-fence ContractError and zero adapter queries. Canonical binding does not restore the old writer. |

## Actual process and execution boundary

The test uses existing `_PanRuntime` from `tests/test_real_queue_http_e2e.py`.
Its launcher is the owned `production_http_server.py`, which delegates to the
existing `tests/support/automation_http_server.py` and
`tests/support/isolated_http_server.py`, importing `packages.web.server` and running
the actual production lifespan and default FinalService/TypedExecutor binding.
There is no in-test ASGI app, lifespan-off route host or replacement query provider.

The launcher wraps the existing default Adapter methods solely to append method,
PID and timestamp observations inside the temporary profile, then delegates to
the original implementations unchanged. No arguments, credentials, account data
or fabricated quota response are logged. The default remains Adapter/unavailable.
This test log is not product persistence or an execution/replay ledger.

The test expects exactly two default query invocations: one rejected monitor95
account-binding query, and one admitted once execution query. Inventory and
perform are never invoked. The sole admitted execution has one original Run,
one uncertain Attempt, one unknown effect and stable effectKey. Repeated reads,
exact duplicate admission, normal service ticks, owned process shutdown and a
new process startup cause no additional Adapter method call, wake command,
Attempt or effect.

Public authenticated canonical HTTP supplies Job/Run reads. Since there is no
public Attempt/effect detail route in this candidate, the test supplements those
reads with `mode=ro`/`PRAGMA query_only` SQLite reads of canonical `final_documents`
and compares the authoritative aggregate before and after process replacement.
It never reads legacy Job/quotaResult projections or writes the canonical ledger.

The test allocates a random loopback port and asserts it is neither 8767 nor 8768.
`_PanRuntime` launches using the current interpreter with temporary config,
empty plugin manifests and no Sessions. Before each shutdown it captures only
the owned process tree, PID and process birth time. The test identifies which
owned PID actually owns the selected listening socket and attributes query
observations to that listener. Shutdown uses the existing owned fixture endpoint;
only that captured process tree is eligible for fallback cleanup. Each generation
must exit with code 0, every captured PID/birth identity must be gone, the port
must reject connection and permit a fresh bind before restart/final exit.
`process-tcp.json` records both generations and all cleanup assertions.

## Commands and results

Working directory: `D:/project/pan-worktrees/quota-native-admission-20261028`.
Interpreter: `C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`,
Python 3.14.5. Exact source hashes are in `process-source.json`.

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_quota_native_admission.py::test_unknown_production_process_restart_real_http -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/process-http.log
python scripts/run_tests_isolated.py -- python -m pytest tests/test_quota_native_admission.py -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/process-closure.log
git diff --check
```

The initial process-only run passed in 14.21 seconds (`process-http.log`). The final
full dedicated matrix, including listener-PID attribution and unbound fence,
passed **17 tests in 84.14 seconds**, exit 0, recorded in `process-closure.log`.
The final run used port **53564**, listener PID **18256** (birth
1793161869.565302), then listener PID **27292** (birth 1793161877.8439755).
Their captured companion PIDs 34416 and 37212 also retired. Both generations
exited 0 and released the port; `allOwnedProcessesStopped` is true.
Existing 225 business and 98 canonical/Session
passes belong to parent a66edbaa; they were not rerun or counted as new validation
for this additive test-only change. The three protected original red tests and
`original-red.log` are unchanged.

An initial strict anonymous-write assertion failed with 403 because the login
response had set a cookie on the HTTP client. `red-process-cookie.log` preserves
that failure. The test now explicitly clears its client cookies after acquiring
the Bearer token, then continues to assert exact 401 for anonymous/invalid Bearer
and exact CAS status codes. The original assertions were not weakened.

This proves normal owned OS process shutdown/restart with durable canonical
identity. Abrupt kill/crash, power loss, real account/provider consumption, actual
Session wake, full backend suite, browser, remote CI, Linux/macOS and deployment
are not claimed. No protected port or existing service was operated. No product
redesign, main/practical update, merge or push is included.
