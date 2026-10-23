# ETA v0.1 最终设计覆盖与可执行验收矩阵

审计基线 `bbb38bffd4a131e09eddbce58545b53cf49b8f52`，仅合入原 TA 树；
本矩阵按已入库设计、实施补充（含 plugin/query 后续授权）、core-contract 复核。
状态区分：**本轮验证**、**实现/既有证据**、**明确延期**、**待用户**。
既有 79/192/185 及其他历史计数不累加成本轮结果，也不机械重复整套。

## 执行入口

- **C**（本轮完整组合）：`python scripts/run_tests_isolated.py -- python -m pytest tests/test_eta_final_combined.py -o addopts= -q`。需先在本树 `packages/web` 构建生产 dist；只读复用 node_modules，使用 Node/Chromium，无模型。
- **F**（本轮冻结源码相关组合）：报告中的 `final-command.txt`，JUnit `final.xml`。包括 C、增长测量及定向 persistence/cancel/serial/auth/bus/plugin lifecycle/query recovery 用例。
- **H-core**：`artifacts/event-trigger-core/REPORT.md`、`artifacts/event-trigger-rework/REPORT.md` 与相邻 JUnit。
- **H-query**：`artifacts/session-query/REPORT.md` 及最终 JUnit；字段/类型/边界与 query/legacy 用例源码为 `tests/test_session_query.py`。
- **H-plugin**：`artifacts/event-plugin-adapter/REPORT.md` 与相邻 JUnit，源码 `tests/test_plugin_events.py`、`tests/test_plugin_event_lifecycle.py`。
- **H-UI**：`artifacts/eta-action-panel-integration/REPORT.md` 与相邻 Vitest/browser 证据，MA 已接受 UI f328a8d2（79项）。
- **H-delay**：`audit/delayed-restart-mcp-20261022/MA-ACCEPTANCE.md`、定向 MCP JUnit；本轮 C 对同实例实际 stdio MCP 做独立复核。

以上路径均在本候选工作树。单项追加复核可用对应 tests 文件的 `::test_name`，
不用运行整仓。H 开头只表示沿用历史证据，不冒充本轮运行。

## 设计 §1–§6：模型、匹配、操作与授权

