# P6 Template boundary and original Trigger receipt handoff candidate

Baseline: `ed55d259fc3214b361a8296a9da60436d96a001f`.
Branch: `implement/action-unification-p6-20261106` in the original isolated worktree.
Candidate identity is the commit containing this report; its exact SHA is supplied
in the delivery response. No push, deployment, real-profile migration or MA
acceptance is implied. **P6 remains incomplete.**

The controlling contract is revision 4 of
`docs/design/action-unification/ACTION_UNIFICATION_DETAILED_PLAN_20261104.md`,
especially immutable releases/versions, original identity/no replay, §16's
capability preservation and §17's Scheduler continuity. Its recorded old 410
state is not acceptance of capability removal. The P3 MA review and prior P6
reports remain applicable. No legacy engine, writer, ledger or scheduler was
reactivated.

## Original reds and compatibility matrix

The original seven reds remain unchanged in the previous candidate's
`scheduler-baseline-retirement-red.xml.gz`,
`scheduler-retained-retirement-red.xml.gz` and
`scheduler-retained-combination-red.xml.gz` artifacts. New red artifacts and
SHA-256 values are indexed in `evidence/action-unification-p6/closure-validation.json`.
New failed XMLs redact only ephemeral owned-test token reprs, then gzip the
remaining failure evidence; raw and redacted hashes are recorded separately.

| Original entry / red | Successor and evidence | Status |
| --- | --- | --- |
| POST `/api/jobs/job-templates`, route-owner red | `final_api.legacy_template_create` -> FinalService operation/owner transaction | Passed unchanged retirement route assertion |
| PUT `/api/jobs/job-templates/{id}`, route-owner red | Canonical boundary; original alias advances to an immutable successor | Passed unchanged route assertion |
| DELETE `/api/jobs/job-templates/{id}`, route-owner red | Canonical boundary; resolves current alias and existing retire operation | Passed unchanged route assertion |
| POST `/api/jobs/job-templates/{id}/instantiate`, route-owner red | Canonical boundary; explicit legacy revision -> current fixed TemplateRef | Passed unchanged route assertion |
| Anonymous DELETE without expectedRevision answered 422 | Authenticate before query/body parsing | Passed unchanged auth assertion |
| Aggregate route-table ownership red | Both original and facade copies use canonical handlers | Passed unchanged inventory assertion |
| Facade instantiate expected 410, got 400 | Preserve approved compatibility capability; unbound host returns canonical 503 before payload validation, anonymous caller 401 | Retirement-only Template assertion replaced by named successor assertions; original red retained |
| Published Template update reused/overwrote the published ID | New immutable canonical successor; same old ID/revision alias, one outer receipt/transaction | Additional original-baseline red reproduced; immutable/cold replay and commit rollback pass |
| Old Trigger `triggerAdmission` receipt has original Run/admission IDs | Source hash/revision + exact Event/Trigger/Job/Run/admission match; original runtime and effect records stay in place | Controlled receipt subset implemented |
| Old Trigger history and cancel route | GET `/api/automation` adds `canonicalOriginal`; POST original `/runs/{oldId}/cancel` calls existing original Run cancellation | Real TCP old fields, control CAS, same-key replay, grant boundary pass |
| Active cooperative settled v1 clock | `SourceMigration.adopt_settled_source_graph` -> existing WP04 composition/cursor/activation | Offline host port and cold/no-replay tests pass |
| Old effectful Trigger execution snapshots | No same-plan canonical execution adapter yet | Preserved/blocked; implementation gap remains |
| Active JSON `scheduled-task` occurrences and task-wide lifecycle aliases | Original task/dispatch/count/Job ownership mapping still required | Open; clock adoption does not establish this migration |

## What changed

Template HTTP/facade handlers now belong to `final_api`, retain old parameter
shapes, authenticate first and have no ActionRuntime/Templates fallback. The
legacy alias, canonical mutation and operation receipt join one FinalService
transaction. Updating a published Template creates a distinct immutable
successor; the original published object remains byte-equivalent. Alias replay
returns the original response. Delete and instantiate resolve the current
alias revision/identity. A failed alias save rolls back the canonical object
and all nested receipts.

`TriggerReceiptHandoff` is an explicit offline preview/apply/query adapter.
Eligibility requires a cooperative profile enrolled **before any data exists**.
`enroll_fresh_profile` refuses an existing directory; it cannot attest an
unknown installation. No normal startup or read path enrolls a source.
Source writer exclusion uses existing OwnerFence leases/epoch/nonce; an active
or abandoned legacy lease rejects claim. Apply freezes the source before the
canonical receipt commit. Failure leaves it frozen; the same reviewed intent
can resume. Same-profile adoption joins the actual held canonical owner and
does not replace the original profile activation intent.

This adapter reconciles an original admission receipt only when the exact
canonical Run/admission already exists. It does not manufacture a Run,
Attempt, effect, ActionRef, receipt or successful result. It hashes the entire
existing runtime at preview and rejects drift at apply. Unknown effect/notice
facts stay unknown. Frozen plans, Attempt generations, effect keys, original
outbox and cancellation facts remain in their existing aggregate.

