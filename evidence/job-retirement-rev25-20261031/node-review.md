# Revision25 original-node disposition

Immutable source: `f5f05ca5be51e3dbcd1bc4b4cd5b29ebe0ab1597`.

All original assertions and historical red provenance remain in current43-matrix.json. Counts below are exact current execution evidence, not all original nodes green.

## 1. `tests/test_jobs_api.py::test_list_jobs_mixed_kinds_and_filters`

**historical-bounded-not-reaccepted-in-rev25**. Mixed original records list/filter/sort via read-only fixture.

Remaining: No legacy creation or runtime migration claim..

Registered successor IDs:

- `tests/test_jobs_api.py::test_list_jobs_mixed_kinds_and_filters`

Execution: historical only in this batch.

## 2. `tests/test_jobs_api.py::test_structured_identity_new_records`

**historical-bounded-not-reaccepted-in-rev25**. Canonical Job/Run/Attempt and original queue/task/source identities are linked.

Remaining: Old kind/flat dispatch projections are internal; old MCP compatibility remains separately open..

Registered successor IDs:

- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`
- `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[assign]`
- `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[session-broadcast]`
- `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[session-message]`

Execution: `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[assign]` passed, `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[session-broadcast]` passed, `tests/test_session_product_closure.py::test_product_http_real_queue_source_and_cold_original_query[session-message]` passed.

## 3. `tests/test_jobs_api.py::test_patch_rename_and_description`

**historical-bounded-not-reaccepted-in-rev25**. Rename/description, CAS, original operation and reload.

Remaining: No supplier consumption claim..

Registered successor IDs:

- `tests/test_job_retirement_closure_successors.py::test_rename_description_roundtrip_cas_and_original_operation`

Execution: historical only in this batch.

## 4. `tests/test_jobs_api.py::test_patch_blank_name_rejected`

**historical-bounded-not-reaccepted-in-rev25**. Empty and whitespace canonical names reject before mutation.

Remaining: Business payload names use their own schema; name scope matrix also passed..

Registered successor IDs:

- `tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk[ ]`
- `tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk[\t\n]`
- `tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk[]`

Execution: historical only in this batch.

## 5. `tests/test_jobs_api.py::test_patch_target_switch_updates_flat_and_struct`

**historical-bounded-not-reaccepted-in-rev25**. Target binding edit persists revision and keeps frozen Run/plan unchanged.

Remaining: Native definition assertion; old flat duplicate fields retired..

Registered successor IDs:

- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[  \u5408\u6cd5 English  -broadcast]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[  \u5408\u6cd5 English  -message]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[  \u5408\u6cd5 English  -qq]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[Legal English-broadcast]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[Legal English-message]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[Legal English-qq]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[\u5408\u6cd5\u4e2d\u6587-broadcast]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[\u5408\u6cd5\u4e2d\u6587-message]`
- `tests/test_job_retirement_literal_validation.py::test_legal_literal_create_edit_cas_names_and_frozen_run[\u5408\u6cd5\u4e2d\u6587-qq]`

Execution: historical only in this batch.

## 6. `tests/test_jobs_api.py::test_patch_enable_disable_stops_entries`

**historical-bounded-not-reaccepted-in-rev25**. Actual source disable prevents future Event/effect; Job disable/retire denies new admission but preserves admitted Run; explicit queued cancel has zero effect.

Remaining: Old flattened completed/nextFireAt fields are replaced by independent Creator/Job/Run states; not old URL compatibility..

Registered successor IDs:

- `tests/test_retirement_rev16_behavior.py::test_creator_disable_stops_future_events_and_effects`
- `tests/test_retirement_rev16_behavior.py::test_job_lifecycle_preserves_admitted_run_but_explicit_cancel_prevents_effect[disable]`
- `tests/test_retirement_rev16_behavior.py::test_job_lifecycle_preserves_admitted_run_but_explicit_cancel_prevents_effect[retire]`

Execution: historical only in this batch.

## 7. `tests/test_jobs_api.py::test_action_template_default_assign`

**historical-bounded-not-reaccepted-in-rev25**. Accepted r14/r15 simple precomposed assign form reaches actual queue with frozen/source/task identity.

Remaining: Explicit simple product entry, not arbitrary unknown-action fallback or old dispatcher..

Registered successor IDs:

