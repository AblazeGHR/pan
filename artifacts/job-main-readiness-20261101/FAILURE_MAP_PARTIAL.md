# Frozen 5a7 first partial failure map

Original Job job_f3f196f78be77db64106ef05. Incomplete:116 failures plus reporter INTERNALERROR. The following mappings are review candidates, not blanket test retirement authorization. Original sources/JUnit preserved in full-first-red/.

## tests.test_authorization_runtime

- Automatic recovery revocation retains budget/no new attempt
- S07 old system_actions.execute removed; function requirement retained
- tests/test_job_automatic_recovery.py canonical authority revocation and executor gate; old monkeypatch must move to current typed provider; unresolved node-level equivalence

Nodes:

- `test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt`

## tests.test_auto_force_http_e2e

- Job terminal notice auto-force Steer, no restart
- E01/E07 old creation routes/tools retired; business behavior retained
- Real UserAuth + product_process_logs_v1 + canonical Run/session outbox; successor in progress

Nodes:

- `test_real_job_terminal_auto_force_steers_system_notice`

## tests.test_background_job_api

- Create/retry/message/broadcast validation and routing
- E01/S01 retire old handler + payload; no auth bypass
- Canonical /api/jobs /runs /operations + UserAuth; exact validation/targeting matrix still to map

Nodes:

- `test_http_start_reports_missing_session`
- `test_http_start_invalid_argv_and_cwd`
- `test_http_start_forwards_creator_and_target_separately`
- `test_http_retry_rejects_running_job`
- `test_http_message_job_creation_preserves_source_and_text`
- `test_http_scheduled_broadcast_checks_each_target_and_uses_broadcast_model`
- `test_http_process_name_required_before_spawn[payload0]`
- `test_http_process_name_required_before_spawn[payload1]`
- `test_http_process_name_required_before_spawn[payload2]`
- `test_http_process_name_required_before_spawn[payload3]`

## tests.test_background_job_retry_if

- Reason match36/budget/backoff/cancel/recovery/IO policy
- S01 legacy retry writer removed; final model section2.5 recovery side effects unknown must block
- tests/test_job_automatic_recovery.py; reason subset capability refusal is NOT automatically parity, each retained capability needs check

Nodes:

- `test_reason_match_matrix[cancelled-cancelled]`
- `test_reason_match_matrix[cancelled-dependency-unavailable]`
- `test_reason_match_matrix[cancelled-failed]`
- `test_reason_match_matrix[cancelled-interrupted]`
- `test_reason_match_matrix[cancelled-orphaned]`
- `test_reason_match_matrix[cancelled-timeout]`
- `test_reason_match_matrix[dependency-unavailable-cancelled]`
- `test_reason_match_matrix[dependency-unavailable-dependency-unavailable]`
- `test_reason_match_matrix[dependency-unavailable-failed]`
- `test_reason_match_matrix[dependency-unavailable-interrupted]`
- `test_reason_match_matrix[dependency-unavailable-orphaned]`
- `test_reason_match_matrix[dependency-unavailable-timeout]`
- `test_reason_match_matrix[failed-cancelled]`
- `test_reason_match_matrix[failed-dependency-unavailable]`
- `test_reason_match_matrix[failed-failed]`
- `test_reason_match_matrix[failed-interrupted]`
- `test_reason_match_matrix[failed-orphaned]`
- `test_reason_match_matrix[failed-timeout]`
- `test_reason_match_matrix[interrupted-cancelled]`
- `test_reason_match_matrix[interrupted-dependency-unavailable]`
- `test_reason_match_matrix[interrupted-failed]`
- `test_reason_match_matrix[interrupted-interrupted]`
- `test_reason_match_matrix[interrupted-orphaned]`
- `test_reason_match_matrix[interrupted-timeout]`
- `test_reason_match_matrix[orphaned-cancelled]`
- `test_reason_match_matrix[orphaned-dependency-unavailable]`
- `test_reason_match_matrix[orphaned-failed]`
- `test_reason_match_matrix[orphaned-interrupted]`
- `test_reason_match_matrix[orphaned-orphaned]`
- `test_reason_match_matrix[orphaned-timeout]`
- `test_reason_match_matrix[timeout-cancelled]`
- `test_reason_match_matrix[timeout-dependency-unavailable]`
- `test_reason_match_matrix[timeout-failed]`
- `test_reason_match_matrix[timeout-interrupted]`
- `test_reason_match_matrix[timeout-orphaned]`
- `test_reason_match_matrix[timeout-timeout]`
- `test_default_side_effects_never_retry`
- `test_zero_budget_and_spawn_failure_consume_no_extra_attempt`
- `test_retry_does_not_swallow_registry_failure`
- `test_invalid_policy_has_no_effect[options0]`
- `test_invalid_policy_has_no_effect[options1]`
- `test_invalid_policy_has_no_effect[options2]`
- `test_invalid_policy_has_no_effect[options3]`
- `test_invalid_policy_has_no_effect[options4]`
- `test_invalid_policy_has_no_effect[options5]`
- `test_invalid_policy_has_no_effect[options6]`
- `test_invalid_policy_has_no_effect[options7]`
- `test_invalid_policy_has_no_effect[options8]`
- `test_invalid_policy_has_no_effect[options9]`
- `test_invalid_policy_has_no_effect[options10]`
- `test_invalid_policy_has_no_effect[options11]`
- `test_restart_reconcile_persists_count_backoff_and_limit`
- `test_cancel_during_backoff_prevents_retry`
- `test_accepted_cancel_beats_orphan_recovery`
- `test_concurrent_recovery_and_late_runner_are_fenced`
- `test_orphan_does_not_duplicate_live_task`
- `test_http_policy_and_public_status`
- `test_runner_does_not_collapse_terminal_reasons[error0-timeout]`
- `test_runner_does_not_collapse_terminal_reasons[error1-timeout]`
- `test_runner_does_not_collapse_terminal_reasons[error2-interrupted]`
- `test_runner_does_not_collapse_terminal_reasons[error3-failed]`
- `test_unavailable_independent_policy_fails_before_remote_start`
- `test_recovery_notifies_final_outcome_once`
- `test_separate_recovery_process_after_runner_loss[orphaned]`
- `test_separate_recovery_process_after_runner_loss[failed]`

