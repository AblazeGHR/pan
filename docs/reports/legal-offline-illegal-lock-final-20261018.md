# Legal Offline / Illegal Lock：A–G 最终交付

2026-10-18；同一工作树、同一分支继续实施，待 MA 独立验收和集成。

## 交付与提交

工作树：`D:/project/pan-worktrees/legal-offline-illegal-lock-review-20261018`。分支：`review/legal-offline-illegal-lock-20261018`。

- 基线及最后核验的 main/practical：`5d20e72bf44f8cdb46bf8629f4772fb3b393863a`。
- 初始审阅：`1e50493441202e7db0e17794eaca1b46c12a7753`。
- 首轮实现：`2d9c95fa2372308b40ba9f05ae1cdd8475c32acd`；首轮报告整理：`236534e55a4df340e34e0d4bb266f201f4e1090e`。
- 本轮先提交 A–G 技术契约：`a8efac60386bb047b0da88aa46aa0f291ba0d602`。
- 本轮实现/迁移/测试/证据：`6239d7453eb684e9fc39dee9ffd7bc9b5bf153f1`。

未移动 main/practical、未合并、未 push/release、未重启现有 Pan、未访问 protected 8767/8768。所有真实服务测试使用独立数据根及动态端口，自有进程按 PID/create-time 回收。最后只读核验 8768 仍为既有 PID 31556，8767 无监听。

原用户设计文件位于 Pan-main，为未跟踪文件，保持原字节：SHA256 `C4055322C9162446C833BBDAFE351CEFA6F8FDA88C7E69D94FE6978538C2D6A9`。分支修订版采用独立文件名 `docs/design/PAN_LEGAL_OFFLINE_ILLEGAL_LOCK_AGENT_TASK_REVISED_20261018.md`，避免未来集成覆盖该原稿。

## 对 A–G 的落实

| 原则 | 当前实现及含义 |
| --- | --- |
| A 单锁与独立 gate | manual/illegal 仅为可撤销消息锁的互斥来源。Stop reports 保留独立 queueLockAutoReport；Resume reports 只清该策略；显式 Unlock 按原 API 清除可撤销锁及报告策略。unknown Steer/selfConsume、forceHold、编辑 lease、Pause/native owner 均不随自动或手动解锁消失。 |
| B 持久 run 身份 | 每次受保护执行在交接前同次保存 False + UUID。Worker token/generation 独立防旧回调，actual writer 内再核验。安全终态同 history/result 保存 True；正常捕获 Error/429 合法，Idle 的 CLI 可以存活。 |
| C 原子异常与隔离失败 | queue exclusion 内先对账失主 pending/ledger，再快照已有 queued 未锁项。失败 run、精确成员、illegal 来源、锁及 revision 同次原子替换。失败 run UUID 是墓碑/幂等键；同次检测不扩大、不重锁。初始化和 watchdog 失败只影响该 Session。 |
| D 全锁与有效实例 | 所有来源全锁不自动唤醒。Start/Restart 或新未锁消息可以建立实例；构造 Idle、spawn 返回、CBC 的 PTY-created native_ready 都不恢复。实际 adapter init 或结构化 result 才提供就绪证明；合成启动失败终态不提供恢复资格。 |
| E 不猜历史 | 缺失/损坏历史不臆造异常 run，保持可消费性。offline_migration_uncertainty 记录原因并随 Session 保存持久化；重新加载保留已有原因/新来源。旧布尔锁迁 Manual，旧 autoReport 保持独立策略，任何显式新来源均不猜测覆盖。 |
| F 恢复边界 | 实例注册/Restart 时捕获已有失败批次 ID 集合；ready 只恢复该集合且仍为对应 illegal 的项，后来的批次不解。当前实例已开始执行时，ready 不覆盖其 False/run。pending、ledger、批次及 revision 同次保存；后续继续原队列调度。 |
| G native 与 owner | CBC UserPromptSubmit 在 hook 放行前等待 Pan 持久准入确认；失败/超时拒绝。owner 独立于 legal_offline 持久记录 PID/create-time；外部 takeover 打开前保存 unknown 预留。alive/unknown 均拒绝第二 writer；旧非 Job kill 返回后还须验证观察到的身份已死亡。Codex 原生事前能力与未观察后代的限制见下文。 |

