# R3 runtime target/text preservation guard

Baseline: `b288613e8a1862ea82d16a27d27992b715d3e0ba`.
Previous review: `1d0c8d12fd587eb9834ecfaa06e501e7f0f7a45e`.
Guard implementation: `6ad2e33ed3ea08c42aac7e0eb842f2d790cb77d1`.
Final verified source, including runtime-only and mixed CSV tests:
`9ea32689b2667c1f83b4e4e095db1048385f126d`.
Intermediate test source: `f0a9b8dd6876bd2a21c873d0e1317f19f868f2ef`.
Branch `r6/job-migration-readiness-20261029`, same owned worktree.

The 447424c2/bff3e59c runtime matrix is approved implementation work. No new
user decision is requested. The earlier review's claim that a missing Binding
enum requires a new model decision was too broad: existing `input` bindings
already express pre-admission target/text changes. This candidate reuses that
shape in explicit mapping proposals and prevents unsafe literal publication
while the delivery-manager acceptance contract remains unwired.

## Concrete behavior

`runtime_fields.delivery_binding_gap` describes the approved Creator/Delivery
Manager owner, pending/reserved phase after Event publication, required original
Event/dispatch identity, runtimeRevision/CAS and reservation fence, text revision,
effective receipt stage and per-target receipts. It supplies proposed existing
bindings `delivery.sessionId`, `delivery.sessionIds`, `delivery.text`. Mutable
text requires the Action's `runtimeBeforeAcceptance` declaration. These are
review metadata, not executable canonical fields or a new Binding type.

`inventory._classify_message` and `_classify_scheduled` retain message,
broadcast, legacy scheduled assign and scheduled assign/send_session as
`incompatible-preserved`, reason `runtime_binding_unavailable`. Their target
IDs are empty, fieldMap is not advertised as lossless, and next action is
`retain-source-manager-await-runtime-contract`. Existing missing-target,
pending-notice and uncertain-effect reasons remain alongside the new reason.
No source state, cursor, original receipt or outcome is rewritten. Accepted,
success, unknown and partial recipients stay on original identities; a source
known-no-attempt statement is retained and is not automatically declared an
unknown effect or safe resend.

`Mapper.map` independently rejects a stale or relabelled SIMPLE runtime-delivery
item before proof processing/object allocation. Explicit partial bundles can
still import an unrelated supported process while keeping the delivery record,
raw base64 backup and gap metadata. A source containing only affected records
can be inventoried publicly but cannot publish an empty canonical replacement.
Review packet and Markdown expose the owner/phase/input proposal and route this
to product implementation, with no new D2/D3 user decision for this gap.
Mixed preserved CSV rows use a deterministic column union so runtime gap and
implementation-follow-up fields survive either row order; the existing source
values and spreadsheet-escaping rules remain unchanged.

This does not implement delivery parity and does not authorize retiring or
fencing the original source manager. It prevents one incorrect migration.
Event backlog exists in b288 and is not reported as absent. A fresh synthetic
target's local owner fence does not prove real-source migration.

## Exact product-owner handoff

Read-only source evidence is `packages/jobs/final_service.py`:

- `admit_event` (around 1951) can use current Job `arguments` before admission;
- `_admit` (around 711-755) copies input into the frozen Run plan;
- `_drive_run` (around 1507) resolves from `frozen["input"]`.

Thus input shape works; alone it does not prove the approved pending/reserved
updates through queue/external acceptance. Product owners should reuse the
existing Event/admission path and delivery manager, expose the approved
owner/runtime declaration in schema/API/UI, and bind effective revision/target/
text digest and phase to the original operation/requestKey, reservation fence
and per-target receipt. Once wired, migration can replace these proposals with
the consumed input mapping and re-review exact source hashes. No second
dispatcher/ledger, new Binding source, Action-version change or effect-key
replacement is requested here.

Notification target/payload belong to Notification Manager and need original
outboxId/transitionSeq, pending not-sent proof, target revision/CAS and payload
digest. This candidate does not change notification migration or assert it is
complete. QQ bot/target/text require an explicitly pinned runtime declaration;
this guard does not infer runtime capability from familiar QQ fields. Original
shell/QQ mapping tests without such a declaration still pass in the broad run;
no new claim about QQ runtime delivery is made.

## Validation, commands and immutable binding

All Python commands below use the designated interpreter twice via the isolated
runner, from this worktree. This is local Windows synthetic evidence, not CI or
provider/real-profile proof. No new skip/xfail or original assertion was changed.

