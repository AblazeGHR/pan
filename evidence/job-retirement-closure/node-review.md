# JOB-RETIRE revision2 逐节点审阅

原 43 是历史失败集合；P23 原三红是其中子集。下列结果不改写旧证据，也不等同整宿主退役。

| 原节点 | 后继/状态 | 保留语义与缺口 |
|---|---|---|
| `tests/test_jobs_api.py::test_list_jobs_mixed_kinds_and_filters` | strict-successor: `tests/test_jobs_api.py::test_list_jobs_mixed_kinds_and_filters` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_structured_identity_new_records` | gap:  | 旧 kind 双写已退役；改验 native Job ID + frozen Action/target provenance，同时保留旧扁平 ID 只读投影的独立覆盖。 |
| `tests/test_jobs_api.py::test_patch_rename_and_description` | strict-successor: `tests/test_job_retirement_closure_successors.py::test_rename_description_roundtrip_cas_and_original_operation` | Native definition/CAS/frozen Run with recording Session provider; no real CLI consumption |
| `tests/test_jobs_api.py::test_patch_blank_name_rejected` | product-gap: `tests/test_job_retirement_closure_successors.py::test_blank_name_rejection_preserves_definition_operation_and_disk` | Empty string rejected; whitespace-only values accepted and committed: strict successor red |
| `tests/test_jobs_api.py::test_patch_target_switch_updates_flat_and_struct` | strict-successor: `tests/test_job_retirement_closure_successors.py::test_target_and_text_edit_only_changes_future_frozen_runs` | Native definition/CAS/frozen Run with recording Session provider; no real CLI consumption |
| `tests/test_jobs_api.py::test_patch_enable_disable_stops_entries` | gap:  | 迁为 native lifecycle config/state CAS；已准入 Run 与 future Creator events 分开验证，不能恢复旧 entries 驱动。 |
| `tests/test_jobs_api.py::test_action_template_default_assign` | gap:  | 删除旧默认 assign 期望的执行含义；显式 Action ref 与 typed args 在统一 Run 路径验收。 |
| `tests/test_jobs_api.py::test_action_template_send_session` | gap:  | 用 product_session_message + frozen Step 替代 run_due_scheduled_tasks，保留一次效果及原 effectKey 收据断言。 |
| `tests/test_jobs_api.py::test_action_template_unknown_falls_back_to_assign` | gap:  | 此正向 fallback 与终局明确冲突；后继必须拒绝未知 Action/legacy 字段，不能迁回默认 assign。 |
| `tests/test_jobs_api.py::test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates` | gap:  | 迁为明确 native Session operation/provider；保留 effect admission 时冻结合法候选和 run-again 显式新 Run。该具体动态选择后继尚未在本次证明。 |
| `tests/test_jobs_api.py::test_resume_legal_running_empty_scan_is_successful_noop` | gap:  | 旧 route 410 正确；空候选是否完成必须在 native typed provider 契约补精确测试，本次不声称覆盖该业务效果。 |
| `tests/test_jobs_api.py::test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged` | gap:  | 迁 Creator publication 与 Job admission 分离；补空选择不新增 backlog/effect 的 native provider 断言。 |
| `tests/test_jobs_api.py::test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text` | gap:  | 改 native Step 的显式 query/fixed text；保留动态 target，不借 metadata flat 字段重建 legacy dispatcher。 |
| `tests/test_jobs_api.py::test_resume_legal_running_keeps_per_fire_client_message_id_stable` | gap:  | 不再调用已退役 _execute_job_action；保留同 Run/Step/target effectKey 和 provider 去重键断言。 |
| `tests/test_jobs_api.py::test_runs_and_next_and_run_now` | gap:  | 拆为 readonly 原 ID/runs 与 native manual admission/Creator nextFire；旧 run-now URL 应保持 410。 |
| `tests/test_jobs_api.py::test_partial_failed_event_emitted` | gap:  | 以 native partial/uncertain effect + 唯一 outbox 验收，不恢复 start_broadcast 或重复 terminal notice。 |
| `tests/test_jobs_api.py::test_post_creates_scheduled_task_multi_entry` | gap:  | 用独立 Creator 多规则 + Job/Trigger 显式定义；保留各 ruleId/occurrence，旧 scheduled-task POST 不再合法。 |
| `tests/test_jobs_api.py::test_post_creates_scheduled_shell_action_without_target_or_runner` | gap:  | 用 product_process native Action 参数和 independent execution intent；保留未执行前不分配实际 runner 的断言。 |
| `tests/test_jobs_api.py::test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields` | gap:  | 迁 typed channel Action 参数/schema，保留 bot/target 持久及非自报授权。未连接真实 QQ provider。 |
| `tests/test_jobs_api.py::test_patch_scheduled_qq_action_is_strict_and_editable` | gap:  | 用 native Job revision 更新 typed channel arguments；保持严格 schema、编辑不更改历史 frozen plan。 |
| `tests/test_jobs_api.py::test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error` | gap:  | 通过 typed channel executor 保留明确成功/离线拒绝，不用 legacy kind dispatcher；精确 bot 业务效果需 provider 专项后继。 |
| `tests/test_jobs_api.py::test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure` | gap:  | manual Run 不伪造时间 Event；显式 provider reject 与未知结果区分。旧 route 410 保持。 |
| `tests/test_jobs_api.py::test_scheduled_qq_backlog_retry_runs_without_session_target` | gap:  | 不恢复旧 undelivered backlog；用同 Run/Step/effectKey 新 Attempt，unknown 必须查询。本次未证明 QQ 专属重试。 |
| `tests/test_jobs_api.py::test_post_accepts_session_message_and_rejects_empty_schedule` | gap:  | 旧 kind POST 不合法；native Job 手动可无时间来源，Creator 空 rules 另行严格拒绝，不能照搬旧 combined schema。 |
| `tests/test_jobs_api.py::test_post_requires_existing_session` | gap:  | native API 的目标检查在对应 typed provider/业务层；保留无效 Session 不发生效果；精确业务检查需 Session provider 专项。 |
| `tests/test_jobs_api.py::test_patch_schedule_replaces_entries` | gap:  | 时间配置归 Creator，不在 Job PATCH 替换 schedule；Creator revision/CAS/anchor/cursor 回归继续验。 |
| `tests/test_jobs_api.py::test_patch_schedule_rejects_bad_spec` | gap:  | 保留 invalid time config 的无写入拒绝，入口改 canonical Creator schema。 |
| `tests/test_jobs_api.py::test_patch_target_clear_enters_undeliverable` | gap:  | 旧 undeliverable/backlog 不再作为新执行状态；native 定义/入场严格拒绝无效必要参数，不能猜目标。 |
| `tests/test_jobs_api.py::test_patch_text_updates_dispatch_body` | strict-successor: `tests/test_job_retirement_closure_successors.py::test_target_and_text_edit_only_changes_future_frozen_runs` | Native definition/CAS/frozen Run with recording Session provider; no real CLI consumption |
| `tests/test_jobs_api.py::test_run_records_carry_entry_id` | gap:  | 旧 entryId 只读历史必须保留；新来源用 creatorId/occurrenceKey/driveId，不伪造旧执行记录。 |
| `tests/test_jobs_api.py::test_pin_failure_keeps_disk_and_emits_nothing` | strict-successor: `tests/test_job_retained_pin_successor.py::test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing` | 原 fixture 注入 background_jobs._atomic_write 已不在 JsonPins 实际写路径；保留原红，新增实际 coordinator.atomic_write 失败后继，证明 pins 字节不变、无广播、GET 仍 pinned。 |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-json]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-json]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-encoding]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-encoding]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-read]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[session-message-read]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-json]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-json]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-encoding]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-encoding]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-read]` | strict-successor: `tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations[scheduled-task-read]` | P24 offline historical fixture; no canonical WS CRUD or executable legacy writer claim |
| `tests/test_scheduler_legacy_bridge.py::test_run_now_does_not_move_periodic_next_fire` | strict-successor: `tests/test_job_retained_history_bridge.py::test_run_now_does_not_move_periodic_next_fire_retained_successor` | P24 synthetic same-identity owner/cursor/receipt; real cohorts remain unverified |
| `tests/test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent` | strict-successor: `tests/test_job_retained_safety_canonical.py::test_terminal_child_writeback_does_not_recreate_retired_parent_successor` | P23 public Run cancel then logical retire; no physical definition purge claim |
| `tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]` | strict-successor: `tests/test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[False]` | P23 public Run cancel then logical retire; no physical definition purge claim |
| `tests/test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]` | strict-successor: `tests/test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[True]` | P23 public Run cancel then logical retire; no physical definition purge claim |
| `tests/test_authorization_runtime.py::test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt` | gap:  | Unbound automatic recovery is unsupported; full bound policy revocation/budget successor missing |
| `tests/test_quota_policy_http.py::test_quota_policy_owned_http_default_unavailable_is_unknown_real_http` | gap:  | Original NameError before HTTP; canonical query/control real TCP successor missing; do not restore retired Job POST |