| 设计/明确要求 | 实现入口 | 验收/证据及可执行检查 | 状态/剩余 |
|---|---|---|---|
| §1/§3 Event→Trigger→State→Action，不强制 Job | automation/service.py、engine.py | C 中 Node→持久 Run→真实 Pause→队列→生产 UI；无 Job 包装 | 本轮验证 |
| §2/§4 Definition 与 Event 分离，稳定 ID/source/version/schema | automation/events.py、plugins/catalog.py、event_adapter.py | F 的 bus身份/schema及 C 的 producer.ping/source；H-plugin catalog规范 | 本轮验证+历史 |
| §4 注册/发布身份来自可信运行时，不信 payload | opaque handle + connection/generation | C 旧generation error且无 Run；H-plugin 冒充core/other/source拒绝 | 本轮验证+历史 |
| §3/§7 独立异步扇出、有界容量、失败可见 | Bus 每subscriber队列/task + EventConnection | F 的 bus满载诊断；H-plugin 两订阅者/slow peer隔离；非竞争消费 | 本轮验证+历史 |
| §4 多Event OR、依赖可用、不可用保留Trigger | Engine.match/available、API dependencies | C 停用后definition unavailable、Trigger仍enabled，重启actual ready恢复 | 本轮验证；可用性按core-contract要求全部依赖可用 |
| §4 State布尔与比较、同字段同次缓存 | engine.evaluate + state_reader | H-core State缓存/skip/error；当前内置field仅workers.runningCount，不声称任意Session属性可读 | 实现/既有证据 |
| §5 参数 literal/Event 路径；错误不调用操作 | engine.arguments、Action schema | H-core 映射错误failed和schema变化失败；纯字典路径，无脚本 | 实现/既有证据 |
| §5 命中冻结Trigger revision/Action ID/参数 | Ledger.append + run数据 | C 查询前持久Run；F persistence gate与动态引用 | 本轮验证 |
| §5 同Trigger串行、不同Trigger独立 | _runner owner+wake；每tid任务 | F 8个并发交错+serial_cross_trigger；无轮询/Action长锁 | 本轮验证 |
| §5 修改配置只影响后续；Action开始读最新一次 | engine.drain action snapshot+SHA256 | F serial/dynamic用例；C UI展示实际Action/query | 本轮验证 |
| §5 删除/停用/契约变更Action显式失败 | executor/provider/validate_action | H-core dynamic disabled/deleted/schema用例；H-query legacy拒绝 | 实现/既有证据 |
| §6 动态ID独立Group/build | Store源 + execute_dynamic | C query Action无Group即可执行；H-query draft/source | 本轮验证 |
| §6 Group immutable release/pin不变，旧入口不扩权 | build automation=False、legacy provider gate | H-UI immutable release对照、H-query legacy拒绝；没有修改这些产品实现 | 实现/既有证据 |
| §6 能力方向所有API/命令 | 共用固定GET/argv；显式内部Pause adapter | 实施补充明确本版不要求适配全部API；现只支持既有受限GET/命令+Pause | **其余API适配未实现，明确延期**；不可标全部API完成 |
| §6 用户key→12h内存Bearer、刷新重登 | auth.py + automationStore/Login | F API token与授权边界；H-UI login/logout/refresh；key不是OS安全边界 | 本轮验证+历史 |
| §6 Agent只disabled草稿，不夹带enable | Ledger.put + Action draft gate | F http_user_boundary；C 匿名/受限MCP不能借query启停其他Session | 本轮验证；createdBy信息不是凭证 |
| §6 启停/删除/已启用配置修改token，自动化不再鉴权 | automation/api.py + engine无token | F token API，C enabled自动执行/实际MCP权限检查相互独立 | 本轮验证 |

## 设计 §7–§9：插件、持久化与选择器

| 设计/明确要求 | 实现入口 | 验收/证据及可执行检查 | 状态/剩余 |
|---|---|---|---|
| §7 非Python外部adapter，复用Catalog/Session绑定启用权威 | pan-events/v1 stdio adapter + Node示例 | C 实际Node进程，停用/重新enable/重启actual ready；H-plugin三MCP模式共享owner | 本轮验证+历史；其他remote transport延期 |
| §7 停用撤权、订阅撤销、generation fence | EventConnection.invalidate/revoke + eligible | C旧generation拒绝；F在途reconcile close和EOF无热重启；H-plugin慢启动/close隔离 | 本轮验证+历史 |
| §8 Trigger/Run持久化、revision并发控制 | automation/store.py atomic/fsync/replace | F admission/partial disk failure/target checkpoint；H-core并发revision/取消API | 本轮验证+历史 |
| §8 落盘后执行、Event+Trigger唯一 | append/admitted/release_event + dedup | C重启后实际redelivery receipt且Run数不变；F partial persist gate | 本轮验证；publish到落盘仍可丢失 |
| §8 queued恢复：enabled+available才执行；disabled取消 | recover/kick/claim/defer_unavailable | C 自有离线queued fixture→重启插件ready→succeeded；F disable/deferral交错 | 本轮验证；恢复fixture不是OS断电证据 |
| §8 running→uncertain，无盲目重投 | recover、close/cancellation checkpoint | C自有running fixture恢复uncertain，UI警示且无retry按钮；F真取消与query恢复保留结果 | 本轮验证；无类型专属自动对账 |
| §8 失败不自动重试；部分落盘可见 | engine diagnostics + persisted error/results | F append失败无effect、target checkpoint停止后uncertain | 本轮验证；前持久化diagnostics进程内 |
| §8 无同步I/O阻塞loop，无外部操作长全局锁 | io/to_thread、短owner协调、plugin per-owner | F slow disk+inflight close，H-plugin真实hung peer；增长样本见ledger-growth.json | 本轮验证+历史；JSON事务仍与历史大小相关 |
| §9 explicit/event现有语义 | executor固定目标 | H-core explicit/event、H-query prior typed检查；本轮query复用同Pause业务与结果层 | 实现/既有证据 |
| §9 query必做后续已实现，结构化白名单 | session_query.py + SessionQueryEditor | H-query47专项与边界；F真实改名/移动/新增后不重选、恢复不查询；C query UI展示 | 本轮验证+历史 |
| §9 query字段真实、类型/深度/节点/候选/结果有界 | 七字段；8/64/2048/256；typed比较 | H-query schema/type/unknown/cap测试；C3候选1结果冻结 | 实现/既有证据+本轮闭环 |
| §9 固定去重有序ID，逐目标串行且失败继续 | execute_dynamic targetIds/targetResults | F query冻结、H-query部分失败/0结果；C checkpoint+UI展开 | 本轮验证+历史 |
| §9 matched/succeeded/failed+逐目标及中断已落盘结果 | result aggregate + progress ledger | C生产UI per-target与selection；Fcheckpoint失败和恢复不重算 | 本轮验证 |

