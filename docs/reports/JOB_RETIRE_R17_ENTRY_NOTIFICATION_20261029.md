# JOB-RETIRE r16/r17：实际 MCP 接线与通知/积压行为证据

这是有明确未闭合项的候选，不是旧功能完整迁移、旧宿主删除、上线或用户验收结论。用户功能保全硬约束已纳入本报告：未经用户明确允许并更新计划，缺失/削减/降级不可验收；D6 既存实例保留不豁免能力覆盖。target 冻结与积压改投的冲突已暂停受影响实现和验收，交 MA 向用户寻求决策。

工作树 `D:/project/pan-worktrees/job-entry-notification-closure-20261029`，分支 `r6/job-entry-notification-closure-20261029`。基线 `4b577633d2d9ffcdc5bf8a44b12836724c18804e`；MCP 首次实现/验证 `cd8e04b5ca05265612b0e2a45169d36c07d169ab`；最终测试源码 `ee629b3c2c4303325c26ea7b855592de9ffb29f5`。其后提交只封存报告和证据，不修改产品和测试源码。原 r15 树不变，所有 r6–r15 原红、原映射保留。

## 本批已修复的准确范围

产品仅修改 `packages/mcp/server.py`：`PanMCP.call_tool` 在实际 FastMCP 请求边界连接已有 `legacy_mcp_compatibility`；新增精确 `LEGACY_MCP_GUIDANCE`，修正两处仍推荐旧写工具的说明。没有注册旧写工具，也没有增加旧 dispatcher、HTTP fallback、执行器或账本。

已检查指定 venv 内 mcp 1.28.1 源码：公开 `FastMCP.call_tool` 可由子类覆盖，库自己的 `_setup_handlers` 注册 `self.call_tool`，支持直接返回 `CallToolResult`。版本、源码哈希和扩展入口在 `mcp-extension.json`。

实际子进程 `python -m packages.mcp.server` 经过 initialize / initialized / tools/list / tools/call：10 个旧写名不可发现，尝试调用得到 `isError=true`、`structuredContent.error.code=capability_unsupported` 和具体 canonical 操作提示；9 个保留读/取消工具仍可发现。验证了原未知工具错误不变、malformed 参数拒绝、真实 IPC 权限拒绝/授权后 canonical 创建及原 operation 身份查询、保留读/取消入口。旧写尝试没有 HTTP、IPC、operation、Run、effect 或新 Worker。canonical 写仍要求现有权限、requestKey 和 CAS。

这修复的是实际迁移提示接线。**unsupported 提示不是原写操作的可执行等价后继，也不能据此宣布旧功能保全完成。** 旧参数若缺少 canonical 身份/权限/CAS，当前仍不执行；是否要求兼容旧调用形状及其完整生命周期，属于需 MA 汇总的剩余能力边界。

## 不可变源码上的实际命令与计数

解释器 `D:/project/Pan-main/.venv/Scripts/python.exe`，Python 3.14.5；本批未安装或切换环境。完整版本在 `environment.json`。所有测试通过 `scripts/run_tests_isolated.py`，使用合成根、暂停的临时 Session、自有随机端口。

| 证据 | 测试源码 SHA | 计数 | exit | 耗时 |
|---|---|---:|---:|---:|
| `baseline-mcp.xml`，新增实际 stdio 测试运行于原 server 字节，运行后恢复 | 4b577633 | 0 pass / 1 fail | 1 | 见原日志 |
| `mcp.xml`：实际 stdio + r12 + authorization MCP | cd8e04b5 | 17 pass / 0 fail / 0 skip | 0 | 9.521s |
| `behavior.xml`：首批真实 HTTP/Event/队列/通知 | cd8e04b5 | 9 pass / 1 fail / 0 skip | 1 | 22.755s |
| `accepted-delta.xml`：Session 固定目标、QQ 写校验、原插件/手动重试 | cd8e04b5 | 26 pass / 4 fail / 0 skip | 1 | 153.041s |
| `r17-mcp.xml`：最终实际 stdio + r12 + authorization MCP | ee629b3c | 17 pass / 0 fail / 0 skip | 0 | 9.289s |
| `r17-behavior.xml`：r16 行为 + r17 积压 + 真实进程完成通知 | ee629b3c | 15 pass / 4 fail / 0 skip | 1 | 41.599s |

