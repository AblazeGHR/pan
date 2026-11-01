# Revision6: selected production read-only construction seam

Parent candidate: `550c150d8112655d3f6627fa66edbf23a17472ac`.
Same task branch/worktree; no rebase, reset, new TA or protected-ref movement.
This revision implements MA's explicitly selected Service construction seam,
superseding the unimplemented proposal/fixture-only limit in FOLLOWTHROUGH.md.

## Delivered changes

`Service.__init__` calls `quota_host.bind_readonly` immediately after constructing
QuotaPolicy, with its existing worker and trusted `Path(root).parent` profile root.
It neither depends on request payload nor executes before-auth API binding.

The helper only replaces the exact default Adapter. It returns existing custom
adapters (including an already-bound HostAdapter) unchanged, so repeated binding
does not reconstruct or overwrite a host-injected implementation. The existing
production factory constructs ReadSource/Bridge/Bindings only. Inspection of
ReadSource.__init__, Bridge.__init__ and ProviderSettings.__init__ found path and
object assignments/imports, with no query, account/profile read, CLI, process
probe, task or loop. The new tests forbid source/bridge reads during construction.
The helper requires HostAdapter with zero mutation ports before assigning
adapter.runtime and policy.adapter. An invalid factory leaves the original
adapter untouched and raises explicitly.

Host defaults remain disabled. Every HostAdapter query and inventory call still
passes its existing enabled/store gate before source reads. Disabling, policy
epoch/CAS, authorization, immutable Event/Run/Attempt/effect and unknown handling
are unchanged. Production mutation ports remain empty; perform rejects unbound
wake/switch/restart operations. There is no scheduler/dispatcher/watch restoration,
inventory-driven business command, provider protocol, new ledger or retry system.

`tests/support/quota_host_http_server.py` no longer monkeypatches Service.__init__
or assigns policy.adapter/runtime. It only replaces `quota_host.production` with
its existing owned fakeCLI/cache factory. The production helper now calls that
factory. Instrumentation wraps sample unchanged and records fixed phase/status
fields inside the temporary profile, without arguments/secrets or business ports.

The production-process unavailable launcher records HostAdapter methods as well
as Adapter methods, delegating both unchanged. This preserves the strict existing
two-call observation when the default disabled HostAdapter is bound: one rejected
monitor account binding and one once execution. Neither invokes the source while
disabled; original unknown Attempt/effect survives duplicate/reopen/recovery gates.

## Actual validation

Interpreter: `D:/project/Pan-main/.venv/Scripts/python.exe`, Python 3.14.5.
Working directory: `D:/project/pan-worktrees/quota-native-admission-20261028`.

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_native_admission.py -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/revision6-native.log
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_quota_host_http.py tests/test_quota_host.py tests/test_quota_provider_source.py -q -o addopts= --tb=short -W ignore --basetemp=evidence/quota-native/revision6-owned-temp 2>&1 | Tee-Object evidence/quota-native/revision6-host.log
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_automation_canonical.py tests/test_quota_policy.py -q -o addopts= --tb=short -W ignore 2>&1 | Tee-Object evidence/quota-native/revision6-service.log
git diff --check
```

| Batch | Actual result | Exit |
| --- | --- | --- |
| Native plus production-process HTTP successor | 20 passed in 93.97s | 0 |
| Host/provider plus original unchanged host HTTP assertions | 84 passed in 27.29s | 0 |
| Service/canonical plus original policy | 19 passed in 2.17s | 0 |

New dedicated tests prove construction and disabled host perform zero bridge/source
reads; bound adapter points to the original Runtime with no ports; repeated helper
calls preserve object identity; custom adapter is never overwritten; forbidden
mutation factory is rejected without assignment; Service supplies the trusted
profile root; disabled query has zero samples; enabled canonical once has exactly
one sample; duplicate admission and cold host reopen preserve original uncertain
Attempt/effect with no repeated sample; disabling restores zero-source behavior.

The unchanged original real-HTTP host node exercises the actual product binding
and all original assertions: authenticated host configuration, exact CAS/type
gates, known provider/current account domain, native weekly 70, command whitelist,
unchanged quota-cache bytes, no business commands/secrets, disable, two OS process
generations, persisted auth/logout, and no later CLI activity. Its bounded factory
records show **two constructions, one sample-start, one known sample-end**, and
zero mutation ports: `revision6-host-binding.jsonl`. Policy/CLI/cleanup statistics
are in `revision6-host-proof.json`. Generated historical artifact was copied to
bounded evidence then restored to its exact pre-test bytes; temp keys/databases
are excluded from Git by a local ignore file and explicit staging.

The existing strict process successor also proves original Event ID/digest,
Run/Attempt/effect identity and stable effectKey after owned OS process replacement;
unknown retry remains reconciliation_required and manual-mode admission is denied.
`process-tcp.json` contains captured PID/birth/listener identities, both exit 0,
port-release/fresh-bind and no-resend assertions. Its owned cleanup was rechecked
after the batch. No existing service/protected port or real account was involved.

The three original protocol-red tests/assertions and all previous raw red evidence
remain unchanged; they are not reclassified as green or redundantly rerun here.
Their exact canonical node mappings remain in FOLLOWTHROUGH.md. Original host
timeout red is also preserved there alongside the revision5 fixture diagnosis.

Current product diff is only the Service seam and quota_host helper; no API,
FinalService, typed executor, worker/server, shared host/contracts/ledger/owner or
Session/card change. Source hashes, protected-file byte checks and exact commands
are in `revision6-source.json`.

No full suite/browser/build, Linux/macOS, remote CI, deployed runtime, true account
CLI/provider consumption, actual wake/switch/restart, abrupt crash/power-loss or
human acceptance is claimed. Product source binding is now implemented and owned
fixtures prove it; deployment/real-account acceptance remains outside this task.
No push/merge into main/practical or service operation outside owned fixtures.
