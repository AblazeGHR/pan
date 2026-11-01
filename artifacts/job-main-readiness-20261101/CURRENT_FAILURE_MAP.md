# Complete current original-failure work map

Original 5a7 local+CI inventories contain 263 unique failure/error identities. This document records the latest exact 96bb rerun of the 146 previously unresolved nodes: 124 failed, 13 setup errors, one existing skip, eight passed. Source drift was empty. Original JUnits/sources are immutable evidence. Passing focused successors below remain distinct from a final full-suite gate.

Model source: docs/design/job-unification/JOB_FINAL_MODEL_20261025.md sections 2.5, 3 feature matrix M01-M14, 7 D4/D6. The model target is not blanket retirement authorization. Each unresolved item below remains open until its own public behavior and safety assertions are mapped.

## tests.test_background_job_api

- Original capability and approved model target: M01/M02/M04/M13; E01 canonical public API, old writes retire after auth
- Current production counterpart: /api/jobs, /api/job-runs/{runId}/operations; product process/session Actions
- Current disposition: Current 27-node HTTP file including two fixed-name-path no-echo negatives is green within public-api-successors-combined.xml (66 pass); final exact-SHA combination/CI remains required

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_http_start_reports_missing_session` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_start_invalid_argv_and_cwd` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_start_forwards_creator_and_target_separately` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_retry_rejects_running_job` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'retry' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_message_job_creation_preserves_source_and_text` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start_message' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_scheduled_broadcast_checks_each_target_and_uses_broadcast_model` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start_broadcast' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_process_name_required_before_spawn[payload0]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_process_name_required_before_spawn[payload1]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_process_name_required_before_spawn[payload2]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_http_process_name_required_before_spawn[payload3]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_background_jobs

- Original capability and approved model target: M01/M13; E01/E15 preserve process/notice/control capability
- Current production counterpart: FinalService Run/Attempt/effect + native /executions + canonical outbox
- Current disposition: 23 original cases still need per-node canonical assertions; do not restore start/retry/second Job engine

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_accepted_cancel_wins_runner_exit_race` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_finishes_accepted_cancel_if_canceller_disappears` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_cancel_survives_runner_disappearing_during_tree_lookup` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_start_persists_metadata_and_detaches` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_rejects_invalid_command_and_cwd[argv0-x]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_rejects_invalid_command_and_cwd[argv1-]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_rejects_invalid_command_and_cwd[argv2-missing]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_external_cwd_runs_and_projects_one_completion_notice` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_terminal_notification_is_idempotent` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint recover_notifications retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_retry_rejects_running_job` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint retry retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_retry_inherits_creator_and_target` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_terminal_broadcasts_job_updated_once` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_terminal_broadcast_retries_after_channel_failure` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_terminal_broadcast_skips_scheduled_children` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_independent_terminal_broadcast_uses_local_ledger_and_retries` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint recover_notifications retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_hidden_runner_claim_is_once_and_refuses_unknown` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_hidden_runner_claim_finishes_already_accepted_cancel` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_agent_message_job_registry_commits_publish_full_lifecycle[single]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_agent_message_job_registry_commits_publish_full_lifecycle[broadcast]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_broadcast retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_process_creation_requires_explicit_bounded_name[None]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_process_creation_requires_explicit_bounded_name[]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_process_creation_requires_explicit_bounded_name[   ]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_process_creation_requires_explicit_bounded_name[name3]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_independent_job_bridge

- Original capability and approved model target: M01/M13; E15 native execution-only port
- Current production counterpart: ExecutionHost/IndependentExecutor and original receipt query
- Current disposition: Two old remote Job-lifecycle facade nodes require per-node replacement, not a second remote Job store

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_core_routes_only_new_generic_jobs_and_never_reconciles_remote` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_unified_gui_reads_remote_and_refuses_local_metadata_mutation` | AttributeError: 'NoneType' object has no attribute 'app' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_action_templates