| Run | Source | Result / exit | Evidence |
| --- | --- | --- | --- |
| Initial new tests | previous implementation, new dedicated tests | 11 failed / 1, most due to an owned fixture naming mistake | `runtime-binding-red.xml/log` |
| Corrected new tests, before fix | previous implementation | 11 failed / 1, actual literal/preservation/guard assertions | `runtime-binding-red-corrected.xml/log` |
| CSV optional-column reproduction | f0a9b8dd with one new dedicated test | 1 failed / 1 after correcting one fixture's missing sourceFile | `runtime-binding-csv-red-corrected.xml/log` |
| Dedicated guard development | dirty implementation | 12 passed / 0 | `runtime-binding-development2.xml/log` |
| Dedicated R2/R3 and authorization | intermediate f0a9b8dd | **45 passed, 1 deselected / 0** | `runtime-binding-verified.xml/log/exit` |
| R2/R3 + original authorization/review packet | guard 6ad2e33e | **56 passed, 1 failed, 1 deselected / 1** | `runtime-binding-final.xml/log/exit` |
| Original inventory/apply/partial/revision1 assertions | guard 6ad2e33e | **63 passed, 16 failed, 1 skipped / 1** | `runtime-binding-original-assertions.xml/log/exit` |
| Final dedicated R2/R3 + authorization + original review packet | final 9ea32689 | **58 passed, 1 failed, 1 deselected / 1** | `runtime-binding-final-source.xml/log/exit` |
| Fresh standalone mixed preservation/dry-run/apply/replay/rollback | final 9ea32689 | **exit 0** | `runtime-binding-final-source-probe.log/exit`, `runtime-binding-final-source-probe/` |

Final combined command: all **14 new runtime-delivery cases**, 18 unchanged R2
cases and 14 authorization cases passed (46 total); 12 original review-packet
cases passed and one old simple-count assertion failed. No failing node is
excluded from this result. R2 includes actual NoEffects runtime import, cold
process, transaction/crash recovery and owner-fence checks.

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_job_migration_runtime_readiness.py tests/test_job_final_migration_user_authorization.py tests/test_job_final_migration_review_packet.py -q -o addopts= -k 'not all_51' --junitxml=evidence/job-migration-runtime-r2-20261029/runtime-binding-final-source.xml
```

The single deselected node is the unchanged old 51/60-set test prohibited by
this task. It is not counted as passing. One original broad-run link test skips
because Windows link creation fails with WinError 1314; no condition was added.

Preserved broad commands:

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_job_migration_runtime_readiness.py tests/test_job_final_migration_user_authorization.py tests/test_job_final_migration_review_packet.py -q -o addopts= -k 'not all_51' --junitxml=evidence/job-migration-runtime-r2-20261029/runtime-binding-final.xml
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 -m pytest tests/test_job_final_migration.py tests/test_job_final_migration_apply.py tests/test_job_final_migration_partial.py tests/test_job_migration_readiness.py -q -o addopts= --junitxml=evidence/job-migration-runtime-r2-20261029/runtime-binding-original-assertions.xml
```

All **17 fresh reds** are listed by exact node/message in
`runtime-binding-red-inventory.json`. They are not baseline failures or waived
assertions. They include old six-simple/eight-import counts, literal message/
scheduled publication and lookup, related partial runtime import and schedule
rule tests, plus the old stale-context check for a message now excluded from
execution. Its source is preserved rather than mapped, so the prior selected-
record attestation check is not entered. These reds and the product-manager
handoff keep full migration/feature-parity acceptance open.
The 16-node broad original-assertion run is bound to 6ad2e33e; subsequent code
only adds dedicated tests and the review CSV column fix, leaving its
inventory/Mapper/apply implementation unchanged. No full rerun at 9ea32689 is
claimed. The fresh final review-packet failure is bound to 9ea32689 separately.

Standalone replay (new owned output directory required):

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 tests/support/job_migration_runtime_binding_probe.py --output .validation/job-migration-runtime-bindings-20261031/operator-fresh-01
```

Fresh proof: one BG canonical entry, one preserved send_session entry with no
target IDs; backup exactly equals original bytes; source-before/source-after
hashes equal; idempotent replay writes zero; offline rollback keeps runtime
fence false. No actual provider/HTTP/process effect runs in this probe, no
ports or `.tmp` files. Private full bundle is retained only in `.validation`.

Only migration scripts, dedicated tests/support and docs/evidence changed.
`git diff 1d0c8d12 HEAD --check` is clean for tested source. `packages/**` and
global fixtures remain identical to b288. Historical evidence is preserved.
The evidence-inclusive staged whitespace check exits 1 on original pytest
failure lines in raw logs/JUnit. Those failure bytes are retained unchanged;
no source whitespace issue is being waived.
No protected ref advancement, push, remote CI, service, real-data/account or
8767/8768 operation occurred. POSIX target runtime, actual manager acceptance
updates, real authorization/profile migration and release remain unverified.
