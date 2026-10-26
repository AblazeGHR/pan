# R5-P7a candidate evidence

工作树 `D:/project/pan-worktrees/job-p7a-reds-auth-20261025`，分支 `r5/p7a-reds-auth-20261025`，候选父提交 `bb675a69a69efc7a75b046f6e0bc9db2b4ff9db7`。任务为剩余红测核对及授权桥接收紧。候选提交 SHA 由交付消息及 Git 记录给出；未 push，未推进 main/practical，未操作 8767/8768 或启停实用服务。

最终隔离复跑：**70 文件、1104 passed、0 failed、0 errors、0 skipped，exit 0**。pytest 用时 286.94 秒，外层进程用时 290.07 秒。源码只有 `packages/automation/authorization.py`、`packages/mcp/server.py` 两个产品文件变化。React 源码、scheduler/jobs 业务和 MCP 工具注册不变；AST 对照确认 84 个 MCP 工具声明名称、参数、注册装饰器完全一致。没有添加 skip/xfail。

| 隔离验证分组 | 通过数 |
|---|---:|
| authorization | 44 |
| automation | 159 |
| event（含原文件集的 eventloop） | 49 |
| locker（含 user_locker） | 55 |
| job_action_templates | 33 |
| canonical Job、传输、运行时及其余集成用例 | 764 |
| 合计 | 1104 |

单独的关闭前定向检查为 **41 passed**（授权真实 stdio/raw IPC/HTTP fallback 拒绝、handbook、后继断言）。完整集合包含这些用例，不把两轮计数相加。精确 argv 在 [acceptance-command.json](acceptance-command.json)，文件范围在 [acceptance-files.json](acceptance-files.json)，终态与 JUnit 在 [acceptance-exit.json](acceptance-exit.json)、[acceptance.xml](acceptance.xml) 和 [acceptance.log](acceptance.log)。重现入口是 `D:/project/Pan-main/.venv/Scripts/python.exe evidence/r5-p7a-reds-auth-20261026/run_verification.py --label acceptance`，入口再调用仓库 `scripts/run_tests_isolated.py` 清理继承的 PAN_*、使用临时数据和非保护端口。

| 逐项处置类别 | QA 剩余 71 | 本轮追踪身份并集 146 |
|---|---:|---:|
| 产品说明/夹具修复及已集成修复复验 | 24 | 26 |
| 旧协议改为 canonical 或显式退役 | 45 | 118 |
| 基线既有产品失败 | 0 | 0 |
| 缺依赖/构建恢复后原断言通过 | 2 | 2 |
| 合计 | 71 | 146 |

修复/复验组中 **23 项在接手的 bb675a69 triage 就已通过**，本次只同身份复验，不归功于 P7a 实现。其余 3 个身份是 2 项 Event HTTP fixture 在首个请求后安装 middleware 的错误，以及 canonical 工具说明漏掉 handbook 指针的中间回归。授权路径收紧是本次独立产品变更。环境组是 Locker 滚动 GUI 与 User Cards 实时浏览器用例：离线安装锁定依赖、构建当前源码后，原测试及断言直接通过。其余曾受环境阻挡但随后暴露 legacy owner fence 的测试归入协议迁移/退役，未用环境分类掩盖协议问题。

[逐项 JSON](classification.json) 和 [逐项 CSV](classification.csv) 对 146 个原身份分别记录 QA 来源、原始失败、交接状态、最终状态、7f303faa 同身份结果、处置原因及本轮实际执行通过的后继参数化用例。没有未分类项，没有剩余新增红。53 个被迁移/退役的函数定义列在 [retirement-map.json](retirement-map.json)；分类脚本还校验所有列出的后继均实际收集并通过，不能仅靠文字引用。15 个 Event Store 旧宿主重复测试原先已在 P3 `tests/automation_retirement.json` 声明退役，本轮移除其遗留收集体并保留对应 canonical 断言。