- Original capability and approved model target: M14 + source scheduling D4; E05 complete Job Template
- Current production counterpart: Templates snapshot read; canonical CreatorRuntime time event
- Current disposition: Snapshot IO node now strict retained service-registry IO test; shared misfire node unresolved

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_snapshot_io_does_not_hold_job_registry_or_block_event_loop` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_shared_scheduler_entry_and_misfire_do_not_replay_effects` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint run_due_scheduled_tasks retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'register_scheduled_tasks'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_completion_recovery

- Original capability and approved model target: M04; unknown/partial never resend originally attempted children
- Current production counterpart: TypedExecutor / SessionDelivery child facts under frozen effect identity
- Current disposition: Old progress checkpoint call removed; known/unknown/new child continuation proof still unresolved

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_system_delivery_confirmation_does_not_resend_receipts` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint execute retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_delete_http_owned

- Original capability and approved model target: M13; owner/resource fence, retire before purge
- Current production counterpart: Canonical process cleanup + original owner-fenced retire API
- Current disposition: Two real HTTP tree cleanup/deletion cases still unresolved; preserved-data fixture delete does not replace them

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_real_owned_tree_delete_http[False]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_real_owned_tree_delete_http[True]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_hidden_production

- Original capability and approved model target: M01/E15; single native execution port; no unknown replay
- Current production counterpart: ExecutionHost /executions with retained original Attempt/effect/supervisor birth
- Current disposition: 13 original setup errors split into actual removed create/start calls; all 13 original identity successors now pass through actual canonical native port (hidden-production-canonical-port.xml); real HTTP deletion remains separate

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_legacy_scheduled_shell_uses_real_runner` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_three_generation_console_and_gui[new-independent]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_three_generation_console_and_gui[new-legacy]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_three_generation_console_and_gui[alloc-independent]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_three_generation_console_and_gui[alloc-legacy]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_stdout_exit_and_bidirectional_file_ipc[independent]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_stdout_exit_and_bidirectional_file_ipc[legacy]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_shutdown_recovery_then_cancel[shutdown-independent]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_shutdown_recovery_then_cancel[shutdown-legacy]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_shutdown_recovery_then_cancel[deadline-independent]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_production_shutdown_recovery_then_cancel[deadline-legacy]` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_crash_recovery_and_cleanup` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_independent_runner_crash_recovery_and_cleanup` | failed on setup with "AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'run_due_scheduled_tasks' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_lifecycle_prevention

- Original capability and approved model target: M01/E15; kernel owner creation edge and cleanup proof
- Current production counterpart: Native execution host and platform process port
- Current disposition: 11 legacy-path cases retain original creation edge/venv/controller/PID-publication requirements; native successor mapping still unresolved

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_runner_death_at_creation_edge_converges_without_survivors[base-pre-import-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_death_at_creation_edge_converges_without_survivors[base-post-create-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_death_at_creation_edge_converges_without_survivors[venv-outer-pre-import-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_death_at_creation_edge_converges_without_survivors[venv-outer-post-create-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_death_at_creation_edge_converges_without_survivors[venv-inner-pre-import-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runner_death_at_creation_edge_converges_without_survivors[venv-inner-post-create-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_multigeneration_tree_cleanup_on_exit_or_exception[root-exit-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_multigeneration_tree_cleanup_on_exit_or_exception[wait-error-legacy]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'stat'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_controller_process_crash_keeps_owned_runner_then_restart_cancels[legacy]` | assert False  +  where False = until(<function test_controller_process_crash_keeps_owned_runner_then_restart_cancels.<locals>.<lambda> at 0x00000207C188B3D0>) | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication[base]` | assert False  +  where False = until(<function test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication.<locals>.<lambda> at 0x00000207C188BA00>) | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication[venv]` | assert False  +  where False = until(<function test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication.<locals>.<lambda> at 0x00000207C188BED0>) | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_runner_venv

