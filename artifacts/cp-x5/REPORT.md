# X5 service lifecycle backend candidate

Branch: `feat/cp-x5-posix-20261103`. Baseline:
`c9dc621a01ccb568878c8a37297c034cbaec18de`. Initial working tree was clean.
Candidate SHA is the commit containing this report (obtain with `git rev-parse HEAD`).

## Verification on this Windows host

All test commands used `python scripts/run_tests_isolated.py --`, which cleared
inherited PAN variables and used synthetic temporary directories. No real profile,
provider, protected service, 8767/8768 listener, push or target-branch update.

| Check | Exit | Evidence | Result |
|---|---|---|---|
| Existing launcher/fast lifecycle/P5b contracts, after initial backend change | 0 | `baseline-contract.xml` (historical filename; **post-change**, not baseline) | 33 passed, 2 skipped |
| Backend/prelaunch/strict contracts | 0 | `backend-contract.xml` | 29 passed |
| Backend plus native synthetic HTTP process | 0 | `native-backend.xml` | 14 passed (before adding the lock contract) |
| Final focused combined suite | 0 | `final-contract.xml` | 64 passed, 2 skipped |
| Baseline original supervisor function against new lock contract | 1, expected | `baseline-lock-red.xml` | 1 failed: canonical restart did not hold shared lock |
| `python -c "import packages.web.server; print('server import OK')"` | 0 | tool output | server import OK |
| `git diff --check` | 0 | tool output | no whitespace errors |

Final focused command (after isolated-runner prefix):

```text
python -m pytest tests/test_service_process_backend.py tests/test_launcher.py tests/test_lifecycle_fast_contract.py tests/test_p5b_lifecycle_action.py tests/test_lifecycle_prelaunch_fence.py tests/test_lifecycle_strict_successors.py -q -o addopts= --junitxml=artifacts/cp-x5/final-contract.xml
```

Baseline red reproduction did not overwrite working source: a Python subprocess
read `git show <baseline>:packages/core/main_lifecycle.py`, extracted only the
original `run_canonical_supervisor` AST function, compiled it into the imported
module namespace and ran
`tests/test_service_process_backend.py::test_canonical_restart_uses_existing_cli_start_lock`.
The test failed at its expected missing-lock assertion. Final source passes that
same test in the combined suite.

The two final skips are the existing real-host unsupported-capability tests:
Windows declares service.lifecycle, so their unsupported-host precondition is
false. Explicit injected unsupported negative tests still execute. The suite also
executes two real detached-supervisor **identity rejection** cases; these never
target a real Pan service and do not prove a successful service restart.

Native evidence: real local OS Popen/wait with exit 7, plus real launcher of an
owned synthetic main.py HTTP server on a dynamically allocated loopback port;
verified PID/birth/root/entry/listener/HTTP readiness, verified termination and
offline proof. The finally path checks the owned process and cleans it if needed.
This is Windows launcher primitive evidence, not production Pan lifecycle parity.

## Delivery and limitations

Changed source: `packages/core/platform/service_process.py`,
`packages/core/launcher.py`, `packages/core/main_lifecycle.py`.
New tests: `tests/test_service_process_backend.py`.
API/DB schema: unchanged. Additive persisted field: POSIX `process.json`
main.processIdentity uses existing `pan.process-identity/1` schema.
Capabilities: unchanged; Linux/macOS service.lifecycle remains false.
No frontend source change, so pnpm build/browser acceptance is not claimed.

Parity matrix, primary official citations, exact remaining proof gaps and proposed
path ownership are in `docs/plans&overviews/CP_X5_PARITY_20261103.md`.
Missing: Linux/macOS native execution, successful real canonical service restart/
exit, graceful leader exit plus descendant/group absence proof, native concurrent
start/restart and startup failure/timeout matrix, three-platform CI. No deployment,
real-data migration, human acceptance or MA acceptance is claimed.

The inherited canonical receipt/cancellation/running-policy negative controls were
retained. This candidate does not change unknown into success or add automatic
restart/replay. OS logout/system reboot persistence and hiddenDesktop safety
equivalence need explicit MA product decisions.