The old cancel route uses an existing handoff binding's control revision and
the current original canonical Run CAS in one transaction. It reuses the same
cancel transition/postcommit resource handling. Its original `job.lifecycle`
grant is retained through a restricted cancel-only service port; it cannot
resume, retry, admit work, or borrow read/execute permission. The public
canonical Run API continues to require its existing execute grant. Accepted
cancellation is not reported as a completed cancellation. Source JSON bytes
remain unchanged; the existing binding metadata carries its compatibility
control revision. Unmapped activated-host requests do not reach the old writer.

The active-clock host port requires a previously cooperative profile and the
actual claimed profile owner. It verifies the full reviewed source snapshot,
uses the existing source history importer and WP04 composition, restores the
original next-fire fact, validates fixed Job ActionRefs/links, installs the
existing authorities and writes the existing operation receipt atomically.
Pending publications remain with the existing same-identity pending-adoption
protocol. No historical occurrence is re-created. This transfers the clock's
future publication authority, not old Job executions.

## Validation and provenance

All commands ran through `scripts/run_tests_isolated.py`; all service ports
were dynamically allocated and asserted different from 8767/8768. Stores,
auth, IPC bridges and subprocesses belonged to synthetic temporary roots.
Source hashes, JUnit statistics and artifact hashes are in the validation JSON.

The final interpreter was
`C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`.
Windows IANA data was supplied read-only with
`PYTHONTZPATH=E:/software/python-userbase/Python312/site-packages/tzdata/zoneinfo`.
No global dependency was installed. `minimal-requirements.txt` already declares
Windows tzdata. Original missing-tzdata failures remain recorded.

| Layer | Observed result |
| --- | --- |
| Final Template/Trigger/clock/Scheduler-retained + retirement + FinalService HTTP combination | **155 passed**, 0 failure/error/skip; `closure-combination-corrected-final.xml` |
| Source migration/canonical migration/owner/FinalService clients | **115 passed**, 0 failure/error/skip; `closure-owner-transport-corrected-final.xml` |
| Latest Trigger source after final parsing/control-revision changes | **12 passed**; `trigger-handoff-final-verified.xml` (overlaps combination; includes two later owner cases) |
| Real HTTP | Owned TCP Template auth/provider boundary; original Trigger history/cancel/replay; existing canonical client TCP tests |
| Real MCP | Existing real core and native standalone stdio client cases pass in the 115-case group; includes canonical CRUD and legacy Template list. No new adoption command is exposed over MCP |
| Python/static | AST parse all changed/new Python files; isolated `python -m packages.jobs.final_contracts --check` passed |
| Frontend/production build/Chromium | Not rerun: no React/dist changes; Python checks are not production-build/browser evidence |
| Remote CI / real data migration / deployment / human acceptance | Not performed |

The broader initial runs were 153/154 and 118/121. Three failures were missing
IANA data; those exact cases passed after the environment correction. The
fourth was `test_runtime_owner_stuck.py::test_concurrent_starts_over_random_port_with_real_owned_processes`
receiving an empty HTTP response. The read-only baseline probe loads every
modified existing product module from **ed55d259** without changing the
checkout: it reproduces all four failures. Thus the remaining Worker HTTP red
is baseline-equivalent in this environment, not verified green. The focused
Candidate Trigger/Template TCP tests do pass. This is not real CLI/Worker/QQ
acceptance. The failure/assertion was not weakened or silently skipped.

The validation JSON links all intermediate harness reds separately. Final
commands were isolated pytest over the exact file sets above; the baseline
replays use `tests/support/p6_closure_baseline_probe.py`. Normal repository
commit hooks are required; no `--no-verify` or push is used.

## Remaining implementation and acceptance gaps

1. Original effectful Trigger Runs without `triggerAdmission` + a complete
   matching Run/admission receipt are still preserved/blocked. Old Runtime
   frozen plans contain legacy Action definition/context snapshots; the
   canonical dispatcher reads fixed ActionRefs/steps/authority. Copying them
   into execution would fail or change plan identity. A shared legacy-plan
   adapter is **not implemented**; this is not a claim of model impossibility
   or acceptance of removed capability. The original effectful family remains
   an open red node.
2. Active JSON `scheduled-task` writer/lease handoff, running/pending dispatch
   identity, max-run counters and original Run/Attempt/effect transfer remain
   open. The implemented v1 Source clock subset cannot stand in for them.
3. Task-wide pause/resume/run-now/delete and lifecycle aliases across old
   HTTP/MCP/GUI remain open. A cursor mutation cannot be replaced by a generic
   Job pause/manual Run. No unsupported guard is presented as parity.
4. This slice repairs Template boundary/alias safety and its seven known
   combination reds. It does not re-certify every legacy Template policy,
   schedule, generation and instantiate return field. The prior simple HTTP
   translator is narrower than the separately reviewed full Group Template
   mapper; those must converge before complete Template parity is claimed.
5. The receipt handoff and settled-clock adoption host ports are not yet exposed
   as a complete HTTP/MCP migration workflow. Existing stdio regressions are
   transport evidence, not proof of full adoption over stdio.
6. The baseline Worker HTTP red, cross-platform/remote CI, real-profile writer
   attestation/migration and manual acceptance remain unverified or blocking
   at their respective layers.

Next implementation should reuse the existing source/occurrence and original
runtime primitives to close item 2, then wire task lifecycle aliases with the
original cursor/count/dispatch semantics. It must also connect item 1's frozen
plan compatibility without re-admitting unknown effects. Main/practical and
real data/profile/service ports remain protected.
