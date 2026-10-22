# 第一检查点发布候选验证与失败去向

## 源码范围

基线为 `ee28c59606fd39f7293f57d850db2ecd51cc4370`；`50113c48554567a383fcbba02ac61de658d2b663` 修复 selected Session action harness 的 generation 与两份 store；`26f8bcc799bb74b95525de8832ec8df24229c8dc` 为阻断补修。本次后继提交只补 v0.4.0 元数据、CHANGELOG 和发布/验证说明，最终完整 SHA 由交付报告记录。

本地 main/practical 未由 TA 推进；没有新 push/tag/release，没有操作现有服务、8767/8768 或真实业务。证据仍在原隔离树 `artifacts/runtime-owner/` 与 `artifacts/release-checkpoint-1/`，不纳入产品提交。

## 远端失败与后继定向证据

[run 39733897751](https://github.com/AblazeGHR/pan/actions/runs/39733897751) 对应 ee28：每矩阵 4764 项，3.12 为 4685 passed /20 failed /59 skipped；3.14 为 4684 passed /21 failed /59 skipped；均零 error，两套 React build 成功。完整证据：`ci-full.log`、`ci-logs.zip`、`ci-artifacts/pytest-results-{3.12,3.14}/pytest-results.xml`。59 个 skip 的原始身份和原因在 `junit-analysis.json`，未删除、未转为新 skip。

修前本地 15 文件为 211 passed /14 failed /1 skip（其中缺 Python Playwright 属环境失败）；补齐独立环境后的中间 18 文件为 243 passed /3 failed /1 skip。最终两版本同 18 文件各 247 项，246 passed /0 failed /0 errors /1 skip，两个持久 Job 均 exit 0。原始命令见 `repro_final.py` / `repro_314.py` 和 `validation_final_job.py` / `validation_314_job.py`；环境为 uv 独立 requirements、pytest/pytest-timeout、Playwright 1.63.0，子进程通过 `scripts/run_tests_isolated.py` 隔离。JUnit 为 `ci-repro-final.xml` / `ci-repro-314.xml`，日志同名 `.log`。既有 skip 唯一身份为 `tests.test_docs_terminology::test_skill_sync_copy_matches_source`（以 JUnit 真实记录为准），原因是 gitignored 本地同步副本不存在。

以下保留远端每一个失败身份；“本地通过”只表示相同 node ID 在对应最终定向 JUnit 中无 failure/error/skip，不能替代后继最终远端 CI。

| 远端失败身份 | 失败矩阵 | 去向 | 后继本地证据 |
|---|---|---|---|
| `tests.test_agent_notify::test_notice_to_self_enqueued` | 3.12, 3.14 | 故障注入/观测移至当前 _save_body 原子 writer；失败候选不发布，检查空队列/ledger 及无元数据文件。旧 mutable-before-save 预期已被原子事务契约取代。 | 对应矩阵同身份通过 |
| `tests.test_agent_notify::test_notice_save_failure_keeps_queued_item` | 3.12, 3.14 | 故障注入/观测移至当前 _save_body 原子 writer；失败候选不发布，检查空队列/ledger 及无元数据文件。旧 mutable-before-save 预期已被原子事务契约取代。 | 对应矩阵同身份通过 |
| `tests.test_auto_force_http_e2e::test_real_report_auto_force_steers_manager_report` | 3.12, 3.14 | 报告场景显式启用 user 自动报告策略；默认关闭策略与产品代码不变。 | 对应矩阵同身份通过 |
| `tests.test_auto_force_http_e2e::test_real_job_terminal_auto_force_steers_system_notice` | 3.12, 3.14 | Job fixture 补必填 name；保留真实终态/锁定/Steer 断言。 | 对应矩阵同身份通过 |
| `tests.test_auto_force_ui_e2e::test_auto_force_settings_ui_real_chromium` | 3.12 | 保留原 history/queue/Steer 断言和 20 秒预算；等待并检查同一份队列快照，避免多次 GET 跨回执写入窗口。 | 对应矩阵同身份通过 |
| `tests.test_delayed_restart_locked_queue_http::test_plugin_configuration_and_delayed_restart_share_lifecycle[plugin-first]` | 3.12, 3.14 | 报告场景显式启用 user 自动报告策略；默认关闭策略与产品代码不变。 | 对应矩阵同身份通过 |
| `tests.test_delayed_restart_locked_queue_http::test_plugin_configuration_and_delayed_restart_share_lifecycle[manual-first]` | 3.12, 3.14 | 报告场景显式启用 user 自动报告策略；默认关闭策略与产品代码不变。 | 对应矩阵同身份通过 |
| `tests.test_docs_terminology::test_no_banned_legacy_terms` | 3.12, 3.14 | 修正规范主文档及 HTTP 引用页的 TA 术语，不改扫描规则。 | 对应矩阵同身份通过 |
| `tests.test_eventloop_history_offload::test_http_policies_stream_in_the_request_task_without_body_relay` | 3.12, 3.14 | 产品修复：CLI guard 改直接 ASGI，保留实例校验且不创建普通请求 task/stream relay。 | 对应矩阵同身份通过 |
| `tests.test_illegal_queue_wake_http::test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog[wake]` | 3.12, 3.14 | 假 CLI 明确 UTF-8 stdio；cp1252 修前中文损坏已保存，新增确定性回归；保留 Wake/锁定/ready 断言。 | 对应矩阵同身份通过 |
| `tests.test_illegal_queue_wake_http::test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog[manual-unlock]` | 3.12, 3.14 | 假 CLI 明确 UTF-8 stdio；cp1252 修前中文损坏已保存，新增确定性回归；保留 Wake/锁定/ready 断言。 | 对应矩阵同身份通过 |
| `tests.test_illegal_queue_wake_http::test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog[start]` | 3.12, 3.14 | 假 CLI 明确 UTF-8 stdio；cp1252 修前中文损坏已保存，新增确定性回归；保留 Wake/锁定/ready 断言。 | 对应矩阵同身份通过 |
| `tests.test_illegal_queue_wake_http::test_sticky_messages_wait_for_explicit_action_and_wake_precedes_backlog[safe]` | 3.12, 3.14 | 假 CLI 明确 UTF-8 stdio；cp1252 修前中文损坏已保存，新增确定性回归；保留 Wake/锁定/ready 断言。 | 对应矩阵同身份通过 |
| `tests.test_illegal_queue_wake_http::test_pan_and_long_job_crash_notification_stays_locked_until_wake` | 3.12, 3.14 | Job fixture 补必填 name；保留真实终态/锁定/Steer 断言。 | 对应矩阵同身份通过 |
| `tests.test_long_run_persistence::test_same_client_message_and_task_id_concurrently_create_one_item` | 3.12, 3.14 | 20+20 并发尝试保留；内部 lock-held _persist_task_item 按真实调用契约持有 Session queue lock。 | 对应矩阵同身份通过 |
| `tests.test_long_run_persistence::test_failed_receipt_save_rolls_back_index_and_allows_retry` | 3.12, 3.14 | 在实际 _save_body 注入首次失败，保留 rollback、retry 和 index 唯一性断言。 | 对应矩阵同身份通过 |
| `tests.test_mcp_handbook.TestDescriptionCallChain::test_every_tool_ends_with_pan_skill_pointer` | 3.12, 3.14 | 两个 delayed-restart 工具说明补 /pan skill 尾部指针。 | 对应矩阵同身份通过 |
| `tests.test_restart_interrupt_cbc_e2e::test_cbc_interrupt_kills_old_process_without_replaying_task` | 3.12 | running 早于 handoff：等待假 CLI 实际读取标记与队列回执后 interrupt，保留不重放断言。 | 对应矩阵同身份通过 |
| `tests.test_source_metadata::test_send_session_accepts_future_external_sources` | 3.12, 3.14 | 按已存在 ancestry 分类契约，非上游发送者规范为 agent；sourceSessionId 继续校验。 | 对应矩阵同身份通过 |
| `tests.test_wake_ready_integration_http::test_explicit_wake_retains_old_locks_until_ready_and_on_startup_failure[False]` | 3.12, 3.14 | 假 CLI 明确 UTF-8 stdio；cp1252 修前中文损坏已保存，新增确定性回归；保留 Wake/锁定/ready 断言。 | 对应矩阵同身份通过 |
| `tests.test_action_groups::test_command_timeout_retires_owned_descendants` | 3.14 | 进程在 PID/身份查询间退出：接受 NoSuchProcess，保留同身份存活失败及 owned 清理。 | 对应矩阵同身份通过 |
| `tests.test_job_lifecycle_prevention::test_job_attribute_failure_prevents_creation_and_deletes_list[legacy]` | 3.14 | 本地 3.14 原场景通过，远端 guard 启停失败未复现，未做推测性修改；保持最终 CI 阻断核验。 | 对应矩阵同身份通过 |
| `tests.test_queue_send_latency::test_send_ack_does_not_wait_for_direct_steer_stdin_drain` | 3.14 | 本地 3.14 原阈值与阻塞 drain 场景通过，未放宽 0.2 秒预算；最终远端仍需验证。 | 对应矩阵同身份通过 |

## 验证边界与剩余门禁

- 没有新增 skip/xfail、移除失败测试或降低原负载。原子 notice 故障断言更新的是已经改变的事务契约；不是把失败候选当作已接受消息。并发私有原语补 Session 排他，与产品调用点一致。
- 3.14 window guard 启停失败未本地复现，延迟用例仅确认本地原负载通过；不宣称已证明所有远端时序失败根因。最终双矩阵失败仍阻断发布。
- Worker/selected-actions 的历史 38 Vitest、typecheck、定向 lint、三视口 Chromium 证据属于 50113；ee28 的 27 Python 短冒烟、17 Worker store 和 production build/76 预压缩资产属于旧 SHA。不得把它们写成新版全量成功。
- 本次仅版本/文档变更，不机械重跑全量本地或前端 build；版本 CLI/诊断、文档和一致性检查由本轮交付报告列出退出码。
- MA 独立验收完整候选后才可安全推进 main/practical、完成 practical build、冻结精确 SHA，再推最终 CI。Python 3.12/3.14 两个远端全量均必须阻塞通过并收完整日志/JUnit，完成短冒烟后才由 MA tag/release。
- ETA、持久 Event Store、query、serial/parallel、有限重试及对应 UI、Job Template 和自动换号均不在本检查点。
