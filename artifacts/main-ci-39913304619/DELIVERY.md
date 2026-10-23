# CI 39913304619 修复候选

- 原受测源码：`0156f008b149d59641ccc0176721c8e2b5ae59d8`。
- MA 指定并已纳入的 main：`c277d76ab7dbdaffc5947984756290762cbf5166`，保留 Windows setup/start 自动注册及取消 Git 分支限制功能。
- 最终受验源码：`f36592ece507ff708f9b65a66bafff36b863dda5`。
- 分支：`fix/main-ci-39913304619`；工作树：`D:/project/pan-worktrees/main-ci-39913304619`。
- practical：`2652ac8e6d0ff312a255666bc5e459890346e6e5`，未移动。main 仅由 MA 先前更新，本 TA 未移动。

## 原始 CI 证据

Run：https://github.com/AblazeGHR/pan/actions/runs/39913304619

| Python | Job | 总数 | 通过 | 失败 | skip |
|---|---|---:|---:|---:|---:|
| 3.12 | 120281027493 | 5507 | 5433 | 13 | 61 |
| 3.14 | 120281026974 | 5507 | 5434 | 12 | 61 |

唯一持久收集 Job `job_0dd23a9ef01a078ab80e489c` 完整收集两腿日志、JUnit 和 artifact 元数据，退出 0。原始日志及 JUnit 随证据归档保留。两腿合并为 14 个不同失败身份；前期消息中的 15 是计数错误，已纠正。

## 全部失败身份与去向

下列缩写均为 `tests.<文件名>::<测试名>`，方法保留类名。

| 身份 | 分类与处理 |
|---|---|
| test_docs_terminology::test_no_banned_legacy_terms | 文档规范回归：规范 API 文档中的旧称呼改为 TA，不改检查规则。 |
| test_eta_final_combined::test_node_query_pause_production_ui_restart_and_delay_mcp | 旧 crash fixture：不能把成功 Run 的终态 attempts/result 复制成 queued；构造对应的未开始 attempt 状态。保留 running -> uncertain 与不重放检查。 |
| test_eventloop_history_offload::test_http_policies_stream_in_the_request_task_without_body_relay | 产品回归：新增 BaseHTTPMiddleware 引入额外 task/stream；改为直接 ASGI middleware，在 response.start 前 reconcile，保留原 request task 和响应字节。新增写入时序检查。 |
| test_interaction_card_http::test_http_mcp_user_response_and_directed_trigger | 旧 synthetic fixture：补真实固定目标 Action/Trigger 定义，走 production trusted-choice resolve/verify；不绕过可信绑定。 |
| test_job_unified_http::test_unified_event_system_actions_legacy_and_production_ui | 浏览器测试同步：实例化 Template 仅出现列表行尚未证明完成；等待真实 Job completed 后，仍要求 registry 无 jobExecutions snapshot。 |
| test_jobs_completed_retention::test_log_cleanup_is_independent_safe_and_only_removes_expired_owned_logs | 旧 fixture：starting background Job 补合法 target/argv/cwd，保留真实 admission/snapshot 清理。 |
| test_locker_runtime_http::test_product_locker_login_choice_pause_restart | 旧通知断言：解析当前结构化 JSON，精确断言 cardId/responseKind/summaryBody/choiceId；仍查只有一次提交/拒绝通知，无删除通知、正文泄漏或重放。 |
| test_mcp_handbook::TestDescriptionCallChain::test_every_tool_ends_with_pan_skill_pointer | 描述规范回归：补 interaction_card、agent_inbox、job_config_drafts 缺少的 /pan 指针，保留全工具检查。 |
| test_plugin_event_lifecycle::test_real_peer_self_cleanup_without_watch_cycle[reader_cancel] | 产品退出确认缺口：先捕获自有进程树，根进程退出后等待这些已捕获进程结束。20 次修前诊断曾捕获 1 次 cleanup 返回时自有 conhost 仍 running；保留 PID/创建时间和命令证据。 |
| test_plugin_qq_integration::test_qq_job_and_outbox_continue_when_session_tools_are_disabled | 旧 fixture：通过持久 Job admission 再执行原 dispatch key，追加 snapshot cleaned 与原 dispatch key 检查。QQ 全部为 fake upstream，不使用真实账号。 |
| test_plugin_session_config::test_legacy_scheduled_qq_action_is_unchanged_by_plugin_catalog | 同上：补持久 Job 身份，不绕过 immutable arguments、attempt receipt 或旧 outbox 不重发语义。 |
| test_takeover_lifecycle_http::test_http_first_terminal_survives_real_reader_conflict_without_resend | fixture 冲突：fake CLI 默认 UTF-8 重配置覆盖了该测试明确设置的 GBK；takeover probe 保留窄编码。另将真实 reader-conflict 注入移到 atomic replace 边界，保持一次 sharing violation、一次 handoff、结果 cursor 和原时限；超时时保存 owned asyncio task stacks。 |
| test_user_locker_browser::test_locker_browser_http_mcp | 与 interaction_card_http 共用的 synthetic fixture 补可信 choice 绑定；真实隔离 Chromium 路径保留。 |
| test_automation_retry_http::test_node_parallel_finite_retries_and_uncertain_confirmation_http | 同类产品退出确认缺口：Action cancellation 等待已捕获自有进程树。远端未保留残留 PID 现场，不能断言当次具体剩余者就是 conhost。清理未确认归为 uncertain，不自动 retry；保留 CancelledError，增加确定性红绿回归。 |

