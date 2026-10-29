# JOB-RETIRE revision14 — passive Template production wiring

Complete Job-only Templates now use the existing instantiate operation without a Creator or Trigger. The active React view supplies simple assign and shell fields through the existing Template POST convenience boundary. Assign and an unquoted shell command have real owned production-bundle/HTTP/effect evidence. **Windows quoted shell commands remain a demonstrated product gap; this candidate is not full shell-parity acceptance.**

Baseline: `aad8dd82eaa137a5e456a3c49696dd7387545249`. Tested implementation: `cb950d53201be21477314d351d99a597daa7504b`. Branch: `r6/retirement-parity-acceptance-20261029-a`; worktree: `D:/project/pan-worktrees/job-retirement-closure-20261027`. The following evidence-only commit does not change these tested sources. All r6–r13 evidence and original red artifacts are preserved.

## Design correction and exact source boundary

`JOB_FINAL_MODEL_20261025.md` Template row 113 defines a complete passive configuration, and M01 permits direct Job or full Template creation. The previous empty-Creator rejection was an implementation limitation, not D1. D1 still requires a real Scheduler Event Creator for scheduling. The v2 migration plan explicitly calls for precomposed forms and reuse of existing worker/platform operations.

Changed product methods and callers:

- `FinalService.instantiate_template`: the former time instantiate transaction with the mandatory Creator check removed; no-Creator plus nonempty Trigger composition remains unsupported. It reuses the original operation/requestKey, CAS, references, permission checks, ownership and `CompositionCoordinator.instantiate_template`. `instantiate_time_template` forwards for compatibility. Existing time-provider restrictions are retained. CompositionCoordinator required **no modification**.
- `FinalService._normalize_template_form` and its narrow `write` consumer: only Template create accepts `{definition, form, expectedRevision, requestKey}`. Only a single resolved immutable system `product_assign_v2`/assign or `product_process_logs_v1`/process step with literal arguments and empty composition is accepted. It invokes the r11 normalizer, current source-authority policy and normal reference/business validation. Form data is not persisted as a document field. Normalization occurs inside the first operation transaction; same-key replay returns its existing receipt rather than reinterpreting fields against changed state.
- `normalize_legacy_form_fields`: partial explicit canonical arguments merge with normalized fields after conflict checks, instead of discarding the other normalized required fields. Command selection now delegates to `packages/core/platform/worker_runtime.py::shell_command_argv`. Existing host policy moved there unchanged; browser OS is never used.
- `final_api.definition_routes.create`: accepts this exact convenience envelope on existing `POST /api/job-templates`; other writes retain their existing contract. Existing `/api/job-templates/{id}/instantiate` forwards to the generic method. No new endpoint, schema, publication or ledger.
- React `SimpleTemplateForm`, `CanonicalCollection` and `canonicalJobs.writeFormTemplate`: exact catalog-issued immutable product refs/default completion; name/session/text or command/cwd/hideWindow fields; raw multiline strings; no source-spoof controls, placeholder Session or configuration hole. Save creates a draft Template, Publish fixes its revision, Instantiate defaults to a disabled Job and never creates a Run. Missing/disabled product publication blocks the form. Existing operation lookup handles unknown creation without an alternate dispatcher.

No Worker, process provider/creation implementation, MCP, contracts, publications, scheduler calculator, recovery, transport or shared fixtures were changed. The small platform helper relocates existing policy only; it does not repair the Windows execution defect below.

## Exact successors and retained limitations

Dedicated backend file: `tests/test_retirement_rev14_templates.py`; dedicated frontend file: `packages/web/src/views/CanonicalJobsView.retirement-r14.test.tsx`. Every exact test and parameter ID is retained in `product.xml`, `runtime.xml` and `react.xml`. `parity-matrix.json` gives versioned capability/node dispositions; historical `successors.json` and revision6 matrix are not rewritten.

