# P6 follow-up on 267287c

The original candidate is retained. Work continues on the same branch and with
the same owner/fence/receipt and preservation boundaries.

## Slice 1: adopted flat MCP in all three projections

`test_P6_GROUP_05_old_to_upgraded_stdio` now executes the complete original old
JSON Group → adoption → specified Action v2 → explicit publication → old/future
pin chain in standalone, native and gateway projections. Each uses an actual
stdio subprocess and trusted IPC. The original flat schema, default/escaped/
UTF-8 business data, original release files, business requestKey, independent
transport key, changed-payload conflict, unknown operation identity, no replay,
revocation and absence of hidden Jobs are asserted in each projection.

Result: **3 passed**; evidence `evidence/action-unification-p6/flat-three-modes.xml`.
Command: `python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_unification_p6.py -k old_to_upgraded_stdio -q --tb=short --junitxml=evidence/action-unification-p6/flat-three-modes.xml`.

This closes the previous adopted-flat native/gateway stdio evidence gap. It does
not change arbitrary existing-writer eligibility or claim real model Worker,
real-profile, browser, deployment or target-platform acceptance.