## 验证与边界

- 本地使用 canonical `D:/project/Pan-main/.venv/Scripts/python.exe`（Python 3.14.5），仅临时 profile、随机非 8767/8768 端口、fake CLI/loopback upstream、隔离 Chromium。
- 前端依赖安装到本树，pnpm store 在本树 `.validation/pnpm-store`。原 SHA 的 frozen install/build 成功；其准备 Job 后续在 PowerShell 5 JSON 解码失败、退出 1，未冒充 pytest 结果。后改 Python 读取清单，得到原 SHA 红测 14 项：11 failed / 3 passed / 0 skip，退出 1。
- 原两腿各有 61 个既有 skip；逐项身份、原因保留在原 JUnit 和 junit-summary。主要是 43 个停用 oneshot、机器本地 probe/workdir、显式安装/任务注册 opt-in、zoneinfo 与 Windows ancestor Job 限制。本次未新增 skip/xfail。
- 最终精确 SHA 的 JUnit、退出码、14 个原失败身份覆盖、20 次进程退出结果由 `validation.json` 记录。未执行候选远端 CI、完整 5507 项本地矩阵或本地 Python 3.12 全套；不能将相关集通过称为双矩阵 CI 通过。
- 额外异常分支红绿：`cancellation-red.xml.gz.gz` 为 1 failed / exit 1；`uncertainty-green.xml` 为 144 passed / exit 0。未确认清理即使处于幂等 Action + enabled retry，也只产生 1 个 uncertain attempt，不自动重投。
- 先前 `f3d2bc55` 的 503 项结果包含 1 次 takeover terminal 等待超时；原始 JUnit 和临时 profile 的相关 metadata/history/fake-CLI 证据保留。独立重复 10 次未复现；因此将冲突注入收敛到 actual replace 边界，而不声称已证明该次超时的唯一原因。
- 关机中断 Job `job_d9222ff7306e37e46ef2836c` 退出 `1073807364`，部分运行不计为通过。用户明确要求重跑后，通过原 Job retry 创建 `job_d9c99dc334726253abeffa0c`；原日志保留。
- 未删断言、放宽规则/时限或新增 skip/xfail。Queue 原 ID/unknown 不重投、Pause/Illegal/lease/owner/generation、Locker 可信绑定、Job snapshot 和 practical 旧数据兼容由相关契约测试覆盖。
- 无 push/release、无真实 provider/QQ/账号操作、无共享依赖写入、无现有服务重启；未扩展 OAuth/private exporter。TA 候选仍需 MA 独立验收。


## 最终验收事实

- 精确源码 `f36592ece507ff708f9b65a66bafff36b863dda5`：503 项，**502 passed / 0 failed / 0 error / 1 既有 skip**，pytest 与持久 Job 均退出 0；耗时 273.880 秒。
- 两腿原始 JUnit 的 5507 个测试身份多重集一致；合并后的全部 14 个失败身份均在最终相关集中通过。
- 20 次自有树退出检查全部通过，cleanup 返回时未发现剩余已捕获进程。最终 Job Registry 同时记录 processTreeEnded=true。
- 最终相关集唯一 skip 为 gitignored 的本地 skill 同步副本不存在；未新增 skip/xfail。
- 编译语法检查、Node `--check` 和源码 `git diff --check` 退出 0。此前本树冻结依赖构建成功；本轮无前端产品源码改动，真实 Chromium 已在相关集中执行。

## 证据入口与复核

- `validation.json`：精确 SHA、双矩阵原始统计、全部 14 个失败身份的最终去向、退出码与覆盖声明。
- `red.xml.gz` / `focused-green.xml`：原 SHA 本地红测与最终源码相关集绿测；原矩阵 JUnit 是 `pytest-results-3.12.xml.gz` / `pytest-results-3.14.xml.gz`。
- `job-120281027493.log.gz` / `job-120281026974.log.gz`：完整原矩阵日志；`final-job.log.gz`：最终持久 Job 完整输出。
- `junit-summary.json`：原失败 traceback 与全部既有 skip 身份/原因。
- `collection-and-experiments.zip`：收集元数据/脚本、红绿及中间失败 JUnit、原 Job 日志、关机中断日志和单次 takeover 超时的自有 profile 证据；没有删除失败实验。
- `owned-runtime-evidence.zip`：本轮隔离 Chromium/HTTP 生成物以及进程退出诊断；其来源均为临时 profile，不是生产数据。
- `SHA256SUMS.json`：交付文件校验和。源码提交之后仅新增交付证据，不改源文件。

重做相关集可使用证据 ZIP 中的 `focused.py` 与 `process_probe.py`，放回本树 `.validation/ci39913304619/` 并保持 `junit-summary.json` 同目录，执行：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe D:/project/pan-worktrees/main-ci-39913304619/.validation/ci39913304619/focused.py
```

长命令应由执行者自己的持久 Job 运行并接收终态；不要直接轮询工具。预期 pytest 503 项、502 passed / 1 skip，随后 20 次进程检查，最终退出 0。MA 仍需独立验收，候选远端双矩阵尚未运行。