| Prior boundary / original node | Current exact successor | Proven scope / remaining gap |
|---|---|---|
| r11/r13 no-Creator unsupported; M01/M05 | `test_zero_creator_complete_template_default_disabled_same_key_cold_reopen` | Real canonical HTTP, Job-only composition, no Creator/Trigger/Run/effect/Worker; same-key operation identity, conflicting payload/CAS rejection and cold replay |
| `test_jobs_api.py::test_action_template_default_assign`; M05 | `test_production_form_assign_queue_source_frozen_identity_cold_no_duplicate` + `test_fresh_production_browser_real_template_http_queue_and_shell` | Simple preconfigured assign entry reaches actual `worker.assign` durable paused queue; raw text/source/task identity, edit-after-admission frozen plan, original cold receipt, no duplicate/no spawn. Does not restore arbitrary unknown-action fallback or the obsolete scheduled dispatch API |
| `test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner`; M01/M07 | `test_fresh_production_browser_real_template_http_queue_and_shell`; `test_production_shell_form_real_process_exit_and_original_lookup` (red) | Passive shell form persists command/cwd through fixed process ref without target or early Run; actual simple shell exits successfully once. Scheduled regressions run separately. General command-string compatibility is **partial**, because the quoted executable case fails; do not treat an argv-only successor or refused-shell test as complete preservation |
| D5/reference/no-write | `test_form_rejection_no_definition_operation_or_composition[...]`, `test_session_deleted_after_publish_blocks_instantiate_without_writes`, `test_real_grants_passive_activation_revoke_and_source_authority` | Missing Session, stale/custom ref, unknown/conflicting/dynamic fields, requestKey failure; no document/operation/composition writes. Actual delegated grants, explicit activation grants and revocation; forged source rejected. Activation itself creates no Run |
| Atomicity | `test_passive_instantiate_failure_rolls_back_and_recovers_same_key` | Owned checkpoint failure rolls back all three tables; same-key retry succeeds. This fault injection is transaction evidence, not provider delivery evidence |
| Existing scheduled composition | `test_scheduler_time_templates.py` and final Creator regressions | Existing real Creator/time composition path remains separately exercised; a passive Template is not a synthetic Scheduler |

The r13 frontend test `no-Creator Template exposes structured unsupported...` became `complete no-Creator Template instantiates with activation false without a fallback or Run request`. Its negative assumption is superseded by the documented capability and real baseline red reproduction, not relaxed to accept both outcomes. Existing request count/revision/activation/no-Run checks remain. Original r13 logs/XML/report remain immutable.

`baseline-zero-creator-red-command.json` binds the baseline service/API files to `aad8dd82...`; the new positive test against that actual baseline failed once (exit 1), receiving `422 capability_unsupported` instead of `201`. Current files were restored byte-for-byte after the baseline run. This is a real service boundary reproduction, not a fabricated response. Development experiments are preserved separately and are not added to final counts.

## Production browser scope

`retirement-r14-production.mjs` builds a fresh Vite production bundle of the actual CanonicalJobsView and uses Chromium against an owned uvicorn/FinalService HTTP server, random port, synthetic Sessions/data, without intercepted API replies. Browser actions create, publish and instantiate two Templates in six writes. Both instances have no Creator/Trigger, are disabled and have no Runs/effects. The test then uses existing canonical API controls to enable/admit them and the existing dispatcher to execute: one real assign queue row and one real simple shell file line, exactly one canonical effect each, original-identity cold lookup and no duplicate after reopen. This does **not** claim that the browser clicked Run or exercised the entire deployed application shell.

Final browser details and sanitized operation/Job identities are in the `R14_BROWSER=` line of `product.log`. Browser, owned HTTP thread/socket and cold services close in `finally`; process-host cleanup remains the original fixture/provider responsibility. No real profile/account or existing service is used.

## Immutable verification

