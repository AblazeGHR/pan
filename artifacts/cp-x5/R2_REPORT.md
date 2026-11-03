# X5 R2: original group evidence and native target gate

Parent candidate: `9cd64041b0cc68debedc29565f730a6aa0837483`.
Source snapshot submitted for the Windows full native trial:
`5c2219d5` (full SHA is recorded by the native harness in native-service.json).
Later snapshot-member validation is additionally covered by focused contracts;
the native trial does not pretend to test uncommitted POSIX changes.

## Source behavior

- `platform/service_process_posix.py` reuses existing strong ProcessOwner identity,
  member observation and read-only group probes. Original process.json stores
  processTreeSnapshot before graceful shutdown. No extra ledger or replay engine.
- Dead leader: read-only original group/observed-member absence may confirm exit.
  Missing/weak/cross-boot snapshot, permission error, live group or observed member
  preserves unknown. No dead-leader path sends a group signal.
- launcher stop cannot clear original tree evidence merely because PID/port are
  absent. Canonical offlineProof carries serviceTreeProof; status remains unknown
  for an unresolved POSIX tree. Windows retains its existing tree executor.
- Failed strong-identity serialization after owned spawn invokes kill_tree on the
  ORIGINAL retained handle. Existing ProcessOwner native spawn-abort path is also
  exercised by target tests when post-spawn strong identity cannot be read.
- Frontend/API request schema and capability registry unchanged. Additive field:
  process.json/main.processTreeSnapshot and offlineProof.serviceTreeProof.
- debug is intentionally foreground/weak: no new group kill permission is inferred.

## Local Windows evidence

All pytest commands use `python scripts/run_tests_isolated.py -- python -m pytest`.

| Check | Result | File |
|---|---|---|
| R2 combined focused contracts | 104 passed, 5 skipped, exit 0 | r2-final-contract.xml |
| Tree evidence, native startup timeout and missing executable | 12 passed, 3 POSIX-only skips, exit 0 | r2-tree-contract.xml |
| Prior CLI/diagnostic subset | 36 passed, 3 POSIX-only skips, exit 0 | r2-cli.xml |
| Original R1 stop function against new original-tree preservation contract | 1 expected failure, exit 1 | r2-baseline-tree-red.xml |
| server import + capability projection | exit 0; Windows true, Linux/macOS false | tool output |
| Dispatch-only YAML parse | exit 0 | tool output |

The 5 combined skips are 3 explicit POSIX native tests on Windows plus 2 existing
unsupported-host lifecycle tests whose host precondition does not apply on Windows.
Injected unsupported-path tests execute. No skip is target-platform proof.

Baseline red replay compiles only `stop_service` from `git show 9cd64041:packages/core/launcher.py`
into the isolated test process module, then runs
`test_absent_pid_and_port_cannot_erase_original_tree_unknown`. It fails at the old
clear_state path: "unknown tree must retain original evidence". No working source
was overwritten. Trailing whitespace in traceback XML is normalized only.

Combined focused files: test_service_process_backend.py, test_x5_service_evidence.py,
test_launcher.py, test_pan_cli.py, test_cli_diagnostics.py, test_lifecycle_fast_contract.py,
test_lifecycle_prelaunch_fence.py, test_lifecycle_strict_successors.py,
test_p5b_lifecycle_action.py. Original receipt/authorization/cancellation controls
remain intact; startup timeout test uses a real owned unready main process and
checks its termination and dynamic-port absence.

## Native Windows trial completed

Durable Job: `job_23960ea9e8f4fc2712308ef9`, targeting this TA Session
`ses_319c647f585e2a85`; received its built-in terminal notification and verified
status=completed, exitCode=0, notificationState=delivered, snapshotCleanup=cleaned,
processTreeEnded=true. No background polling/watcher or directnotify was installed.
Result: **1 passed, 0 skipped**, 27.188 seconds. Evidence: r2-native.xml,
r2-native/native-service.json and r2-native/cli-commands.txt (commands only).
The tested archived product source SHA is
`5c2219d5cdc8c1e884b4f42bb59006cd917c8459`.

The harness archives a committed source into a temporary owned checkout, creates
an empty synthetic config/data set, disables channels and providers, uses a dynamic
non8767/8768 port, and records source provenance. Its marker-fenced disposable
sitecustomize injects only service.lifecycle for candidate execution; this is
explicit test-only capability injection, never a production support declaration.

It tests real CLI caller exit followed by surviving main, status, HTTP stale birth
and ask rejection, HTTP canonical restart, original requestId/effectId lookup,
CLI preserve restart/offline exit, and separate OS-process start_lock competition
with CLI start and canonical restart. A lock failure must keep the original unknown
receipt and cannot restart when queried after lock release.

All native assertions passed on Windows: startup caller exited while service
remained alive, original request lookup did not replay, CLI restart/exit/status
completed, separate-process CLI/canonical start_lock competition preserved an
unknown receipt without replay, and the owned port was released. The harness also
confirmed original supervisor exit before listener cleanup. No Linux/macOS result
is inferred from this run. Subsequent changes to the export format remove verbose
CLI receipts from delivered logs; the original commands and boolean facts remain.

## MA target handoff

`.github/workflows/x5-service.yml` is workflow_dispatch only, Windows/Ubuntu/macOS
Python 3.12, independent of existing main CI. Installs existing minimal/QQ runtime
dependencies and pytest-timeout; collects and runs shared contracts, then explicitly
opts into native trial. Artifacts always retain target failures and skip reasons.
It is neither pushed nor dispatched here. GitHub requires initial default-branch
workflow registration; MA decides how to register it before independent-ref dispatch.

Linux/macOS native results are still missing. Group evidence scope is original
group + observed members, not OS containment against arbitrary unobserved escaped
descendants. No logout persistence, boot registration, hiddenDesktop equivalence,
real data migration, deployment, provider/account operation or human acceptance.