- Original capability and approved model target: M01/E15; no window before task imports
- Current production counterpart: Typed process /executions, venv redirector and original native startup probe
- Current disposition: Four entry labels need same original pre-import/no-orphan assertions through canonical admission; no legacy create/JobStore writer

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_venv_runner_has_no_window_before_task_imports[mcp-True-0]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_venv_runner_has_no_window_before_task_imports[mcp-False-7]` | AttributeError: module 'packages.web.server' has no attribute 'api_background_job_start'. Did you mean: 'api_background_job_get'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_venv_runner_has_no_window_before_task_imports[independent-True-0]` | AttributeError: <module 'packages.jobs.independent_service' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\jobs\\independent_service.py'> has no attribute 'sys' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_venv_runner_has_no_window_before_task_imports[scheduled-True-0]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_scheduled_process retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_job_window_policy

- Original capability and approved model target: M01/E15; explicit Windows visibility policy
- Current production counterpart: platform process ownership + typed process Action
- Current disposition: Nine legacy dispatcher paths need per-node canonical argv/shell/window-policy mapping; no blanket unsupported

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[None-False]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[None-True]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[True-False]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[True-True]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[False-False]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_legacy_runner_uses_policy_for_argv_shell_and_old_records[False-True]` | AssertionError: assert 1 == 0  +  where 1 = <function main at 0x00000207C1AD5A60>()  +    where <function main at 0x00000207C1AD5A60> = <module 'packages.core.background_runner' from 'D:\\project\\pan-worktrees\\job-main | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_mcp_and_http_forward_explicit_visible_option` | AttributeError: <module 'packages.core.background_jobs' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\core\\background_jobs.py'> has no attribute 'start' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_independent_service_persists_and_retries_visible_policy` | ImportError: import error in packages.jobs.independent_service.subprocess: No module named 'packages.jobs.independent_service.subprocess'; 'packages.jobs.independent_service' is not a package | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_independent_old_payload_idempotency_does_not_break` | ImportError: import error in packages.jobs.independent_service.subprocess: No module named 'packages.jobs.independent_service.subprocess'; 'packages.jobs.independent_service' is not a package | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_jobs_api

- Original capability and approved model target: M01-M14; E04 canonical object/Run/composition ownership
- Current production counterpart: canonical /api/jobs, /api/job-runs, event-creators, triggers, job-templates
- Current disposition: 30 original nodes: two metadata/CAS/name-control successors now focused green; exact remaining nodes below remain unresolved

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_structured_identity_new_records` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_rename_and_description` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_blank_name_rejected` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_target_switch_updates_flat_and_struct` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_enable_disable_stops_entries` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_action_template_default_assign` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint run_due_scheduled_tasks retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'register_scheduled_tasks'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_action_template_send_session` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint run_due_scheduled_tasks retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'register_scheduled_tasks'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_action_template_unknown_falls_back_to_assign` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_resume_legal_running_empty_scan_is_successful_noop` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_resume_legal_running_keeps_per_fire_client_message_id_stable` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint _execute_job_action retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_runs_and_next_and_run_now` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_partial_failed_event_emitted` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_broadcast retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_post_creates_scheduled_task_multi_entry` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_post_creates_scheduled_shell_action_without_target_or_runner` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_scheduled_qq_action_is_strict_and_editable` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_qq_backlog_retry_runs_without_session_target` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_post_accepts_session_message_and_rejects_empty_schedule` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_post_requires_existing_session` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_schedule_replaces_entries` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_schedule_rejects_bad_spec` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_target_clear_enters_undeliverable` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_patch_text_updates_dispatch_body` | KeyError: 'ok' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_run_records_carry_entry_id` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint run_due_scheduled_tasks retired; use canonical FinalService/FinalRuntime/CreatorRuntime. Did you mean: 'register_scheduled_tasks'? | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_pin_failure_keeps_disk_and_emits_nothing` | KeyError: 'error' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_legacy_mcp_compat

