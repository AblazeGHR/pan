# Phase 5：真实隔离实例验证

候选工作树：`D:/project/pan-worktrees/job-chain-phase1-20261025`，分支 `feature/job-chain-phase1-20261025`，进入本阶段的 HEAD 为 `653e05537e2333f117e803dd209dbb5428e5a5f9`。本报告与修复一起提交，最终候选 SHA 见交付消息和 `git rev-parse HEAD`。

## 验证环境与运输路径

- `scripts/run_tests_isolated.py` 清除继承的 `PAN_*`/`PYTHONPATH`；测试自行分配 loopback 随机端口，并拒绝 8767/8768。
- `tests/support/job_chain_phase5_server.py` 复用仓库真实 uvicorn/Pan lifespan 启动器，Session、config、workspace、Jobs、Actions、Events、terminal registry 和日志指向 pytest 临时 profile。它只将本实例私有 IPC 地址写入本 profile，供实际 MCP 子进程使用。
- 实际 MCP 进程为 `python -m packages.mcp.server`，通过 MCP SDK 的 stdio client 初始化、发现 tools、调用原工具名。message/background 使用产品的私有 IPC→现有路由路径；scheduler 工具调用真实 HTTP。独立 HTTP 客户端也通过 TCP 查询真实 host。
- provider 端为仓库的确定性本地 stream CLI；测试验证 Pan 的进程、队列、交接和审计，不接入真实模型、QQ 或外部 provider。
- 浏览器是实际 headless Edge/Chromium，访问该实例 `/react/automation` 的生产 bundle，使用真实 HTTP/WS；没有 `route.fulfill`、ASGI client 替代 host 或 API response mocks。

## 已通过的端到端样例

| 样例 | 断言与证据 |
|---|---|
| 旧 message MCP 调用 | 参数仍为 text/schedule/target_session_id；所有 agent 四对象 grants 均为 false 时可用；返回统一 Action Job；一次 `system_session_message` Attempt；真实 queue 后隔离 CLI 收到一次正文及 agent prefix；原 ID 可 get，未来消息可 cancel；完成后的旧 nextRunAt 为 null |
| 旧 background MCP 调用 | 原 name/argv/cwd/target_session_id；统一 Job 自身启动 native Runner；记录集合无隐藏旧 Job；`processJobId == jobId`；一次 system_background_process Attempt；短进程与 Runner 的 PID/创建时间身份均已退出 |
| Scheduler 到期与单 owner | 原 scheduler_create 参数；显式 owner-switch 可重复；source/trigger/job 相互链接；到期后 Event=1、Trigger Run=1、system_scheduled_task Attempt=1；旧扫描与 Action inline plan 均未双投 |
| Scheduler 生命周期 | run-now 产生独立手工 Run 且 nextFireAt 不变；pause/resume 成功；list/get/runs 可用；once 的 nextFireAt 随 source cursor 变为 null；删除原 schedule Job 后移除其 managed Trigger，共享 source 保留浏览器链 Job 的唯一引用 |
| 正常重启 | 同一临时 profile stop/start；Event ID、原 Job ID、Attempt 数保持；MCP 原 ID 仍可查询 |
| 执行中的 host crash | native 命令已经写入 effect marker 后，仅 kill 本测试捕获的 host PID/创建时间身份；重启后同一 Job 为 uncertain、Attempt 仍为 1、marker 仍只有一行；retry 拒绝；原 PID/创建时间路径 cancel 成功；再次重启不重放；无残留进程且端口释放 |
| 不可映射旧记录 | 仅在停止后的临时 profile 写入带未知 schedule 字段的旧格式 fixture；owner-switch 返回 legacy_unmappable；字段保留、无新 source、仍由旧路径 pause |
| 浏览器创建与引用 | UI 创建 2 个稳定 stepId 的链，选择既有 Scheduler；保存后通过真实后端执行两步，均 completed；Job 展示 Scheduler/Trigger 链接；Trigger 展示拥有 Job；Event Register 展示真实 source 与两个引用。1440/390 各 5 张截图，共 10 张；无页面异常、无 document/panel 横向溢出 |

## 修复

