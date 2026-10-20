# Illegal 持续入队锁、独立唤醒与 Oneshot 入口退役

日期：2026-10-20。实现提交：`2f1c2b3bd02e901ceadbc2b010960277bafcbb4c`。
工作树：`D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`；分支：`review/legal-offline-illegal-lock-20261018`。

## 结果与操作

本轮落实用户确认的六项决定。Illegal 未取消期间，所有新入队消息自动建立 Illegal 锁，包括用户、agent/assign、插件/频道、报告、定时与 Job 失败通知。普通群发、普通正文“继续”和 Force 不据此清除 Illegal。手动解锁一条消息或手动 Start/Restart 才允许有效继任恢复；仍需 adapter 就绪证明。手动解锁本身不直接清除 Illegal，启动失败保留事故。

- **Settings → Behavior → Wake message**：配置全局唤醒消息，默认“继续”，移出输入框后保存；非空且最多 32000 字符。
- **选择 Session → Actions → Wake**：独立于“群发”。同次持久化“发送配置消息、解除该新消息的 Illegal 锁、放到队首”，然后调度。启动恢复、定时恢复复用同一入口。
- Pause、Manual、报告策略锁、编辑 lease、未知交接和 native owner 门禁保持原语义；Wake 不清除这些限制，也不打断已经交接的任务。Pause 时唤醒条目在队首等待 Resume。
- Mark as safely offline 保留原有立即解除对应 Illegal 锁并调度的行为。无需为了新机制等待 watchdog。
- 真正的新身份加入尚未恢复事故；重复 EOF、eventId/clientMessageId/taskId 重试、编辑、原条目重投不重新锁定已显式解锁的消息。不同 Wake 按串行提交顺序各自建立条目，后一次在队首；同一去重 ID 不再次移动或解锁。

## Oneshot 调查与退役边界

核对 CBC/Claude adapter 的 stream/oneshot 参数、MCP、权限、模型、会话恢复、system prompt、结果和 usage 路径。Oneshot 的特有执行策略是每轮短进程隔离；没有只有它才能提供的上述产品能力。按用户决定保留 `_consumer_oneshot`、`oneshot_args` 等实现，关闭公共选择和运行解析入口，旧配置有效回退 stream，原持久配置保留。只支持 Oneshot 的自定义 adapter 明确报该模式已退役，不能静默执行虚构 stream。

新建、修改、默认创建配置 API 拒绝显式选择 oneshot；capabilities/Session projection 不再提供该选项，历史 defaults 在前端归一化。原 Oneshot 行为测试显式 skip：Python 46 项、前端 3 项。新入口禁用/历史回退验收继续执行。CLI wrapper 内部的单轮 subprocess、后台 Job one-shot 调度不是本次退役的 Worker 模式，没有删除或停用。

## 技术与证据

- [legal_offline.py](../../packages/core/legal_offline.py:237)：新消息附到事故 carrier；复用单一 manual/illegal 锁来源。
- [worker.py](../../packages/core/worker.py:5840)：所有首次入队共用 staged writer；行、批次成员、ledger/index、revision、浏览器去重 ID、task seq 和任务上下文一起原子保存。磁盘 replace 失败不发布候选。手动报告继续在其既有 writer ticket 中提交并完整回滚。
- [worker.py](../../packages/core/worker.py:4443)：空 Illegal 和全锁积压都拒绝自动恢复；Force 在 queue/spawn 串行边界复检 Illegal；assign 保留任务查询、去重和报告上下文。
- [worker.py](../../packages/core/worker.py:10283)、[server.py](../../packages/web/server.py:10192)：专用 Wake 和 `/api/sessions/wake`；启动恢复、定时恢复不再调用普通群发模拟 Wake。
- [queueStore.ts](../../packages/web/src/stores/queueStore.ts:320)：WS 新条目识别 Illegal 来源；按服务端 position 插入队首，无刷新/轮询补救。保留红色 i+小锁的复合图标与原 tooltip。
- [AppSettingsModal.tsx](../../packages/web/src/components/layout/AppSettingsModal.tsx:1663)：Behavior 设置入口，服务端配置跨浏览器共享。
- [修订设计](../design/PAN_LEGAL_OFFLINE_ILLEGAL_LOCK_AGENT_TASK_REVISED_20261018.md)：2026-10-20 决定明确覆盖旧“只锁检测时快照”的范围约束。Pan skill 和 API 参考已同步。

## 实测

所有命令 exit code 0。使用隔离测试脚本、临时数据、系统分配端口和本地合成 provider；未使用真实远端模型。