- Original capability and approved model target: E01 old writes auth-before410; M01 same canonical Job/Run; unknown no replay
- Current production counterpart: Canonical HTTP plus native receipt lookup; no child Job identity
- Current disposition: Three original reds now actual HTTP/native/cold unknown successors; original source retained; 66-node affected combination green, final exact-SHA full CI outstanding

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_trusted_legacy_http_entry_is_retired_and_creates_no_job` | AttributeError: <module 'packages.jobs.system_actions' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\jobs\\system_actions.py'> has no attribute 'execute' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_background_mcp_shape_uses_system_action_and_same_job_id` | AttributeError: <module 'packages.jobs.system_actions' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\jobs\\system_actions.py'> has no attribute 'execute' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_unknown_system_action_is_not_replayed_by_new_runtime` | AttributeError: <module 'packages.jobs.system_actions' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\jobs\\system_actions.py'> has no attribute 'execute' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_plugin_provider_window

- Original capability and approved model target: platform existing hidden API provider behavior; no task retirement inferred
- Current production counterpart: ApiProvider real pipe child and owned Windows desktop
- Current disposition: Five nodes passed subsequent full-file recheck without code change; preceding grouped red/root cause retained unresolved until repeat grouped evidence

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_owned_api_provider_console_lifecycle[legacy-exe]` | AssertionError: Traceback (most recent call last):     File "D:\project\pan-worktrees\job-main-readiness-20261101\tests\fixtures\provider_console_host.py", line 74, in <module>       asyncio.run(main())       ~~~~~~~~~~~ | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_api_provider_console_lifecycle[hidden-exe]` | AssertionError: Traceback (most recent call last):     File "D:\project\pan-worktrees\job-main-readiness-20261101\tests\fixtures\provider_console_probe.py", line 50, in <module>       'window_class': name.value, 'desktop | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_api_provider_console_lifecycle[hidden-cmd]` | AssertionError: Traceback (most recent call last):     File "D:\project\pan-worktrees\job-main-readiness-20261101\tests\fixtures\provider_console_probe.py", line 50, in <module>       'window_class': name.value, 'desktop | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_api_provider_console_lifecycle[hidden-powershell]` | AssertionError: Traceback (most recent call last):     File "D:\project\pan-worktrees\job-main-readiness-20261101\tests\fixtures\provider_console_probe.py", line 50, in <module>       'window_class': name.value, 'desktop | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_owned_api_provider_console_lifecycle[shown-exe]` | AssertionError: Traceback (most recent call last):     File "D:\project\pan-worktrees\job-main-readiness-20261101\tests\fixtures\provider_console_host.py", line 74, in <module>       asyncio.run(main())       ~~~~~~~~~~~ | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_plugin_qq_integration

- Original capability and approved model target: M03/M04 QQ scheduled Action and independent report-only outbox
- Current production counterpart: product_channel + original qq_reports outbox
- Current disposition: One old _run_job_action call unresolved; actual real channel/selected-bot successors already green but report-outbox independence still needs this node

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_qq_job_and_outbox_continue_when_session_tools_are_disabled` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint _run_job_action retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_plugin_session_config

- Original capability and approved model target: M04 session metadata/tool-disable independence
- Current production counterpart: canonical product Session Action + trusted worker queue
- Current disposition: One old _run_job_action call unresolved; preserve tools-disabled behavior and exact original source/text

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_legacy_scheduled_qq_action_is_unchanged_by_plugin_catalog` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint _run_job_action retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_scheduler_dependency_cutover

- Original capability and approved model target: S05/S08 no legacy scheduler dependency; D4 original source semantics
- Current production counterpart: canonical CreatorRuntime / no old host imports
- Current disposition: One exact startup subprocess red unresolved; no removal of original import/pin/one-event/cold-owner epoch assertions

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_all_production_consumers_have_no_static_legacy_host_imports` | assert [{'path': 'pa...("pathlib")'}] == []      Left contains one more item: {'path': 'packages/scheduler/pending_adoption.py', 'line': 107, 'literal': 'pathlib', 'code': '__import__("pathlib")'}      Full diff: (-: mis | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_scheduler_legacy_bridge

- Original capability and approved model target: D6 simple offline conversion vs complex preserved; no online legacy writer
- Current production counterpart: explicit offline migration + canonical references and one owner
- Current disposition: One online switch test unresolved; synthetic interval case requires exact ID/alias/cursor proof, not generic unsupported

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_run_now_does_not_move_periodic_next_fire` | AttributeError: <module 'packages.jobs.system_actions' from 'D:\\project\\pan-worktrees\\job-main-readiness-20261101\\packages\\jobs\\system_actions.py'> has no attribute 'execute' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_scheduler_management_browser