| 文件/符号 | 修复原因 |
|---|---|
| `packages/web/server.py::_legacy_mcp_authority` | 新权限开关错误地拒绝旧 MCP 默认调用；恢复既有 Session 门禁，审计记录可信 legacy-mcp 来源 |
| `packages/core/background_jobs.py::start_action_process`；`system_actions.execute` | 兼容 background Action 不再偷偷创建第二个旧 Job；同一 Job 使用原 native Runner 和取消路径 |
| `background_jobs.reconcile_running/runner_update` | 旧 reconciler 不接管 Action 准入；真实 Windows redirector 启动期使用持久 spawn 时间标记防止过早判 orphan；晚到 native receipt 作为 processOutcome 保存，不能覆盖 recovered uncertain Action 结果 |
| `legacy_compat.config_for/legacy_view`；旧 message 扫描 | 旧 schedule 字典与新执行列表分离；旧扫描跳过 Action Job；旧 nextRunAt 使用当前执行游标；单目标 broadcast 仍保留列表身份；send_session 保留原 source metadata |
| `Runtime.tick`；`TimeEvents.tick`；`LegacySchedulerBridge.project_cursor` | event owner 禁止 inline 双扫描；source cursor 提交后更新旧 nextFireAt/lastFireAt 投影，重启可修复投影；once 耗尽后 Job 收敛终态 |
| `LegacySchedulerBridge.update/delete`；`scheduler.api._legacy_update` | 迁移后的引用仍能取回原 scheduled Action；paused 与 enabled 独立保存；未迁移任务继续旧更新路径；删除复用通用 managed cleanup，保留共享 source |
| 旧 retry/cancel 适配 | unknown/uncertain retry 拒绝；host 重启后无内存 waiter 的 native process 仍使用原身份校验取消 |

## 验证计数与证据

最终计数：真实 E2E **2 passed**（34.573 秒）；后端回归 **525 passed、1 skipped，526 collected**（89.277 秒），均 exit 0。唯一 skip 是已有 `test_cron_honours_timezone_argument`，本机缺少 ZoneInfo 时区数据库。`git diff --check` 通过。机器可读计数在 `artifacts/job-chain-phase5/manifest.json` 和两个最终 JUnit 文件。精确命令在 `artifacts/job-chain-phase5/commands.txt`；日志中包含依赖的 Pydantic/Starlette 弃用告警，未据此扩大修复范围。

最后将 Runner 启动时间标记与 Action process admission 放进同一次持久写，封住准入和 spawn 之间的 reconciler 窗口；随后针对 owner、background 和两条真实 E2E 复验，**58 passed**（37.177 秒），结果见 `final-owner-check.xml` / `final-owner-check.log`。这些是重复/补充验证，不能与上面的用例数直接相加当作不同测试数。

- `e2e-final.xml` / `e2e-final.log`：两个真实流程用例。
- `backend-regression.xml` / `backend-regression.log`：受影响的 background/jobs/automation/scheduler/authorization 回归；包括现有真实 HTTP recovery 用例。
- `http-stdio.json`、`crash-restart.json`：实际身份、Event/Trigger/Job/Attempt、权限、重启、marker 和进程清理断言。
- `server.log`、`crash-server.log`：两个隔离 host 的日志。
- `regression/`：现有真实 HTTP 回归产生的本次日志副本；原历史 artifact 文件恢复到阶段基线，避免把随机 ID/profile 变化混入旧证据。
- `browser/browser.json`、10 张 PNG、`browser.log`：浏览器版本、视口、几何、实际 Job ID 与截图清单。`red/` 保留早期浏览器 harness selector/查询地址错误时的页面，不能作为产品缺陷或最终截图。
- 前端产品源码未修改；本阶段浏览器脚本使用现有生产 bundle，因此未重复 tsc/build/资产生成。

## 精确边界

1. 本阶段没有操作 main/practical/integration，没有 push、远端 CI、部署、真实数据迁移、真实 profile 或 8767/8768 服务。
2. 真正 kill host 的 crash 点是 native effect 已开始、Action Attempt 未终结时。Event durable admission 与 publish cursor 之间的精确 crash 点由既有 `test_scheduler_time_events.py` 回归覆盖；本阶段没有在真实 host 的那两个磁盘提交之间注入 kill，也没有模拟断电/磁盘损坏，或在 owner-switch/update 的每个激活写入点注入 crash。
3. 未验证真实 provider/QQ 回执、scheduled shell/QQ/resume_legal_running 的外部全流程、生产数据规模、所有时区/DST 与 misfire/grace/pending-undelivered 组合、旧 Jobs panel 的全部编辑/取消操作。时区数据库缺失造成的已有 cron 时区测试 skip 单独记录，不算通过。
4. 本阶段样例通过不代表所有历史合法 shape 已获端到端证明；weekly 秒精度、未知历史字段、多目标 provider 结果等仍需单独审查。`PHASE4B_COMPLETION_MATRIX_20261025.md` 的整体 §8 状态继续是 partial。
5. 无阻塞产品决策，未创建 interaction_card，card IDs：无。