- `tests/test_retirement_rev14_templates.py::test_production_form_assign_queue_source_frozen_identity_cold_no_duplicate`
- `tests/test_retirement_rev14_templates.py::test_fresh_production_browser_real_template_http_queue_and_shell`

Execution: historical only in this batch.

## 8. `tests/test_jobs_api.py::test_action_template_send_session`

**historical-bounded-not-reaccepted-in-rev25**. Scheduled session-message reaches real worker.send_session with canonical receipt.

Remaining: Explicit native composition, not restored generic dispatch..

Registered successor IDs:

- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`

Execution: historical only in this batch.

## 9. `tests/test_jobs_api.py::test_action_template_unknown_falls_back_to_assign`

**precise-model-conflict-unknown-action-identity-still-open**. Original node rejects unknown public PATCH but forcibly seeds unknown action and expects fallback assign.

Remaining: Original assertion rejects PATCH action.api=create_session, then directly writes that unknown api into legacy record and expects assign on scheduler tick. Canonical steps require an explicit immutable actionRef and D6 forbids executing unknown foreign actions. Runtime amendment permits target/payload changes, not Action identity substitution. Safe explicit assign/default forms already exist; automatic reinterpretation of a corrupt unknown Action requires MA/user plan decision. No generic fallback added..

Registered successor IDs:

- No current strict successor.

Execution: historical only in this batch.

## 10. `tests/test_jobs_api.py::test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates`

**historical-bounded-not-reaccepted-in-rev25**. Current illegal query rescan, real configured Wake, per-fire target and queue identity.

Remaining: Paused owned Sessions; no live supplier Worker/CLI acceptance..

Registered successor IDs:

- `tests/test_retirement_rev6_resume_native.py::test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles`

Execution: historical only in this batch.

## 11. `tests/test_jobs_api.py::test_resume_legal_running_empty_scan_is_successful_noop`

**historical-bounded-not-reaccepted-in-rev25**. Zero matches completes with matchedCount=0/results=[] and no new queue rows.

Remaining: Direct native selection, not unmatched Event surrogate..

Registered successor IDs:

- `tests/test_retirement_rev6_resume_native.py::test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles`

Execution: historical only in this batch.

## 12. `tests/test_jobs_api.py::test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged`

**historical-bounded-not-reaccepted-in-rev25**. Matching actual time Event admits empty resume Run once, settles publication and no backlog.

Remaining: Synthetic temporary graph, not real pending cohort..

Registered successor IDs:

- `tests/test_session_resume_product.py::test_scheduled_passive_template_matching_empty_fire_not_backlogged`

Execution: historical only in this batch.

## 13. `tests/test_jobs_api.py::test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text`

**historical-bounded-not-reaccepted-in-rev25**. Edit name retains dynamic resume; fixed text/target attempts reject.

Remaining: Configured Wake message is current source, no arbitrary replacement text..

Registered successor IDs:

- `tests/test_session_resume_product.py::test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit`

Execution: historical only in this batch.

## 14. `tests/test_jobs_api.py::test_resume_legal_running_keeps_per_fire_client_message_id_stable`

**historical-bounded-not-reaccepted-in-rev25**. Original child effect client ID and queue ID query back to same fire Run; next fire new identity.

Remaining: No remote provider dedup protocol invented..

Registered successor IDs:

- `tests/test_retirement_rev6_resume_native.py::test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles`

Execution: historical only in this batch.

## 15. `tests/test_jobs_api.py::test_runs_and_next_and_run_now`

**historical-bounded-not-reaccepted-in-rev25**. Manual admission preserves actual Creator cursor; no fake Event or legacy run-now call.

Remaining: Separate retained history read matrix; native fields replace old envelopes..

Registered successor IDs:

- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`

Execution: historical only in this batch.

## 16. `tests/test_jobs_api.py::test_partial_failed_event_emitted`

**verified-partial-visible-consumer-owned-production-browser**. Real original fanout queue produces rejected target plus original WS outbox; fresh production React useWebSocket and ToastContainer consume server.broadcast actual frames. Original outbox repeated frame yields one visible failed-target warning, receipt/effect identity and queue stay original through cold reopen.

Remaining: Current job.updated envelope is used; literal job.partial_failed alias is not restored. Live deployment or real client acceptance is unverified..