## E/S/U 全量 45 项

| ID | 当前观察 | 证据入口 | 删除前缺口 |
|---|---|---|---|
| E01 | server.py retains read/cancel; retired write wrappers coexist with canonical API | test_job_final_retirement.py | G2 old Runner query/cancel ownership must remain |
| E02 | Legacy session-message producer removed/refused; native typed message remains | test_job_final_retirement.py | Real Session/QQ provider-specific exact successors remain incomplete |
| E03 | Legacy identity must remain audit-only; canonical User/Agent scope separate | test_job_final_http_service.py | Do not elevate legacy history owner to trusted caller |
| E04 | jobs/api.py remains imported facade/history; final_api owns native definitions | test_scheduler_surface_retirement.py | Cannot delete mixed facade before history/control parity |
| E05 | Old template writes refused; native passive Template supported | test_job_final_http_service.py | Full custom/builtin provenance migration not established |
| E06 | Scheduler read router remains; old writes 410; owner-switch absent | test_scheduler_surface_retirement.py | History/read authorization and real owner settlement required before router removal |
| E07 | background start/retry absent from catalog, read/cancel retained | test_job_final_retirement.py | Body/registry de-registration is not full producer file retirement |
| E08 | message create/update absent from catalog, read/cancel retained | test_job_final_retirement.py | Real existing Runner identities not inspected |
| E09 | Six scheduler write bodies absent; list/get/runs retained | test_scheduler_surface_retirement.py | Read MCP tools intentionally remain; full nine-tool removal not true |
| E10 | server.py still contains create_service_job/transition_service_job lifecycle compatibility | test_job_final_http_service.py | Protected lifecycle receipt handoff and old owner inventory incomplete |
| E11 | React canonical management inherited; this task does not modify or rerun UI | R6_I_REPAIR_20261027.md | Current full management/pin dual-source UI acceptance absent; CP-X3 scope untouched |
| E12 | Creator source client inherited; scheduler dynamic catalog tested | test_scheduler_surface_retirement.py | No browser rebuild/retest this round; no real source migration claim |
| E13 | Native PUT/operation editing works; whitespace names commit unexpectedly | test_job_retirement_closure_successors.py | Two new strict reds; complete control/pin/retention/handoff UI not accepted |
| E14 | CLI/console/setup scripts remain entrypoints; lifecycle helpers retain old registry | inventory.json surfaceCandidates | No lifecycle restart/exit executed; no claim of full client cutover |
| E15 | independent_service still hosts JobStore/ServiceLock and canonical /executions | test_job_retained_safety_canonical.py | Keep canonical executor/cleanup/query; old cohort read/cancel/ack clearance missing |
| S01 | 29 import edges to background_jobs remain across packages/scripts | inventory.json dependencyEdges | Store/path/history/locks consumers must be separated before file deletion |
| S02 | Canonical source cold recovery runs same identity; legacy pass does not tick injected runtime | test_scheduler_source_migration.py; test_scheduler_dependency_cutover.py | Unknown missing commit stays uncertain; no real activation inventory |
| S03 | P23 late receipt leaves retired parent unchanged; P24 historical IDs retained | test_job_retained_safety_canonical.py; test_jobs_api.py | Real hidden-child receipt/owner cohort settlement absent |
| S04 | store.py retained, no package host import in current dependency inventory | test_scheduler_dependency_cutover.py | Offline migration tooling/rollback archive and real history transfer still required |
| S05 | scheduler engine removed by inherited work; source engine semantics moved canonical | test_scheduler_dependency_cutover.py | Removal of engine alone does not establish all hosts retirement |
| S06 | legacy_bridge.py retained; P24 synthetic manual Run preserves cursor/receipt | test_job_retained_history_bridge.py | Real original switch operations/owner/receipt finality unknown |
| S07 | system_actions retains execute_typed and legacy helpers guarded by owner contract | test_job_final_http_service.py | Immutable lifecycle authority and bound automatic recovery parity incomplete |
| S08 | 18 imports of action_runtime remain; canonical FinalService drives native runs | inventory.json dependencyEdges; test_scheduler_source_migration.py | Cannot remove whole Runtime while facade/history/authorization helpers depend on it |
| S09 | Automation Trigger admission remains a required canonical consumer | test_scheduler_source_migration.py | Matching/admission/store are not leaf execution hosts; preserve |
| S10 | main_lifecycle canonical and legacy registry compatibility remain | inventory.json surfaceCandidates | No cross-service lifecycle receipt/owner settlement proof this round |
| S11 | P24 outbox pending/ack failure original key and real queue persistence pass | test_job_retained_canonical_pins.py | Production send_final_notice WS/CLI actual consumption absent |
| S12 | independent_runner remains for ServiceLock/compat; native executor lives separately | test_job_retained_safety_canonical.py | Do not delete canonical /executions with old /jobs lifecycle; old owner clearance absent |
| S13 | P24 config/runtime pin holds both protect delete/GC; P23 public owned cleanup passes | test_job_retained_canonical_pin_resolution.py; test_job_retained_safety_canonical.py | Settings/retention/handoff complete management and transport evidence missing |
| S14 | P23 original PID/FILETIME/tree proof succeeds using specified venv | test_job_retained_safety_canonical.py | Windows only; no new process owner code or three-platform acceptance |
| U01 | Canonical HTTP Agent/User authorization remains independent of legacy scope | test_scheduler_surface_retirement.py | Legacy read scopes cannot disappear before G2/G3 |
| U02 | Old safe cancel remains for existing Runner ownership | test_job_retained_safety_canonical.py | Synthetic canonical cancel does not clear unknown real old owners |
| U03 | Hidden child producer retired; old late callback must not recreate parent | test_job_retained_safety_canonical.py | No physical old cohort cleanup claim |
| U04 | Scheduled child producer retired; terminal successor uses same canonical identity | test_job_retained_safety_canonical.py | Keep history and original receipt semantics |
| U05 | Canonical Attempt fence remains; stale original fence must not acquire new identity | test_scheduler_pending_adoption.py | No real old lease inventory |
| U06 | Typed system execution kept; old helpers cannot authorize new parallel owner | test_job_final_http_service.py | Lifecycle/recovery authority needs MA coordination |
| U07 | Native schema refuses legacy fields; old validators mixed in facade | test_job_final_contracts.py | Whitespace-name semantic gap requires shared contract change |
| U08 | schedule_calculations and data_paths extracted; cold same-grid recovery passes | test_scheduler_source_migration.py | No actual real source activation/cleanup proof |
| U09 | P24 original task/job/run/dispatch/entry IDs readable and bytes unchanged | test_jobs_api.py; test_job_retained_history_bridge.py | History audit export/real index transfer before reader removal |
| U10 | scheduler cron shim removed; jobs cron/calculator retained | test_scheduler_dependency_cutover.py | Pure calculator remains required; no broad file-name deletion |
| U11 | Native Template passive composition supported | test_job_final_http_service.py | Full builtin/custom template provenance not reaccepted |
| U12 | P24 independent config and metadata pin holds protect delete/GC | test_job_retained_canonical_pins.py; test_job_retained_canonical_pin_resolution.py | Old pins historical view still separate; UI/Settings parity missing |
| U13 | server lifecycle helpers still query/create old service Job records | inventory.json surfaceCandidates | Original cross-stop operation settlement missing; keep until coordinated |
| U14 | job_config_drafts remains guarded; canonical final_tools available | packages/mcp/server.py::job_config_drafts | Do not claim complete legacy configuration wrapper removal |
| U15 | action_api and final_api coexist; native schema/operation owns new execution | test_job_final_http_service.py | Mixed read/history/authorization dependencies block entire router deletion |
| U16 | MCP manifest explicitly describes retained reads/cancel and refuses old writes | test_scheduler_surface_retirement.py; packages/mcp/manifest.json | Manifest still intentionally advertises retained legacy controls; full retirement false |
