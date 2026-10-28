# Historical Card recovery after canonical cutover

Baseline: `87c444ce896e768a75bc74fde1af834952f4875c`. Original task branch/worktree: `r6/job-source-receipt-20261029` / `job-source-receipt-20261029`.

## Diagnosis and change

Remote run `40661181678` failed the four parameters of `test_p7a_successors.py::test_legacy_locker_choice_never_reenters_retired_execution` in both Windows Python artifacts (3.12 and 3.14). `remote-original-owned-red.json` retains the eight original inventory entries and traces. Artifact collection completed; that does not mean the CI tests passed. The same four failures were reproduced locally in `original-red.xml` and `.log`.

The test described historical v1 data but created its Card through the current live resolver after claiming the canonical owner. The resolver correctly refused a legacy-only Trigger absent from the canonical graph. The fixture now persists the actual v1 Card, binding, response and lost original Event receipt before owner claim. It initializes the old stores and uses the existing resolver/adapter without starting a legacy dispatcher or effect loop; only afterward does it claim and start the canonical host. No production creation path is downgraded.

Correcting the fixture exposed a real read compatibility gap: two no-receipt cases remained unknown as expected, but the two cases with original persisted legacy receipts also remained unknown (`receipt-gap-red.xml`). The canonical lookup only inspected canonical event operations.

`packages/interactions/runtime.py::bind.lookup` now reads the retained EventStore only if no canonical event operation exists for the original identity. The record must validate as an interaction Event with a v1 binding, matching Trigger identity/revision and exactly one matching original directed target. Invalid records and v2 Events cannot produce a legacy receipt. This only reports existing Event persistence; it does not authorize a graph, complete an effect, rebind a Card, append an Event or replay work. Existing canonical operation lookup takes precedence.

The historical test's seven original assertions remain intact (AST comparison in `assertion-preservation.json`). Three additional assertions prove v1 identity, immutable bindings after cutover, and immutable bindings after reopen. A new negative check proves that fresh creation after cutover still rejects that legacy-only Trigger. Existing checks still forbid reauthorization/resubmission, a second Event, legacy effects, new Jobs and legacy Runs, including detached deleted-Card recovery.

## Verification

All backend runs used the owned isolation wrapper:

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest <nodes> -q --tb=short --junitxml=<report>
```

| Report | Result | Nodes |
| --- | --- | --- |
| `original-red.xml` | 4 failed | Original historical node at exact baseline, before edits |
| `receipt-gap-red.xml` | 2 passed, 2 failed | Authentic pre-cutover fixture, before product lookup repair |
| `history-final.xml` | 4 passed | All original historical parameters after lookup repair |
| `focused-final-2.xml` | 32 passed | All four original parameters plus complete canonical suite (28 cases) after final assertions |
| `recovery-final.xml` | 106 passed, 3 failed | Full `test_p7a_successors.py`, canonical suite, interaction cards/no-answer/questions/card API/runtime groups |

New canonical tests cover retained v1 persistence, mismatched target, revision, fanout, binding, untrusted source and a v2 Event without a canonical admission. Repeated queries preserve the original record and create no Run. Existing canonical admission, unknown lookup, scope/authority and concurrent graph-update tests remain in the complete suite.

The recovery group's three failures are the previously retained legacy setup failures in `test_interaction_questions.py`: `test_trusted_question_scopes_and_independent_unknown_reconciliation`, `test_large_v2_runtime_admission_and_scope_recheck`, and `test_question_event_survives_deletion_without_resend`. All stop in unchanged `test_locker_trusted_binding.setup` -> `Service.start` with `DependencyUnavailable('Canonical Job owner required; legacy Automation host is retired')`. These files and the service are unchanged from this baseline; no assertions were skipped, weakened or converted to xfail. Existing canonical successors remain covered.

Intermediate raw red reports (`historical-boundary-red`, `focused-final`) are retained too: fixture preparation initially missed EventStore initialization and the new direct-test setup initially missed the normal canonical EventStore owner attachment. Both were corrected through existing initialization/binding semantics, without disabling fences. Test counts across reports overlap and must not be added.

## Delivery limits

Source changes are limited to `runtime.py`, the owned canonical test file and the exact historical fixture/node in `test_p7a_successors.py`. Source diff whitespace checks pass; raw failure reports retain pytest's original whitespace. No schema, frontend, quota, shared host/server/worker or other fixture changes. No new ledger or effect protocol.

This is Windows isolated local regression evidence. No new frontend build/browser run was needed for this backend-only change; no remote CI, Linux/macOS, real account/provider consumption, deployment or real-data migration result is claimed. No push, main/practical, existing services or ports 8767/8768 operations. MA owns combination and subsequent push/CI.