- Original capability and approved model target: M13/M14 + D4; E04 React canonical forms
- Current production counterpart: CanonicalJobsView/EventCreator form and real authenticated browser
- Current disposition: One original management browser flow needs exact operation/assertion mapping; build alone is not proof

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_authenticated_native_source_shared_links_conflict_and_retire_in_production_browser` | KeyError: 'result' | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_scheduler_structured_browser

- Original capability and approved model target: D4 time precision/zone/DST/future-event activation
- Current production counterpart: current EventCreator native form and canonical template
- Current disposition: Two original browser flows unresolved; retain exact timezone/weekly-second/offset/advanced behavior

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_structured_scheduler_job_trigger_forms_preserve_precision_shared_cas_and_unknown[False]` | httpx.HTTPStatusError: Client error '400 Bad Request' for url 'http://127.0.0.1:59541/api/jobs' For more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/400 | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_structured_scheduler_job_trigger_forms_preserve_precision_shared_cas_and_unknown[True]` | AssertionError: Locator expected to be visible Actual value: None Error: element(s) not found  Call log:   - Expect "to_be_visible" get_by_role("article", name="Jobs Form Job", exact=True) with timeout 5000ms   - waiting | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_scheduler_time_revision2_browser

- Original capability and approved model target: D4 time composition input semantics
- Current production counterpart: current time input form and complete passive Job Template
- Current disposition: One original checkbox/host override flow unresolved; current missing locator is not retirement authorization

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_native_time_inputs_persist_host_override_once_and_passive_template` | playwright._impl._errors.TimeoutError: Locator.is_checked: Timeout 30000ms exceeded. Call log:   - waiting for get_by_label("Activate future time events", exact=True) | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

## tests.test_session_message_jobs

- Original capability and approved model target: M02/M04 + D4 scheduled messages/broadcast; E01 old message kind removal
- Current production counterpart: Job typed Session Action + Creator + Trigger + original outbox
- Current disposition: 14 original nodes require exact once/edit/cancel/name/no-target/unknown/dedup semantics; only scoped public source/text/time fanout successors exist so far

| Original failure identity | Actual current failure | Remaining proof |
|---|---|---|
| `test_once_delay_persists_and_dispatches_via_send_semantics` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_absolute_once_and_schedule_edit_are_persisted` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_invalid_time_schedule_is_rejected[schedule0]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_invalid_time_schedule_is_rejected[schedule1]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_invalid_time_schedule_is_rejected[schedule2]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_invalid_time_schedule_is_rejected[schedule3]` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_interval_repeats_after_restart_recovery_and_cancel_stops_future_runs` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_weekly_schedule_accepts_weekday_and_local_time` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_stale_running_job_is_requeued_after_service_restart` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_message_job_never_enters_process_runner_retry_path` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_broadcast_deduplicates_and_isolates_target_failures` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_broadcast retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_broadcast_interval_recovery_and_no_burst` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_broadcast retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_scheduled_broadcast_edit_and_cancel_prevent_future_send` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_broadcast retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |
| `test_message_job_default_name_min_vacant` | packages.jobs.retired.RetiredEntryPoint: Legacy entrypoint start_message retired; use canonical FinalService/FinalRuntime/CreatorRuntime | Original business, original identity/no-replay and owner fence assertions retained; see group-specific disposition |

Latest focused update: 152 of the original 263 identities now have intermediate exact passing observations in full-terminal-5a7/successor-observations.json. This is not a final full run. 111 original identities remain without a mapped passing successor; composition-sensitive provider reds are preserved separately from their five-node passing recheck.