最终同 SHA 矩阵合计 **32 passed / 4 failed / 0 skipped，exit 1**，不把之前重复执行计入通过总数。完整 argv、逐 node、退出码和耗时在 `rev17-commands.json`；可重复命令：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe evidence/job-retirement-rev16-20261029/run_rev17.py
```

脚本记录当前 immutable HEAD，执行前后检查 tracked tree 没有变化；已知红项仍会真实 exit 1，不是固定 gate 结果。

源码/报告/JSON 的 staged `git diff --check` 为 exit 0；包含原样 pytest log/XML 时为 exit 2，仅是原失败 traceback 自带的 `E ` 空白行，保留原始证据，未改写清洗为绿（`raw-whitespace-check.log`）。

原 4 项 QQ 重验失败完整保留：selected group 成功节点和三种 bot 的冷重启手动 retry 节点。selected 单节点不带诊断也曾复现失败；同一产品字节下，后来仅加只读 pytest 失败观察插件的 selected + default retry 两个原节点均通过（`r17-qq-readonly-diagnostic.xml`，2 pass，exit 0）。未改旧断言/QQ 产品/fixture，**没有找到根因或据此宣布修复**，当前分类为重验不一致。独立观察测试也曾成功，不能擦除失败矩阵。其辅助执行当时新增测试文件未提交，不混入 ee629b3c 的最终计数。

## 已验证的当前行为及四个红项

实际 HTTP 改写 once → cron + interval 后删除原 cursor；旧时刻无 Run/入队，新规则真实 Event 的规则/Creator revision 正确且仅一个实际队列 effect。空规则和错误 cron 的创建/更新无写入。source disable 阻止未来事件；Job disable/retire 阻止新准入但不取消已有 Run；显式取消排队 Run 才阻止其 effect。冷重启不重复效果。

实际 fanout 一个目标已入队、另一个在准入后通过真实存储删除，产生有序 succeeded/rejected 目标回执、parent partial、Run uncertain；真实 `worker.enqueue_notice` 只入队一次，outbox ack 后冷重启不重投。**红项 1**：`test_partial_failure_terminal_notice_identifies_actual_failed_target`，parent 摘要只有 runId/status，没有原 `job.partial_failed.errors` 要求的失败目标。最小候选位置是 `FinalService._settle` 读取已有 per-target effect 回执构造摘要，及现有 WS 转发如何保留原可见错误；未获共享修改授权。

**红项 2**：`test_pending_event_pause_does_not_consume_original_backlog`。当前动态输入实验中，目标 null 的 Event 留在未准入状态，cold → 修改当前 Job 默认目标 → 原 Event 一次入队可成功；Job disabled 期间也能保留。但 Trigger disable 后 `FinalService._admit_event` 返回 skipped，`tick` 写入 final_event_receipts；恢复后原 Event 永久不再准入。这个实验不是生产 clear-target 接线或完整旧积压生命周期后继；该政策边界仍待用户决策。

**红项 3**：`test_known_missing_notice_target_retains_pending_then_cold_restoration_delivers`。真正 `worker.enqueue_notice` 返回 `session_not_found`，测试记录明确的本地未尝试拒绝，再由自有 sink 返回 rejected/attempted=false；`deliver_outbox` 把任何非 succeeded 都变 uncertain，不再读取。生产 `web.server` lifespan 内 `send_final_notice` 更早把同一个真实拒绝映射成 unknown，源码与实际本地拒绝组合证明分类缝隙，尚未运行完整生产 lifespan E2E。不能直接信任任意 sender 自报 attempted=false；修复需要真实本地 producer 的身份绑定事实。相反，另一测试先真实入队再丢回执，确认 uncertain 且 cold 零重投，这才是外部效果不确定语义。旧 r16 `test_actual_missing_notification_target_unknown_receipt_cold_no_resend` 只是**现状观察**，不再被当成应该吞掉积压的保全证明。

**红项 4**：`test_actual_detached_completion_notice_retains_original_exit_log_details`。真实 canonical detached process/log Action 执行一次并产出 exitCode/logRef，terminal Session outbox 摘要丢失详情。独立正向测试已证实真实进程完成后 cold → 同原 outboxId 实际持久化通知一次，重复查询/dispatch/delivery 不重复进程或通知。所用 sink 复用真实 enqueue，与 server lifespan 的业务方法一致；未把自有 sink 当成生产 WS/UI 已接收证据。

## 旧链路、三种身份和必须提交用户的决策

`old-current-callchains.json` 封存 9b4506、68be5bd4d^ 和当前源码的 13 个准确函数/行号/字节 SHA；只作源头证据，不作运行或迁移证明。

旧 scheduled `_backlog_undelivered_fire` 保存 entryId/fireAt/dispatchKey/text/error、推进正常日程；`_redeliver_undelivered` 每条发送前重读当前 target/enabled/paused，保留缺目标条目，发送成功后在最新记录中只删确认条目，保留并发新增。注意旧发送调用将 current record 交给 `_run_job_action`，note.text 的保存本身不能证明重投使用旧文字，不擅自推断。旧代码把部分非 error 状态视为 delivered，也不授权重放现在的 unknown。

独立进程完成是另一条链：`independent_runner.run` 同事务落 terminal 状态和 `jobId:terminal` outbox；旧 `pan_bridge._recover_legacy_events` 只有真实 enqueue ok 才 ack，因此已知未入队可以继续保留。legacy terminal recovery/WS 广播有各自去重，不是 scheduled backlog。当前 canonical 由 ExecutionStore 原 effect/Attempt 回执 → `_settle` → final outbox/真实 Session notice；本批没有运行旧 writer 或用历史行投影冒充 canonical 进程执行。

请 MA 向用户提交以下冲突并更新计划后再推进受影响部分；以下是选择范围，**不是 TA 自定裁决或已批准方案**：

1. 旧“准入前缺目标继续积压、每次 reservation 使用当前目标”能否正式映射到已有持久化 Event/Trigger 准入前阶段？本批原 Event 保留实验表明不需要新账本，但生产字段映射、paused Trigger 不消费 pending、并发 append/retarget 完整行为尚未闭合。候选最小符号为 `FinalService._admit_event/tick` 的 receipt/skip 区分及明确的既有表单 adapter；需要与 recovery TA 协调，不能改已冻结 Run。
2. 已准入但尚未发送的 frozen target，与用户“改投积压”的边界如何划分？保持 frozen effect 或允许显式重新绑定的具体条件、原身份和审计语义必须用户决定。现状冻结观察不是政策验收，不以新 Run 伪装原 pending identity。外部 unknown/partial 仍不可自动重投。
3. 已知本地未尝试的 terminal 通知怎样保留待投、是否允许改投当前目标？既有 outboxId/recipient/原 authority 可复用；最小候选符号为 `web.server` 内 `send_final_notice` + `FinalService.deliver_outbox` 的可信本地未投递分类，不能全局将 rejected 当可重试或修改已发送 target。通知目标取 frozen notification policy 的现状也需与用户改投要求一起裁决。
4. 原 partial 可见失败详情和独立完成 exit/log 是明确待保留能力；最小提取位置 `FinalService._settle` 的已有 effect/Attempt receipt。没有修改 coordinator、ledger、contracts 或 recovery 的必要授权；若扩张需先协调。

## 原 43-node / remaining-nine / M01–M15

见 `current43-matrix.json` 和 `node-review.md`：保留每条原断言、原 SHA/行号/函数哈希，列精确 successor node、当前运行 SHA 或历史证据出处，区分 bounded observation 与真正全生命周期缺口。附表 M01–M15 在 `capability-review.md`。分类数量是 disposition，**不是 43 项全绿或新执行数量**。

重要未闭合：node16 partial 通知；node21 QQ 重验不一致；node23 原 QQ backlog 自动/持续排空不能用 r10 手动 retry 替代；node24 复合断言只有部分精确覆盖；node28 required clear-target backlog，非 draft 选择；node42 原 monitoring/stable-reset 不被 bounded recovery 豁免；node9 unknown/default fallback 与新模型的冲突需 MA 向用户决策，D6 不能单独当能力移除授权。本批不恢复 generic dispatch，不替用户裁决。r7/r8 固定 Session 和 QQ 写校验、r14 passive Template、r15 quoted shell 的已接受成果保留，未机械重跑全部历史矩阵。

## 清理和未验证层

MCP 子进程 finally 关闭 stdin、等待退出/只终止本实例；owned sentinel 和 HTTP fixture shutdown/server_close/join，native HTTP listener/client/service 在 fixture finally 关闭；真实 detached process 由既有 owned ExecutionStore fixture 检查 supervisor 退出，实际文件计数为一次。失败测试同样执行 fixture teardown。只保留本任务合成日志、XML 和临时 pytest 数据；没有清理别人的 dirty/untracked。没有新增 watcher 或后台 TA。

本批未修改三个 scheduler host、Worker、FinalRuntime、contracts、recovery、迁移脚本、process ownership、产品发布或 schema；没有真实 profile/data/account/51/60、8767/8768、现有服务、main/practical、push、远端 CI 或部署操作。没有 frontend 变化，未验证生产 WS/toast/browser 消费、真实供应商/QQ 投递、POSIX、新旧真实数据迁移或人工验收。最终交付是干净源码/红证据/决策包，绝不是整体退役完成。
