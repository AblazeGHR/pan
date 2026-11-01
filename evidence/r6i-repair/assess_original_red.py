"""Per-node disposition; this does not turn any original red into a pass."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
specific = {
    "test_list_jobs_mixed_kinds_and_filters": ("history-fixture", "以非执行 historical records 替代 start_message/start_broadcast fixture，保留混合列表、过滤和排序断言；不可重新开放旧创建入口。", "tests/test_scheduler_history_read.py; tests/test_jobs_completed_retention.py"),
    "test_structured_identity_new_records": ("retired-positive", "旧 kind 双写已退役；改验 native Job ID + frozen Action/target provenance，同时保留旧扁平 ID 只读投影的独立覆盖。", "tests/test_job_final_http_service.py::test_ft01_real_crud_cas_idempotency_persisted_after_reload"),
    "test_patch_rename_and_description": ("retired-positive", "旧 PATCH 已退役；迁为 canonical PUT expectedRevision，并保留 name/description roundtrip/CAS。", "tests/test_job_final_http_service.py::test_ft01_real_crud_cas_idempotency_persisted_after_reload"),
    "test_patch_blank_name_rejected": ("retired-positive", "用 canonical 定义校验替代旧 PATCH ok envelope，保留空名称拒绝与无写入断言。", "tests/test_job_final_contracts.py"),
    "test_patch_target_switch_updates_flat_and_struct": ("retired-positive", "旧 flat target 写语义不再执行；显式修改 native Step 参数绑定，验证 revision 及未来 Run 冻结，历史不改。", "tests/test_job_final_http_service.py; tests/test_handoff_job_retarget.py"),
    "test_patch_enable_disable_stops_entries": ("retired-positive", "迁为 native lifecycle config/state CAS；已准入 Run 与 future Creator events 分开验证，不能恢复旧 entries 驱动。", "tests/test_scheduler_management_browser.py; tests/test_job_final_creators.py"),
    "test_action_template_default_assign": ("retired-positive", "删除旧默认 assign 期望的执行含义；显式 Action ref 与 typed args 在统一 Run 路径验收。", "tests/test_job_final_contracts.py::test_fc04_legacy_fields_are_rejected_without_fallback"),
    "test_action_template_send_session": ("retired-positive", "用 product_session_message + frozen Step 替代 run_due_scheduled_tasks，保留一次效果及原 effectKey 收据断言。", "tests/test_job_attempt_epoch.py; evidence/r6i-repair/candidate-epoch-green.json"),
    "test_action_template_unknown_falls_back_to_assign": ("retired-positive", "此正向 fallback 与终局明确冲突；后继必须拒绝未知 Action/legacy 字段，不能迁回默认 assign。", "tests/test_job_final_contracts.py::test_fc04_legacy_fields_are_rejected_without_fallback"),
    "test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates": ("retired-positive", "迁为明确 native Session operation/provider；保留 effect admission 时冻结合法候选和 run-again 显式新 Run。该具体动态选择后继尚未在本次证明。", "tests/test_job_final_runtime.py::test_fr01_retry_run_again_and_clone_have_distinct_identities (仅身份相关)"),
    "test_resume_legal_running_empty_scan_is_successful_noop": ("retired-positive", "旧 route 410 正确；空候选是否完成必须在 native typed provider 契约补精确测试，本次不声称覆盖该业务效果。", "未验证精确后继"),
    "test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged": ("retired-positive", "迁 Creator publication 与 Job admission 分离；补空选择不新增 backlog/effect 的 native provider 断言。", "tests/test_job_final_creators.py (只证明来源计数不依赖效果)"),
    "test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text": ("retired-positive", "改 native Step 的显式 query/fixed text；保留动态 target，不借 metadata flat 字段重建 legacy dispatcher。", "未验证精确后继"),
    "test_resume_legal_running_keeps_per_fire_client_message_id_stable": ("retired-positive", "不再调用已退役 _execute_job_action；保留同 Run/Step/target effectKey 和 provider 去重键断言。", "tests/test_scheduler_source_migration.py::test_explicit_host_adoption_continues_same_grid_and_effect_keys (相关 identity)"),
    "test_runs_and_next_and_run_now": ("retired-positive", "拆为 readonly 原 ID/runs 与 native manual admission/Creator nextFire；旧 run-now URL 应保持 410。", "tests/test_scheduler_history_read.py; tests/test_job_final_http_service.py::test_ft01_run_and_six_operations_use_same_wp02_identities"),
    "test_partial_failed_event_emitted": ("retired-positive", "以 native partial/uncertain effect + 唯一 outbox 验收，不恢复 start_broadcast 或重复 terminal notice。", "tests/test_job_final_runtime.py::test_fr05_single_outbox_ack_delete_guard_and_pin_cleanup; tests/test_job_final_independent_execution.py::test_fe02_success_unknown_partial_cannot_be_replayed"),
    "test_post_creates_scheduled_task_multi_entry": ("retired-positive", "用独立 Creator 多规则 + Job/Trigger 显式定义；保留各 ruleId/occurrence，旧 scheduled-task POST 不再合法。", "tests/test_job_final_creators.py; tests/test_scheduler_creator_management.py"),
    "test_post_creates_scheduled_shell_action_without_target_or_runner": ("retired-positive", "用 product_process native Action 参数和 independent execution intent；保留未执行前不分配实际 runner 的断言。", "tests/test_job_final_independent_execution.py::test_fe02_concurrent_admission_never_calls_legacy_create_retry"),
    "test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields": ("retired-positive", "迁 typed channel Action 参数/schema，保留 bot/target 持久及非自报授权。未连接真实 QQ provider。", "tests/test_job_final_contracts.py; tests/test_job_final_http_service.py"),
    "test_patch_scheduled_qq_action_is_strict_and_editable": ("retired-positive", "用 native Job revision 更新 typed channel arguments；保持严格 schema、编辑不更改历史 frozen plan。", "tests/test_job_final_contracts.py; tests/test_job_final_http_service.py"),
    "test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error": ("retired-positive", "通过 typed channel executor 保留明确成功/离线拒绝，不用 legacy kind dispatcher；精确 bot 业务效果需 provider 专项后继。", "tests/test_job_final_independent_execution.py (仅 typed receipt 相关)"),
    "test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure": ("retired-positive", "manual Run 不伪造时间 Event；显式 provider reject 与未知结果区分。旧 route 410 保持。", "tests/test_job_final_http_service.py::test_ft01_run_and_six_operations_use_same_wp02_identities"),
    "test_scheduled_qq_backlog_retry_runs_without_session_target": ("retired-positive", "不恢复旧 undelivered backlog；用同 Run/Step/effectKey 新 Attempt，unknown 必须查询。本次未证明 QQ 专属重试。", "tests/test_job_final_independent_execution.py::test_fe02_retry_keeps_job_run_step_effect_with_new_attempt_history (相关)"),
    "test_post_accepts_session_message_and_rejects_empty_schedule": ("retired-positive", "旧 kind POST 不合法；native Job 手动可无时间来源，Creator 空 rules 另行严格拒绝，不能照搬旧 combined schema。", "tests/test_job_final_contracts.py; tests/test_job_final_creators.py"),
    "test_post_requires_existing_session": ("retired-positive", "native API 的目标检查在对应 typed provider/业务层；保留无效 Session 不发生效果；精确业务检查需 Session provider 专项。", "未验证精确后继"),
    "test_patch_schedule_replaces_entries": ("retired-positive", "时间配置归 Creator，不在 Job PATCH 替换 schedule；Creator revision/CAS/anchor/cursor 回归继续验。", "tests/test_job_final_creators.py; tests/test_scheduler_creator_management.py"),
    "test_patch_schedule_rejects_bad_spec": ("retired-positive", "保留 invalid time config 的无写入拒绝，入口改 canonical Creator schema。", "tests/test_job_final_creators.py"),
    "test_patch_target_clear_enters_undeliverable": ("retired-positive", "旧 undeliverable/backlog 不再作为新执行状态；native 定义/入场严格拒绝无效必要参数，不能猜目标。", "tests/test_job_final_contracts.py"),
    "test_patch_text_updates_dispatch_body": ("retired-positive", "改 native literal/input binding；编辑只影响未来 frozen plan，历史不变。", "packages/web/src/views/AutomationWorkspace.test.tsx::preserves typed literals, stable step IDs, input/Event/output mappings and policies in the canonical Job"),
    "test_run_records_carry_entry_id": ("retired-positive", "旧 entryId 只读历史必须保留；新来源用 creatorId/occurrenceKey/driveId，不伪造旧执行记录。", "tests/test_scheduler_source_migration.py::test_canonical_retirement_keeps_original_identity_links_requests_and_event_history"),
    "test_pin_failure_keeps_disk_and_emits_nothing": ("stale-injection", "原 fixture 注入 background_jobs._atomic_write 已不在 JsonPins 实际写路径；保留原红，新增实际 coordinator.atomic_write 失败后继，证明 pins 字节不变、无广播、GET 仍 pinned。", "tests/test_job_retained_pin_successor.py::test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing (已实跑)"),
    "test_run_now_does_not_move_periodic_next_fire": ("retained-bridge-gap", "bridge 本轮保留；卡在退役 system_actions.execute 注入。需改非执行桥接 fixture 并保留 manual run 不推进周期/原 receipt 断言，不能把 bridge 整体删掉消红。", "tests/test_scheduler_source_migration.py (仅来源同网格续跑相关，未替代该 bridge 节点)"),
    "test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent": ("retained-safety-gap", "卡在退役 _reconcile_scheduled_processes。需 canonical late terminal receipt/cleanup 后继，明确原 parent ID 删除后不被重建；不能仅去掉最终 reconcile 调用。", "tests/test_job_final_independent_execution.py (仅 receipt/fence 相关；尚缺此精确删除后继)"),
    "test_real_owned_tree_delete_http": ("retained-safety-gap", "旧 start 退役导致未到实际清理；需 canonical owned independent intent + 真实 DELETE/retire cleanup proof、PID/birth/FILETIME、unknown 首次拒绝再确认的完整 HTTP 后继。现有真实 owned 树删除精确后继仍缺。", "tests/test_job_final_independent_execution.py (相关，不冒充完整 owned tree DELETE 验收)"),
}
rows = []
for label in ("baseline-red", "baseline-safety"):
    for test in ET.parse(ROOT / "evidence/r6i-resume" / (label + ".xml")).iter("testcase"):
        failure = test.find("failure")
        if failure is None:
            continue
        name = test.attrib["name"]
        base = name.split("[")[0]
        if base == "test_pin_metadata_failure_does_not_fail_committed_operations":
            detail = ("retained-metadata-gap", "原旧 POST 创建 fixture 先返回 503，尚未到损坏 pins 后已提交操作断言。需改 native/offline非执行 fixture，保留每个 kind/failure 分支的配置持久、单次广播、metadata unavailable 和不改损坏文件断言；本次未证明全部六分支。", "tests/test_jobs_completed_retention.py (相关保留策略，不替代这六项)")
        else:
            detail = specific[base]
        rows.append({"node": test.attrib["classname"].replace(".", "/") + ".py::" + name,
            "baselineFailure": failure.attrib.get("message", ""), "category": detail[0],
            "followup": detail[1], "successorEvidence": detail[2], "originalNode": "retained-failed"})
assert len(rows) == 41
current = {t.attrib["classname"].replace(".", "/") + ".py::" + t.attrib["name"] for t in
           ET.parse(OUT / "original-red-current.xml").iter("testcase") if t.find("failure") is not None}
assert {r["node"] for r in rows} == current
counts = {key: sum(r["category"] == key for r in rows) for key in sorted({r["category"] for r in rows})}
(OUT / "original-red-assessment.json").write_text(json.dumps({"counts": counts, "nodes": rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
text = ["# 原 41 条基线红测逐项处置", "", "原测试、断言、红 XML 全部保留，本文件不把相关证据等同精确后继通过。", "",
        "分类计数：`" + json.dumps(counts, ensure_ascii=False) + "`。retired-positive 需要按最终协议改写旧正向期望；其它保留能力必须补正确 fixture/精确后继，不能因旧入口退役而宣布能力已齐。", "",
        "| 原 node（本候选仍红） | 判断 | 后续与证据边界 |", "|---|---|---|"]
for row in rows:
    text.append(f"| `{row['node']}` | {row['category']} | {row['followup']}<br>相关/后继：{row['successorEvidence']} |")
(OUT / "original-red-assessment.md").write_text("\n".join(text) + "\n", encoding="utf-8")
print(json.dumps(counts))