| 验证 | 通过 | skip | 失败 | 证据 |
| --- | ---: | ---: | ---: | --- |
| 后端主回归，含 operation/native admission、真实本地 Codex command/review、队列、手动报告、迁移与锁 | 691 | 46 | 0 | [JUnit](../../audit/illegal-new-queue-regression-final.xml) |
| assign/去重收尾复验及 Session message Jobs，和上行有重复 | 154 | 3 | 0 | [JUnit](../../audit/illegal-new-queue-assign-final.xml) |
| 真实 HTTP/进程崩溃/浏览器，共 12 项 | 12 | 0 | 0 | [JUnit](../../audit/illegal-new-queue-http-final.xml)、[日志](../../audit/illegal-new-queue-http-final.txt) |
| 前端 11 文件 | 223 | 3 | 0 | [日志](../../audit/illegal-new-queue-frontend-final.txt) |
| TypeScript + Vite + 预压缩产物校验 | — | — | 0 | [build](../../audit/illegal-new-queue-build.txt)，72 个压缩资产 |
| 改动前端文件 ESLint | — | — | 0 | [日志](../../audit/illegal-new-queue-eslint.txt)，4 个既有 SettingsPopover hook warning |

按 testcase identity 去除 Python 重复，再加前端：**940 通过、49 skip、0 失败/错误**。49 项全部是用户要求暂时停用的原 Oneshot 行为测试，没有新增 xfail 或以 skip 隐藏现行功能故障。[结构化计数](../../audit/illegal-new-queue-final/summary.json)。提交 hook 的 React 检查通过，`git diff --check` 通过。

覆盖：晚到各来源消息、并发相同 event 去重、显式解锁后重试不重锁、Manual 与 Pause、批次成员/receipt 同存与 replace 故障、无消息/全锁的重复 watchdog、Start/有效就绪/拒绝 resume、Wake 顺序与重试、并发 Force 的 Illegal 复检、safe Idle/handled 429、原队列和报告上下文、历史模式回退，以及真实浏览器保存设置和实时新增锁图标/队首条目。

### 重复强杀 Pan 与长 Job

真实隔离服务运行 Session 和持续 86400 秒的 Job `job_07558916dfa6774477677482`；按捕获的 PID/create-time 及进程树，在同一 barrier 放行中强杀测试 Pan、provider 和 Job 进程，随后用原临时数据重启。

实测：Job 结算为 failed，失败提醒 `q_99e5ec9799344b7384b74f009be371b7` 在 `queue_pending`，`deliveryState=queued`、`queueLockSource=illegal`、Session `legal_offline=false`。CLI 日志证明 Wake 前未收到该失败提醒。Job 的 `notificationState=delivered` 在这里表示成功投递到队列，不表示已经交给 CLI。

显式 Wake 后，本地 CLI 首先收到“继续”，随后收到该 Job 的失败提醒；原队列 ID 的 ledger 更新为 `sent_to_cli`。这次同时证明“已锁定”和“解锁后确实到 CLI”，没有把 API 入队回执当发送证明。

原始证据：[前后快照及任务日志](../../audit/illegal-new-queue-final/job-crash-result.json)、[CLI 实际接收](../../audit/illegal-new-queue-final/job-fake-cli.jsonl)。浏览器截图：[Behavior](../../audit/illegal-new-queue-final/wake-behavior.png)、[唤醒后](../../audit/illegal-new-queue-final/wake-completed.png)。截图捕获启动阶段，完整队列消费由 HTTP 顺序测试验证。

## 集成、兼容与未验证范围

开始时 root/branch/HEAD/status 已核验，原 HEAD `6474274757fc9b1af0e0fbf1fc70ed5a1afb76ec` 干净。先将当时本地 main `ccc1ca5b65c4c896dd16934a132a6a169c7b16ce` 合入本分支，合并提交 `bad516b9d0dafad26e58ba0616eb508c54724175`，无冲突，获得 Behavior 分类；原故障报告保留。

本任务没有推进 main/practical、push、release、重启现有 Pan 或在 8767/8768 测试。工作期间 main 被其他任务推进到 `750da7f5081610d29a0aa734723c656bb1259bf4`（workdir retention），本分支没有追合这两笔无关提交；未来整合时须检查共同修改的 server/API/settings/docs。收尾观察 practical 为 `ccc1ca5b65c4c896dd16934a132a6a169c7b16ce`。

本轮没有对 practical 数据执行迁移或写入；不回溯猜测历史未锁消息是否已经手动解锁，已有锁/批次/receipt 保留。Oneshot 原配置保留而有效模式归一化，用户恢复该入口需另行决定。原设计关于未知 owner、无法观测的外部客户端等安全边界没有扩大覆盖承诺。

未跑全仓全量、远端 CI、真实收费模型或真实 QQ/微信网络；已跑实际插件通知对应的标准入队入口和本地 provider。当前 CI 的标准 pytest 扫描会收集新 Python/HTTP/browser 测试与显式 skip，main 已提供 Playwright/Chromium 安装；前端 Vitest/build 仍为本地验证，不宣称已由远端 CI 执行。

canonical 依赖 junction 仅复用，经 prepare_validation_env 核验，未安装/修改共享依赖。所有测试服务和 Job 均清理；12 个实际监听端口逐一释放，收尾查询无这些端口 listener。保留测试证据和本分支，构建产物在本树 gitignored dist。