UI/API/WS 继续使用新权威 legalOffline/illegalOffline；旧 last_legal_worker_state 独立保留恢复意图。正常 Idle 被外部结束不误标 Illegal，Running 未安全收尾才产生异常批次。

## 实测结果

| 本轮检查 | 通过数 | 失败 / 错误 / skip | exit | JUnit 时间 |
| --- | ---: | --- | ---: | ---: |
| 后端 30 文件（含 46 个 legal_offline、63 个 CBC bridge 用例） | 524 | 0 / 0 / 0 | 0 | 47.639s |
| 真实隔离 HTTP/进程/Chromium 生命周期 | 12 | 0 / 0 / 0 | 0 | 38.092s |
| 本轮不重叠合计 | **536** | **0 / 0 / 0** | **0** | — |

所有最终套件退出码均为 0；失败/错误/skip 均为 0。完整命令与机器可读计数见 `audit/legal-offline-20261018-final/results.json`，日志和 JUnit 在同目录。附加定向套件与主套件有重叠，不重复累计。

本轮未修改 React 源码或安装依赖；前端 65 用例及 TypeScript/Vite build、70 个压缩资产的证据保留在首轮 `audit/legal-offline-20261018/`，不能写成本轮重跑。当前真实 Chromium 测试使用该不变前端，验证实际 HTTP/WS 服务及无刷新 Illegal 展示。共享依赖 junction 本轮只读，Vitest 缓存未写入。本轮代码及测试最终 SHA256 在 `source-manifest.json`。

回归重点：

- 正常任务、正常捕获 429、CLI 仍活的安全 Idle，随后杀 Idle CLI 或重启服务均不产生 Illegal。
- Running 外杀、硬杀自有服务、失主 reservation/ledger 对账；锁仅包含已有未锁 B/C，后续 D 不锁。手动释放 B、将 C 转 Manual、再次服务重启不扩批次。
- False、异常、终态、恢复的 replace 失败/取消，未提交 API 不可见，旧 writer/owner/generation、PID 复用、并发入队/Unlock/Manual claim、lease、空批次/多批次均保留已有断言。
- 无证明的 Idle 不恢复；ready 到达时当前执行 False/run 保持；边界后批次不解；报告策略、unknown safety holds 独立保留。
- 真实隔离服务：全 Illegal 锁且未 Pause，服务重启仍无新 CLI；用户 Start 后 fake adapter 延迟 init，ready 前保持 Illegal 和 owner 身份，ready 后恢复消费；模拟 invalid resume/启动失败则批次不恢复。
- CBC 本地桥接 hook 与 Pan 真实 JSON 原子保存的线程握手：保存阻塞时 hook 不返回；失败时不放行；成功确认前磁盘已为 False；重复 hook 不产生第二执行；迟到准入及迟到 ready 无法修改新 generation。
- Unknown owner 预留及存活 PID 保留；吞掉 kill 失败或根 PID 消失均不能证明非 Job 终端退休；单 Session 对账失败不会中止其他 Session 的 watchdog 恢复。
- 浏览器仍验证 Illegal 红点和继任 ready 后恢复，无刷新；Pause 与手动锁仍挡住实际交接。延迟 Restart/插件组合的原有真实服务回归继续通过。

## 代码推断、剩余能力边界与阻断条件

下述不是已经跑通的真实供应商结论。用户 A–G 产品语义已确定，没有再等待产品澄清；仍有这些必须由 MA 验收时保留的技术边界。