## 设计 §10–§12 与产品共存

| 设计/明确要求 | 实现入口 | 验收/证据及可执行检查 | 状态/剩余 |
|---|---|---|---|
| §10 running正数归零，0→0不发布、不建第二权威计数 | Worker.status observer→registry recompute | H-core真实HTTP/fakeCLI running闭环；F原核心并发/serial；本轮C事件源为Node | 实现/既有证据；不把Node事件说成running事件 |
| §10 原Pause Everything语义，持久状态和队列真实受阻 | api_session_queue_paused + queue_lock/gates | C解锁目标积压仍不交付，对照fakeCLI收到消息；queue_paused落盘 | 本轮验证 |
| §11 UI英文，列表/启停/依赖/Run/错误/目标 | ActionsView/AutomationView、RunList等 | C生产dist Chromium初始和重启后两次，Action query编辑可见、Run目标/结果/uncertain警告；H-UI79项管理交互 | 本轮验证+历史；本轮配置由API，不冒充全部管理交互重测 |
| §11 enable展示Action/config，凭证仅内存 | TriggerEditor review + authStore | H-UI已接受enable review/login测试与browser，F管理API权限 | 实现/既有证据 |
| 新整合main delay MCP不得冲掉API/router、既有gate | mcp.server agent_restart_delayed + web业务 | C同实例真实stdio MCP→HTTP→fakeCLI delayed completed；受限caller其他Session拒绝 | 本轮验证 |
| §12 parallel/concurrency扩展、Event Store/outbox/replay、幂等重试/Job衔接 | 仅类型/未来方向，无实现 | 原设计与supplement/core-contract均未要求本版交付 | **明确延期，不能标完成** |
| §8/§12 ledger retention/index/paging/大规模稳定性 | 当前单文件JSON无清理/分页 | 增长probe仅1/100/1000条有限合成观测，字节与整次更新耗时增加 | **未完成/容量边界**；上线大历史前先定保留/去重保留策略，不擅自删历史 |
| 真人Pause+队列/使用体验整体确认 | 需用户实际操作与判断 | 自动HTTP、fakeCLI、Chromium截图不代替用户验收 | **待用户，不得冒充** |

## 审计结论与可执行后续

本轮没有复现新的产品 P1/P2，因此没有猜测性产品重构。新增的是跨线验收用例、
有限账本测量与文档状态校正。新增验收脚本自身的GBK输出解码及新版列表locator
问题均有失败记录，不将其计为产品缺陷修复。

- MA复审：核对精确受测SHA与最终证据提交只差报告/证据；运行F，检查生产截图和
  combined-http的进程清理、Node/generation、query目标、队列、MCP和恢复结果。
- 用户：用实际目标进行启用review/运行/暂停队列与uncertain提示的人工作业验收。
  本轮禁止真实模型，未访问用户Session。
- 明确延期：全部API业务adapter、parallel、可靠投递/重放/自动对账、ledger保留/
  分页索引。每项要独立授权和验收，不把本轮组合成功扩张为这些规格已交付。