Registered successor IDs:

- `tests/test_retirement_rev25_browser.py::test_fresh_browser_original_partial_outbox_ws_duplicate_and_runtime_form_http`

Execution: `tests/test_retirement_rev25_browser.py::test_fresh_browser_original_partial_outbox_ws_duplicate_and_runtime_form_http` passed.

## 17. `tests/test_jobs_api.py::test_post_creates_scheduled_task_multi_entry`

**historical-bounded-not-reaccepted-in-rev25**. Independent multi-rule cursors with real scheduled graph effects.

Remaining: Legacy entries are mapped to Creator rule IDs; no old scheduler host execution..

Registered successor IDs:

- `tests/test_job_final_creators.py::test_fs01_multiple_rules_and_after_publication_have_independent_cursors`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`

Execution: historical only in this batch.

## 18. `tests/test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner`

**historical-bounded-not-reaccepted-in-rev25**. Accepted r15 raw quoted command/cwd/no target/passive no-Run then real owned process and cold original lookup; exact configured Windows cmd envelope.

Remaining: No new scheduled legacy API; unproven child COMSPEC uses original CRT; POSIX follow-up not rerun..

Registered successor IDs:

- `tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup`
- `tests/test_retirement_rev14_templates.py::test_fresh_production_browser_real_template_http_queue_and_shell`

Execution: historical only in this batch.

## 19. `tests/test_jobs_api.py::test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields`

**historical-bounded-not-reaccepted-in-rev25**. r7 accepted trusted fixed QQ literal validation reverified: invalid target/text/bot fields no write; legal raw text/IDs persisted, edited and frozen.

Remaining: No real accounts; owned plugin validation/effect boundaries separate; old retired URL is not restored..

Registered successor IDs:

- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[botUin-untrusted-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[endpoint-http://untrusted-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[sessionId-unknown-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetId-123-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetId-bad-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetType-private-create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[text-  -create]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[text--create]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[10000-  \u4e2d\u6587 CQ [CQ:face,id=1]\n-10000]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[10000-  \u4e2d\u6587 CQ [CQ:face,id=1]\n-None]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[99999999999999999999-\t legal English  -10000]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[99999999999999999999-\t legal English  -None]`

Execution: historical only in this batch.

## 20. `tests/test_jobs_api.py::test_patch_scheduled_qq_action_is_strict_and_editable`

**historical-bounded-not-reaccepted-in-rev25**. r7 accepted trusted fixed QQ literal validation reverified: invalid target/text/bot fields no write; legal raw text/IDs persisted, edited and frozen.

Remaining: No real accounts; owned plugin validation/effect boundaries separate; old retired URL is not restored..

Registered successor IDs:

- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[botUin-untrusted-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[endpoint-http://untrusted-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[sessionId-unknown-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetId-123-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetId-bad-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[targetType-private-update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[text-  -update]`
- `tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin[text--update]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[10000-  \u4e2d\u6587 CQ [CQ:face,id=1]\n-10000]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[10000-  \u4e2d\u6587 CQ [CQ:face,id=1]\n-None]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[99999999999999999999-\t legal English  -10000]`
- `tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan[99999999999999999999-\t legal English  -None]`

Execution: historical only in this batch.

## 21. `tests/test_jobs_api.py::test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error`

**historical-bounded-not-reaccepted-in-rev25**. Accepted r9/r10 real plugin HTTP routing; current default and frozen-edit checks pass, selected-bot unchanged node fails in matrix/isolated run then passes with read-only diagnostics.

Remaining: Current selected success is inconsistent. Preserve failing and passing raw artifacts; no inferred fix, no complete successor claimed..

Registered successor IDs:

- `tests/test_retirement_rev9_qq_http.py::test_original_plugin_real_onebot_http_success_exact_receipt_single_send[None-user-111111-send_private_msg]`
- `tests/test_retirement_rev9_qq_http.py::test_edit_after_admission_keeps_actual_original_bot_target_text`

Execution: historical only in this batch.

## 22. `tests/test_jobs_api.py::test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure`

**verified-targetless-original-plugin-errors-bounded-current**. Actual original plugin error equals canonical raw receipt for default/selected/offline/unknown bot. No Session target/effect, one canonical Run/Attempt/effect, ordinary failed old release never automatically resends.

Remaining: The three old rev6 red nodes also asserted manual retry must be denied; accepted r10 deliberately permits trusted no-send manual retry. Their exact 409 assertion remains red at cc3fc708 and is not edited or relabelled green. Old retired HTTP envelope compatibility and real QQ delivery are not claimed..

Registered successor IDs:

- `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[None-bot_not_connected]`
- `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[222222-bot_not_connected]`
- `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[333333-unknown_bot_uin]`

Execution: `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[None-bot_not_connected]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[222222-bot_not_connected]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_original_targetless_manual_qq_error_matches_actual_plugin_and_has_no_session_effect[333333-unknown_bot_uin]` passed.

## 23. `tests/test_jobs_api.py::test_scheduled_qq_backlog_retry_runs_without_session_target`

**verified-canonical-runtime-opt-in-automatic-backlog**. Real Scheduler fires retain separate original Event identities and committed pending child facts (dispatchKey/fireAt/text/error) in the original Run plan. Same unfinished Attempt/effect continues automatically through existing pulse only for original identity-bound no-send; per-fire current target/text/bot, pause/disable/revoke, CAS/concurrent pulse, hard crash/owner handoff, successful exact removal from pending projection, unknown no-replay.

Remaining: Opt-in new immutable release only. No real records migrated, no old backlog registry activated or rewritten. Migration owner must consume the release contract; sending/unknown has no remote query protocol and remains held..

Registered successor IDs:

- `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[before-reservation-completed-1]`
- `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[before-invoke-uncertain-0]`
- `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[after-qq-uncertain-1]`
- `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[after-receipt-completed-1]`
- `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[pause]`
- `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[disable]`
- `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[revoke]`
- `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[missing-proof]`
- `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[wrong-attempt]`
- `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[wrong-arguments]`
- `tests/test_retirement_rev25_qq_runtime.py::test_two_real_scheduler_fires_cold_drain_current_bot_each_original_event_once`
- `tests/test_retirement_rev25_qq_runtime.py::test_real_qq_no_send_cold_pulse_same_attempt_effect_current_target_text[None]`
- `tests/test_retirement_rev25_qq_runtime.py::test_real_qq_no_send_cold_pulse_same_attempt_effect_current_target_text[222222]`
- `tests/test_retirement_rev25_qq_runtime.py::test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt[lost-response]`
- `tests/test_retirement_rev25_qq_runtime.py::test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt[missing-id]`
- `tests/test_retirement_rev25_qq_runtime.py::test_unknown_selected_bot_pending_then_current_bot_switch_with_cas_and_no_manual_retry[222222]`
- `tests/test_retirement_rev25_qq_runtime.py::test_unknown_selected_bot_pending_then_current_bot_switch_with_cas_and_no_manual_retry[None]`

Execution: `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[before-reservation-completed-1]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[before-invoke-uncertain-0]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[after-qq-uncertain-1]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_actual_qq_hard_crash_owner_handoff_original_identity_only[after-receipt-completed-1]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[pause]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[disable]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_proven_unsent_qq_current_gate_blocks_same_attempt_without_budget[revoke]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[missing-proof]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[wrong-attempt]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_cold_corrupt_qq_no_send_proof_never_authorizes_automatic_replay[wrong-arguments]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_two_real_scheduler_fires_cold_drain_current_bot_each_original_event_once` passed, `tests/test_retirement_rev25_qq_runtime.py::test_real_qq_no_send_cold_pulse_same_attempt_effect_current_target_text[None]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_real_qq_no_send_cold_pulse_same_attempt_effect_current_target_text[222222]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt[lost-response]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_runtime_actual_accepted_unknown_never_resends_or_creates_attempt[missing-id]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_unknown_selected_bot_pending_then_current_bot_switch_with_cas_and_no_manual_retry[222222]` passed, `tests/test_retirement_rev25_qq_runtime.py::test_unknown_selected_bot_pending_then_current_bot_switch_with_cas_and_no_manual_retry[None]` passed.

## 24. `tests/test_jobs_api.py::test_post_accepts_session_message_and_rejects_empty_schedule`

**historical-bounded-not-reaccepted-in-rev25**. Canonical literal empty time rules rejected at create/update with zero operation/Creator/effect mutation.

Remaining: Session-message composition positive is historical support; unknown Action rejection and exact entry mapping not newly reverified. Empty-schema rejection alone does not close original multi-assertion node or clear-target backlog..

Registered successor IDs:

- `tests/test_retirement_rev16_behavior.py::test_literal_empty_schedule_create_rejected_without_operation_creator_or_effect`
- `tests/test_retirement_rev16_behavior.py::test_literal_empty_or_bad_schedule_update_rejects_without_any_write[rules0]`

Execution: historical only in this batch.

## 25. `tests/test_jobs_api.py::test_post_requires_existing_session`

**historical-bounded-not-reaccepted-in-rev25**. Original four source-aware message/broadcast POST/PUT red nodes now reject absent fixed Session before write.

Remaining: r7/r8 accepted fix; no Worker/history lookup; custom Action rules remain their own schema..

Registered successor IDs:

- `tests/test_retirement_rev6_session_validation.py::test_current_source_aware_product_missing_fixed_session_rejected_before_commit[session_broadcast-create]`
- `tests/test_retirement_rev6_session_validation.py::test_current_source_aware_product_missing_fixed_session_rejected_before_commit[session_broadcast-update]`
- `tests/test_retirement_rev6_session_validation.py::test_current_source_aware_product_missing_fixed_session_rejected_before_commit[session_message-create]`
- `tests/test_retirement_rev6_session_validation.py::test_current_source_aware_product_missing_fixed_session_rejected_before_commit[session_message-update]`

Execution: historical only in this batch.

## 26. `tests/test_jobs_api.py::test_patch_schedule_replaces_entries`

**historical-bounded-not-reaccepted-in-rev25**. Real HTTP old once -> cron+7200 interval replacement, old cursor removed, earliest new cursor, old time zero event/admission/queue, new actual Event with current revision and one queue effect.

Remaining: Independent Creator definition replaces old flattened schedule fields; existing published Events retain original identity..

Registered successor IDs:

- `tests/test_retirement_rev16_behavior.py::test_schedule_replacement_removes_old_rule_and_fires_only_new_real_event`

Execution: historical only in this batch.

## 27. `tests/test_jobs_api.py::test_patch_schedule_rejects_bad_spec`

**historical-bounded-not-reaccepted-in-rev25**. Actual malformed cron expression at HTTP update rejected without config/cursor/operation changes.

Remaining: Native error envelope, not old invalid_schedule code alias..

Registered successor IDs:

- `tests/test_retirement_rev16_behavior.py::test_literal_empty_or_bad_schedule_update_rejects_without_any_write[rules1]`

Execution: historical only in this batch.

## 28. `tests/test_jobs_api.py::test_patch_target_clear_enters_undeliverable`

**verified-production-runtime-form-clear-target-pending**. Real server form selects new assign release, creates passive Template/Job, clears declared runtime target after admission, no Attempt/effect while absent; restore different target/text queues once with original business task and frozen Run.

Remaining: Actual API lifecycle plus fresh browser opt-in verified; no real cohort migration. New model uses Event/Run plan pending facts, not legacy file mutation..

Registered successor IDs:

- `tests/test_retirement_rev25_form.py::test_real_form_instance_clear_target_pending_restore_preserves_business_task_and_frozen_plan`

Execution: `tests/test_retirement_rev25_form.py::test_real_form_instance_clear_target_pending_restore_preserves_business_task_and_frozen_plan` passed.

## 29. `tests/test_jobs_api.py::test_patch_text_updates_dispatch_body`

**historical-bounded-not-reaccepted-in-rev25**. Actual HTTP CAS winner supplies text only to still-unsent target; accepted target retains original bytes, same frozen Run and same Attempt/effect.

Remaining: Only explicit new runtime releases plus Job declaration. Old releases intentionally remain frozen; production migration/caller opt-in not claimed..

Registered successor IDs:

- `tests/test_retirement_session_runtime.py::test_real_http_concurrent_cas_during_first_acceptance_selects_only_winning_unsent_values`

Execution: `tests/test_retirement_session_runtime.py::test_real_http_concurrent_cas_during_first_acceptance_selects_only_winning_unsent_values` passed.

## 30. `tests/test_jobs_api.py::test_run_records_carry_entry_id`

**historical-bounded-not-reaccepted-in-rev25**. Actual time fire keeps Event/Trigger IDs in canonical Run and original queue receipt.

Remaining: Old entry identity in real migrated records remains unverified..

Registered successor IDs:

- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`

Execution: historical only in this batch.

## 31. `tests/test_jobs_api.py::test_pin_failure_keeps_disk_and_emits_nothing`

**historical-bounded-not-reaccepted-in-rev25**. Retained pin failure keeps bytes, emits nothing; native failures rollback.

Remaining: Read-only archive and native writer proofs are separate..

Registered successor IDs:

- `tests/test_job_retained_canonical_pins.py::test_pin_write_failure_rolls_back_disk_and_has_no_success_or_outbox[http-config]`
- `tests/test_job_retained_canonical_pins.py::test_pin_write_failure_rolls_back_disk_and_has_no_success_or_outbox[runtime-metadata]`
- `tests/test_job_retained_pin_successor.py::test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing`

Execution: historical only in this batch.

## 32. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-json]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-json]`

Execution: historical only in this batch.

## 33. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-encoding]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-encoding]`

Execution: historical only in this batch.

## 34. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-read]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-read]`

Execution: historical only in this batch.

## 35. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-json]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-json]`

Execution: historical only in this batch.

## 36. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-encoding]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-encoding]`

Execution: historical only in this batch.

## 37. `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-read]`

**historical-bounded-not-reaccepted-in-rev25**. Original parametrized corruption assertion through retained fixture.

Remaining: Only read/pin semantics; fixture is not migration/effect execution proof..

Registered successor IDs:

- `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-read]`

Execution: historical only in this batch.

## 38. `tests/test_scheduler_legacy_bridge.py::test_run_now_does_not_move_periodic_next_fire`

**historical-bounded-not-reaccepted-in-rev25**. Manual admission preserves actual Creator cursor; no fake Event or legacy run-now call.

Remaining: Actual native scheduled assign/message and manual Runs; original real source activation not claimed..

Registered successor IDs:

- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[assign]`
- `tests/test_retirement_rev6_schedule.py::test_explicit_passive_time_template_reuses_original_worker_and_manual_keeps_cursor[session-message]`

Execution: historical only in this batch.

## 39. `tests/test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent`

**historical-bounded-not-reaccepted-in-rev25**. Actual process late terminal writeback never recreates retired canonical parent.

Remaining: No physical old cohort purge..

Registered successor IDs:

- `tests/test_job_retained_safety_canonical.py::test_terminal_child_writeback_does_not_recreate_retired_parent_successor`

Execution: historical only in this batch.

## 40. `tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]`

**historical-bounded-not-reaccepted-in-rev25**. Public cancel original owned tree then logical retire with original proof.

Remaining: Windows current host only; not entire old owner retirement..

Registered successor IDs:

- `tests/test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[False]`

Execution: historical only in this batch.

## 41. `tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]`

**historical-bounded-not-reaccepted-in-rev25**. Public cancel original owned tree then logical retire with original proof.

Remaining: Windows current host only; not entire old owner retirement..

Registered successor IDs:

- `tests/test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[True]`

Execution: historical only in this batch.

## 42. `tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt`

**verified-real-delegated-monitoring-revocation-budget**. Actual detached process monitoring rejection schedules original autoRecovery; revoke delegated authority before due pulse, then cold reopen: original Attempt/effect, recovery budget/window/deadline and one invocation unchanged.

Remaining: Owned local process only. No real supplier/CLI monitoring or deployment claim; accepted monitoring/recovery algorithm is reused unchanged..

Registered successor IDs:

- `tests/test_retirement_rev25_monitoring.py::test_real_delegated_monitoring_revocation_preserves_attempt_budget_deadline_and_original_effect`

Execution: `tests/test_retirement_rev25_monitoring.py::test_real_delegated_monitoring_revocation_preserves_attempt_budget_deadline_and_original_effect` passed.

## 43. `tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http`

**historical-bounded-not-reaccepted-in-rev25**. Real TCP canonical quota admission, unavailable adapter unknown, cold reopen same Run/Attempt/effect, no legacy writer.

Remaining: No real accounts, real quota query or broader threshold/policy acceptance..

Registered successor IDs:

- `tests/test_quota_native_admission.py::test_unknown_real_tcp_cold_reopen`

Execution: historical only in this batch.