1. **P1，Codex 原生 TUI 的事前准入仍未闭合。** `packages/core/adapters/codex/app_server_wrapper.py:1432` 接到 provider turn/started 才发 pan.native_turn_started；执行可能已经开始。Pan 后置建立 False/run 不能消灭通知前服务崩溃窗口。本轮保留既有 native 控制能力，未把输入键或 Idle 猜成执行，也未称 started 为事前证明。要消灭该窗口，需供应商提供同步 before-turn admission 或在所有原生执行入口前增加可验证握手；不能用打字、Enter 或 UI 状态代替。
2. **Owner 证明有观测范围。** `packages/core/legal_offline.py` 核验记录的 PID/create-time 及当时可见后代；普通 spawn 到 owner 首次保存之间、快照后派生并脱离的子进程、从未登记的历史外部 writer，不能据此保证全覆盖。外部 takeover 先保存 unknown 预留，Windows Codex 复用已有 kill-on-close Job；服务重启失去可核验 owner 时拒绝第二 writer。只剩 unknown 预留且无可证明的退休路径时，需要操作者独立确认所有实例退休并修复 owner 元数据，本轮不提供自动“猜死”开关。
3. **CBC 验证分层。** 本轮验证了本地桥接/hook/Pan 持久协议及真实隔离服务 fake provider；未运行真实 CBC/Codex 供应商、模型请求或真实供应商 429。CBC 的实际 hook 阻止执行语义及用户原生交互仍需真实 CLI 隔离验收。持久准入成功但确认丢失时保留 False 和 owner，不自动重投；等待权威终态或显式 Stop 安全退休。
4. **无启动 init 的 adapter。** 显式 Start 返回构造 Idle 之后，仍需真实 adapter init 或已完成的结构化 result 才恢复。若 adapter 只有首个提示后才初始化，可由新未锁消息建立证明；不会为全锁队列自动发送探测任务或伪造 ready。
5. **异常快照时间。** 记录的是在 queue 串行边界完成对账后的已有 queued 未锁成员；不声称它等于 OS 物理死亡纳秒时的消息集合。持久化失败不放行该 Session 的新交接，也不全局关停其他 Session。

当前关键证据定位（实现提交中的行号）：

| 源码 | 核验点 |
| --- | --- |
| `packages/core/queue_locks.py:26`、`:37` | reports 独立开关；迁移保留显式来源 |
| `packages/core/legal_offline.py:84`、`:157` | PID/create-time 三态；条件恢复边界及保留当前 False/run |
| `packages/core/session.py:1560`、`:1847`、`:1919` | 迁移不确定性、owner 和 JSON 序列化 |
| `packages/core/worker.py:637`、`:689`、`:704` | 对账与 owner 登记不等于 ready；恢复 proof/身份复检 |
| `packages/core/worker.py:2139`、`:2248` | CBC durable admission 与捕获 stdin 上的确认 |
| `packages/core/adapters/cbc/native_bridge.py:202` | hook 在 Pan 确认前阻塞，失败不发 turn_started |
| `packages/core/worker.py:3562`、`:6136` | 全锁不唤醒；单 Session 对账失败隔离 |
| `packages/core/worker.py:7132`、`:7152`；`packages/web/server.py:3456` | 事前 takeover 预留与退休再核验 |

原审阅的逐项文档/源码行证据、可复现场景和最小文字修订保留在 `legal-offline-illegal-lock-review-20261018.md`。本轮源码入口：`queue_locks.py`（独立报告策略）、`legal_offline.py`（批次条件恢复/owner）、`session.py`（迁移原因与原子保存）、`worker.py`（ready、登记 owner、CBC admission、单 Session watchdog）、`native_bridge.py`（阻塞准入）、`server.py`（takeover 预留）。

## 集成准备及操作边界

保留分支和全部审阅记录。main/practical 最后核验仍为基线；MA 可独立 review 本分支累计 diff，验收后再决定 main 集成及 practical 快进，本次未执行。若 canonical 已变化，应重新核对基线及相关回归；不要覆盖 Pan-main 的未跟踪用户原设计文件。

沿用首轮已披露的操作记录：早期默认 Vitest 曾写共享 node_modules/.vite/vitest/results.json，后续已统一 --no-cache，未猜测恢复其他人的旧缓存；该偏差已在首轮报告说明，本轮未再次写共享缓存。未安装依赖、未改 canonical 依赖包内容或 lockfile。