Run from this worktree with existing dependencies:

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-rev14-20261029/run_matrix.py
```

The runner verifies tracked sources clean, records exact HEAD and interpreter versions, serially invokes every check through `scripts/run_tests_isolated.py`, saves raw outputs/JUnit and preserves actual nonzero exits. It does not replace failures with a fixed gate result. Exact argv, counts, exits and durations are in `commands.json`; final counts are recorded below after completion.

| Check | Actual result | Exit | Seconds |
|---|---|---|---|
| product | 85 passed / 1 failed or error / 0 skipped (86 total) | 1 | 69.416 |
| runtime | 222 passed / 0 failed or error / 1 skipped (223 total) | 0 | 150.428 |
| react | 63 passed / 0 failed or error / 0 skipped (63 total) | 0 | 4.64 |
| build | TypeScript + production build; see build.log | 0 | 22.724 |

Dedicated backend subset: 14 tests, included in product counts; browser scenario is one test, not an extra added count. Overall runner exit is 1. No skip/xfail added. Python Python 3.14.5; Node v24.22.1; pnpm 9.7.0; Chromium 153.0.8010.12. No dependency installation.

## True remaining gap and minimum next scope

The unchanged red `test_production_shell_form_real_process_exit_and_original_lookup` supplies a quoted absolute designated Python executable and quoted `-c` program through the real form/Template/process path. `argv[-1]` retains the command bytes, but the actual Windows process exits **1**, Run fails, and the marker is absent. The canonical receipt/log and confirmed owned cleanup are retained in `product.log`/XML.

Read-only trace: `shell_command_argv` → process Action/owned host → `packages/core/platform/process_creation_win.py::OwnedPopen._execute_child` line 137 → `subprocess.list2cmdline(values['args'])` → `create_owned_process`. CRT argv escaping serializes command-language quotes as backslash quotes; cmd reports the quoted executable is not recognized. This isolates a Windows shell-command serialization conflict rather than a Template persistence/CAS issue. Fixing it needs precise MA coordination for `OwnedPopen._execute_child`'s CreateProcess command-line serialization and its platform-boundary tests, or an explicitly approved alternative process contract. Neither is edited here. Preserve argv-only process behavior, identity and cleanup while reviewing that scope; do not globally enable `shell=True` or invent another provider.

M01/M05 have bounded passive/default assign entry proof; M07 remains partial. Unknown generic fallback/clear-target, monitoring/stable-reset, unrelated UI flows, real migration and deployment remain open. No original 43/remaining-nine aggregate is declared newly closed. POSIX shell behavior, remote CI, real user data migration and deployment are unverified in this batch. Source/unit/build, owned HTTP/browser/effects and real production delivery remain separate claims. MA review/integration is required; a clean Git candidate does not mean the retained quoted-command red is accepted.

The runtime matrix includes one pre-existing Windows skip: `test_job_process_logs.py::test_large_unicode_log_leader_loss_remains_unknown_and_original_queryable`, reason `POSIX lost-leader process log/owned subtree boundary`. It is not counted as a preserved capability. No skip was added. Actual stage durations were 69.416 + 150.428 + 4.640 + 22.724 seconds; the initial under-three-minute estimate was exceeded because the runtime group took 150 seconds. No repeated watcher or duplicate Job was started.

Process-log regressions generated synthetic diagnostics through existing `tests/job_posix_diagnostics.py` at `evidence/cp-x1-ci/cp-x2-proof`; these are retained in their original location and inventoried by SHA256 in `cleanup.json`. They were absent before this run and are evidence from this batch, not another worktree's input.

Automatic approval review rejected both the combined cleanup command and the separate `Remove-Item` of the owned `packages/web/node_modules` junction, reporting `blocked by policy` without a more specific reason. Neither command executed. The dependency junction therefore remains and still points at the existing Pan-main dependency directory; no deletion workaround was attempted. Its target was not modified. This ignored local link is an explicit resource residual, not a tracked source change. Final owned port/listener and resource observations are recorded in `cleanup.json`. No remote CI, main/practical movement, push, deployment, existing service operation or real migration occurred.