## tests.test_background_jobs

- Process lifetime/log/cancel/durable notices/ownership
- S01 old start removed; E15 executor lifetime retained
- Canonical process/execution-host/log/notice suites; exact per-node successor mapping pending

Nodes:

- `test_accepted_cancel_wins_runner_exit_race`
- `test_runner_finishes_accepted_cancel_if_canceller_disappears`
- `test_cancel_survives_runner_disappearing_during_tree_lookup`
- `test_start_persists_metadata_and_detaches`
- `test_rejects_invalid_command_and_cwd[argv0-x]`
- `test_rejects_invalid_command_and_cwd[argv1-]`
- `test_rejects_invalid_command_and_cwd[argv2-missing]`
- `test_external_cwd_runs_and_projects_one_completion_notice`
- `test_terminal_notification_is_idempotent`
- `test_retry_rejects_running_job`
- `test_retry_inherits_creator_and_target`
- `test_runner_terminal_broadcasts_job_updated_once`
- `test_terminal_broadcast_retries_after_channel_failure`
- `test_terminal_broadcast_skips_scheduled_children`
- `test_independent_terminal_broadcast_uses_local_ledger_and_retries`
- `test_hidden_runner_claim_is_once_and_refuses_unknown`
- `test_hidden_runner_claim_finishes_already_accepted_cancel`
- `test_agent_message_job_registry_commits_publish_full_lifecycle[single]`
- `test_agent_message_job_registry_commits_publish_full_lifecycle[broadcast]`
- `test_process_creation_requires_explicit_bounded_name[None]`
- `test_process_creation_requires_explicit_bounded_name[]`
- `test_process_creation_requires_explicit_bounded_name[   ]`
- `test_process_creation_requires_explicit_bounded_name[name3]`

## tests.test_event_store

- Disabled temporary admission preserves same event
- Existing Trigger backlog retained; no production skipped downgrade
- test_job_event_deferred_inbox.py and revised Event test; real production deferred-settlement defect fixed, original red retained

Nodes:

- `test_trigger_disabled_after_accept_never_resurrects[disabled]`

## tests.test_illegal_queue_wake_http

- Crash Job notice locked until explicit Wake, original ID once
- E01 old write removed; queue/Illegal behavior retained
- Canonical process/Run/outbox realHTTP successor pending

Nodes:

- `test_pan_and_long_job_crash_notification_stays_locked_until_wake`

## tests.test_independent_job_bridge

- Independent lifetime and old remote metadata mutation refusal
- E15 separate executor stays, second Job lifecycle removed
- Native execution host/owner tests; exact realHTTP successor pending

Nodes:

- `test_core_routes_only_new_generic_jobs_and_never_reconciles_remote`
- `test_unified_gui_reads_remote_and_refuses_local_metadata_mutation`

## tests.test_interaction_questions

- Trusted card scope/multi-choice original unknown reconciliation
- S09 legacy Automation execution retired; interactions retained
- Canonical Automation fixture hookup needed; preserve semantic assertions

Nodes:

- `test_trusted_question_scopes_and_independent_unknown_reconciliation`
- `test_large_v2_runtime_admission_and_scope_recheck`
- `test_question_event_survives_deletion_without_resend`

## tests.test_job_action_templates

- Template schedule validation and full Job instantiation
- E05 time slice replaced with complete Job Template
- Template native product form tests; exact missing validation pending

Nodes:

- `test_snapshot_io_does_not_hold_job_registry_or_block_event_loop`
- `test_shared_scheduler_entry_and_misfire_do_not_replay_effects`

## tests.test_job_completion_recovery

- Original completion recovery without replay
- S11 old notifications removed, canonical outbox retained
- Canonical cold original outbox tests; exact node mapping pending

Nodes:

- `test_system_delivery_confirmation_does_not_resend_receipts`

## tests.test_job_delete_http_owned

- Ownership-bound safe delete through realHTTP
- E04 canonical router replaces legacy mixed shape; safe deletion retained
- Normal owner preserved-data + canonical retire; exact existing real-process safety pending

Nodes:

- `test_real_owned_tree_delete_http[False]`
- `test_real_owned_tree_delete_http[True]`

## tests.test_job_delete_safety

- Only owned resources deletion and no active unknown deletion
- Final model D6/owner constraints retained
- Current canonical owner fence/deletion evidence; exact fixture shape pending

Nodes:

- `test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent`

## tests.test_job_final_admission

- Revision-stale and paused/disabled deferred vs retired skipped
- Existing retirement_trigger_backlog contract
- Revised assertions preserve原 red; named deferred/noeffect/currentrevision/originalID tested

Nodes:

- `test_fa02_trigger_revision_gates_new_admission_but_not_original_lookup`
- `test_fa03_trigger_lifecycle_only_gates_future_admission[disable]`
- `test_fa03_trigger_lifecycle_only_gates_future_admission[pause]`

## pytest

- Unclassified
- No disposition presumed
- Pending traceback review

Nodes:

- `internal`