授权桥只允许 canonical `/api/jobs`、`/api/triggers`、`/api/event-creators`、`/api/job-templates`、`/api/job-runs`、`/api/operations` 的既有路径形状，以及明确枚举的历史 GET/HEAD/OPTIONS 与 cancel POST。`/api/final/*` 别名保持 HTTP 路由兼容，退出可信对象桥；旧 Action draft 写、旧 Trigger/Job/Template 写等不再可经该桥寻址。现有 ack 的 canonical 原 outbox 身份断言继续执行，未新开旁路。

`authorization_objects` 的四类读取转到 canonical page。写请求在任何传输前返回显式 `retired`，不猜 expectedStateRevision、不从最新摘要推 Run ID、不自动拼操作包。既有 canonical MCP 工具承担后继写入。MCP `_api` 对退役对象族也直接拒绝，防止 allowlist 缩小后落入通用 HTTP helper。真实 stdio 与 raw IPC 检查确认读取保留、旧写无状态变化、无 HTTP fallback，也不会获取用户 Bearer。

插件用例继续使用真实外部插件与 core/native/gateway stdio，事实改经 canonical Trigger → Job Run → recording provider；未恢复 Engine.execute。生命周期测试保留受控宽限期前后的精确收敛和无存活子树断言；Scheduler 广播保留独立失败、一个成功、Event 不变、重复投递无副作用，只去掉未承诺的先后次序假设。

`7f303faa` 在独立 detached worktree `D:/project/pan-worktrees/job-p7a-baseline-7f303faa-20261026` 复现。QA 71 中 **62 项基线存在且同身份通过，9 项该基线不存在**，因此没有把设计新增红归为基线既有。初跑 53 pass / 8 环境失败；基线树独立离线安装和构建后，重跑这 8 项及补跑的 handbook 类用例为 9/9 pass。另有 lifecycle 全 12 项的原始基线复跑通过。原始红证据不覆盖，见 [baseline-summary.json](baseline-summary.json)、[baseline-qa.xml](baseline-qa.xml)、[baseline-retry.xml](baseline-retry.xml)、[baseline-lifecycle.xml](baseline-lifecycle.xml)。

验证过程保留了接手时的 `triage-current.xml`（121 failure + 1 setup error / 723 pass）、`final.xml`（69 failure + 1 setup error / 755 pass）和本轮扩大范围的 `verification.xml`（3 failure / 1102 pass）。这些计数不能直接相减当作同一测试集合：本轮扩大到 70 文件，同时清理了旧协议测试身份。`classification-summary.json` 给出身份级闭环。早期执行被工具上下文切换中断，只有部分日志而无 JUnit 的调用不计入结论；最终与基线复跑均用本地隐藏 helper 留下持久日志和 exit JSON，不调用实用服务的后台 Job 接口。

Windows 本机验证包含真实 stdio、受控 TCP HTTP、现有 Locker GUI/通知/滚动浏览器和进程生命周期测试，提供的是各自断言范围内的证据。**未测边界**：没有三平台远端 CI、真实模型/账号、真实用户数据迁移、部署或人工验收；没有声称旧 Phase5 shell/legacy create 工具仍可执行。被退役的旧 Jobs UI/legacy Pause 场景不再作为当前验收入口；canonical Jobs 完整 UI 浏览器验收须由对应 UI 阶段负责。历史 Locker 的 Trigger/Action choice 未在此任务迁移成 canonical Job：本轮断言其不可重入旧执行宿主，未知回执只查原 Event ID，不能把 unavailable/unknown 当成功执行。

只做测试身份迁移/退役，不做 R6 的产品源、路由或注册删除。运行产生的 artifacts/audit 差异保留在工作树且不纳入源码候选；接手时所有未提交文件的原始副本保存在 `D:/project/pan-worktrees/p7a-handoff-preserved-20261026.zip`，[preserved-handoff.json](preserved-handoff.json) 记录其 SHA256。没有 reset/checkout 丢弃。最终完整验证后只修正两个测试 helper 模块的说明文字，执行 AST 与已验证内容完全相同，证据在 [post-validation-docs.json](post-validation-docs.json)。[source-freeze.json](source-freeze.json) 固定最终输入哈希，[test-helper-cleanup.json](test-helper-cleanup.json) 确认本轮持久测试 helper 已退出。
