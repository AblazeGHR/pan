# Migration readiness revision2 — owned synthetic runtime proof

Baseline: `b288613e8a1862ea82d16a27d27992b715d3e0ba` (normal FF, accepted revision1 plus r15).
Immutable tested implementation: `74d19f86b7b5b9babc9921acd7c85a6de4fc152a`.
Worktree: `D:/project/pan-worktrees/job-migration-readiness-20261029`.
Branch: `r6/job-migration-readiness-20261029`.
The final evidence commit adds only this evidence directory; it does not alter tested implementation.

Verdict: the existing importer accepts the reviewed synthetic package into a fresh owned profile, preserves identities/history, rejects the exercised invalid inputs, survives injected failures and cold reopen, and executes zero effects. This is a synthetic runtime-readiness candidate for MA review, not a real-profile upgrade or release acceptance. No product seam required modification in this run.

## Changes and scope guard

Only migration scripts, dedicated support/test, docs and evidence changed. `protected-paths.diff` is empty for `packages/**` and `tests/conftest.py` against the baseline. Original tests/assertions, revision1 evidence and other worktrees remain intact. No protected ref, remote CI, push, service, port, account or real profile operation was performed.

- `source_coverage.py`: explicit path/type/hash/disposition accounting beside lite inventory; arbitrary SQLite is hashed as opaque bytes. Link/error/incomplete manifest and copied runtime authority block P9D before target creation. Authority classification takes precedence even inside a lite Job directory.
- `p9d_rehearsal.py`: synthetic expected-count parameters, source coverage guard and explicit zero-effect checks at failure/crash boundaries; existing importer and original trust checks are reused.
- `p9d_support.py`: manual packet no longer asserts old cohort counts or historical approval applies to a new input.
- Dedicated test/support supplies only synthetic input and actual fresh-profile import/cold-process/fence/rollback probes. Runbook: `scripts/job_final_migration/RUNTIME_READINESS_RUNBOOK_20261029.md`.

## Commands and results

All Python commands use `D:/project/Pan-main/.venv/Scripts/python.exe` both outside and inside `scripts/run_tests_isolated.py --`. Local OS: Windows. No new skip/xfail was added.

Final regression command at immutable source above, exit **0** (`final.exit`, `final.log`, `final.xml`):

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_job_migration_runtime_readiness.py tests/test_job_migration_readiness.py tests/test_job_final_migration_user_authorization.py tests/test_job_final_migration_review_packet.py -q -o addopts= -k 'not all_51' --junitxml=evidence/job-migration-runtime-r2-20261029/final.xml
```

**64 passed, 1 deselected, 0 skipped, 32.22 seconds.** New dedicated suite contributes 18 nodes. The one existing `test_all_51_import_user_owner_history_receipts_preserve_60_and_recover_crash` is intentionally excluded under this revision's explicit prohibition of the old 51/60 set; it is unchanged, not reported as passed. Accepted historical 208/42 counts are not part of this run.

Standalone public support command, exit **0** (`final-rehearsal.exit`, `final-rehearsal.log`):

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 tests/support/job_migration_runtime_rehearsal.py run --output .validation/job-migration-runtime-r2/final-74d19f86
```

Use a new output directory to replay. Full synthetic private files remain under that ignored output. Selected machine evidence is copied unchanged to `final-rehearsal/`; no profile database, full authorization/context, source fixture or deletion command is committed.

Preserved red-to-green evidence:

| Evidence | Original result / exit | Successor |
| --- | --- | --- |
| `baseline-red.xml/log` | missing coverage module and synthetic CLI parameters; 2 failed, 1 passed / 1 | final coverage/CLI/import nodes pass |
| `manual-scope-red.xml/log` | generated guide claimed historical cohort approval; 1 failed / 1 | final manual-packet node passes |
| `authority-red.xml/log` | authority filename in lite directory omitted from blocking list; 1 failed, 17 deselected / 1 | final authority-precedence node passes |
| `verified.xml/log` | previous source `4d4ce5431f1e4d587d9374e5a697e12404d7f4bf`, 63 passed / completed before continuation | superseded by final 64-node run after actual guard fix |

Development logs and the original stale/historical task evidence are preserved, not substituted for final results. Source `git diff --check` passed, exit 0. The evidence-inclusive staged check exits 2 on three original trailing-whitespace lines in the preserved manual-scope red JUnit; those raw failure lines are intentionally unchanged.

## Machine facts at final source

`final-rehearsal/summary.json`, `failure-recovery.json`, `id-mapping.json`, `source-coverage.json`, `validation-bindings.json`, `source-before.json` and `source-after.json` carry exact identities/hashes. `evidence-hashes.json` binds the selected files and test outputs.

- Discovered **25 paths / 18 files = 7 handled + 8 preserved + 3 unrecognized**. All 18 original file hashes remain equal after import/failures/replay. Eleven paths remain outside lite mapping, including logs/pins, independent outbox, cursor, time-source SQLite, Session pending and unknown blob. Record classification remains authoritative even for a handled file. `wholeProfileMigrationComplete=false`, `allLegacyZeroProven=false`.
- Actual canonical SQLite: **2 disabled schemaVersion=2 Jobs, 2 history, 2 original-key source-stated-delivered receipts, 5 unrecognized, 1 activation**. Unknown records receive no target IDs. Logs/history readable; no new Run/outbox/effect, no pending settlement or legacy adoption.
- Bundle publication injection leaves target absent. History-write failure rolls every imported count back to zero; crash child exits **77** during the import transaction, again leaves zero imported rows and retains activating/fenced authority. Original context/spec/identities then recover successfully. NoEffects calls remain zero.
- Fresh Python process cold reopen exits **0**, same rows/IDs, disabled Jobs, zero effects. Normal restart owner epochs progress 4→5, cold process 6, later real claim 7. Stale expected epoch, simultaneous OS-lock owner and released old writer reject; no epoch/nonce/activation-intent reset.
- Nine real runtime negative nodes reject wrong/missing receipt pin, bundle hash, plan digest, Action revision/digest, schema, active IDs and rolled-back bundle. Each has zero partial imported rows, no task/effects, released failed owner handle and preserved original activation intent. Deeper-negative bundles are deliberately corrupted test inputs; no valid authorization/release is forged.
- Offline rollback after import leaves runtime rows unchanged. Reapply of the original offline context/receipt yields the original bundle digest. This is not runtime rollback.
- Cleanup: child exit 0, runtime task closed, owner handle released and state closed, no opened ports, no `.tmp` files. Lock file is intentionally retained under the original owner contract.

## Acceptance boundary and next action

MA can review the narrow diff, replay the standalone command in a fresh owned output and inspect the final JUnit/JSON bindings. The synthetic authorization has explicit fixture provenance, not real user consent. Source/schema hashes in `validation-bindings.json` are QA bindings, not a newly invented runtime pin protocol.

Still unverified/unauthorized: real inventory and per-record approval, original source owner/PID/lease drain, active/pending/uncertain same-identity adopt/status/settle, unknown SQLite/cursor/outbox semantics, real provider grants and receipts, recovery after invalid-spec activation intent, runtime data rollback, POSIX runtime/remote CI, actual upgrade/deployment. A new synthetic target's fence cannot prove source authority migration. Do not use this candidate to declare whole-profile migration complete or delete preserved sources.
