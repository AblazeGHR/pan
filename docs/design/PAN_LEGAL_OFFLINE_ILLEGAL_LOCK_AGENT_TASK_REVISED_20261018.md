# Agent 任务：重构 Pan 异常离线判定、单一队列锁来源标记与自动恢复

## 最高优先级：2026-10-20 用户授权覆盖及 2026-10-21 整合澄清

**本块优先于本文所有历史条款，发生冲突时必须执行本块。** 用户已明确更改产品机制：Illegal 持续期间，所有真正新入队消息（包含 plugin 来源、Job 失败、报告、频道、agent/assign、用户和定时消息）建立对应 Illegal 锁。原 §2 第4条“后续新入队消息不会自动锁定”、实施要求“不添加持续锁定未来消息的门禁”以及其他“只锁已有/不锁未来”的表述只保留为历史，已失效。新的持续入队锁不替代 Pause、Manual、报告策略、lease、Steer unknown 或 native owner 安全 gate；重复事故和原身份重试仍不得重新锁定已显式解锁消息。

授权原话：“新机制下，illegal态会illegal锁后来的所有消息，直到取消illegal态”；后续确认：“5.对，包含所有消息，包括plugin来源的”。Oneshot 授权：“没有的话先移除所有入口，但是先不删代码，也skip所有相关测试”。原始持久记录为 `C:/Users/14709/.codex/sessions/2026/10/18/rollout-2026-10-18T03-19-37-01a14b4e-2d9b-7d41-9fe9-7fb7b80b0735.jsonl`，2026-10-20 08:48:12.940Z 的 user response，message `msg_01a1587f-308c-7aa3-97ec-f9abbd91e02e`、turn `01a1587d-87ea-78d2-b243-8b667ee489c3`；完整摘录和逐项 skip 审计见本次整合报告。只 skip 退役路径，混有现行 stream/native/prompt/usage 的测试须拆分保留现行断言。

普通消息和默认 Force 仍属于发送当前任务补充的既有入口，不能清除 Illegal；专用 Wake 才原子执行发送配置消息、仅解自己 Illegal 锁、放到队首。手动解锁或 Start 后须以有效 adapter ready 才恢复其余对应锁，失败不恢复。合并必须同时保留 main 的当前任务补充默认 Force 规则、`originalPrompt` handoff 文档、native TUI、shortcut 与 retention 能力。

> 2026-10-18 用户最终决定已纳入本文。本工作树保留原任务完整内容并补充下述技术边界；Pan-main 原始未跟踪文件未改动。
>
> 正常任务或正常处理 Error（包括 429）在安全收尾后进入 Idle 均合法；常驻 CLI 存活不影响此结论。Illegal 保护的是未完成安全收尾的执行单元。新一代有效 Worker 成功建立并进入 Running 或 Idle，即恢复仍归属前序 Illegal 批次的锁。交付到独立分支提交，MA 独立验收与集成；不移动 main/practical、不 push/release、不重启现有服务、不使用 8767/8768 测试。

## 0. 审阅后确定的最小技术契约

### 2026-10-20 用户决定：Illegal 持续锁定新入队消息，独立唤醒

本节覆盖历史“异常只锁当时已有消息”的范围决定。事故识别仍按失败 owner cohort 一次快照、重复异常不扩大或重锁；但此后 **所有真正新接受的消息**（用户、agent/assign、插件、频道、报告、Job 失败通知、定时消息）在 Illegal 未取消期间均附到尚未恢复的事故 carrier，建立 Illegal 锁。入队行、精确成员、来源、ledger/index、revision 与浏览器去重 ID 在同一有序 writer / JSON 原子替换中提交。显式指定 Manual 的新消息保留 Manual 单锁，报告策略、协议 hold、Pause、lease 和 native owner 独立。重复 eventId/clientMessageId/taskId、编辑、重试和重复 EOF 不给已手动解锁的原身份重新加锁。历史数据不回溯猜测曾否手动解锁。

普通发送（包括正文恰为“继续”、群发、插件通知和 Force）不会据此清除 Illegal 或建立继任实例。全锁积压与空 Illegal Session 都不能因 watchdog/异常 force recovery 自动启动。明确手动解锁至少一个消息后允许继任启动；显式 Start/Restart 也允许，但仍需 adapter 权威就绪证明才能退休前序事故、解除其余 Illegal 锁。启动失败/拒绝 resume 不算恢复。手动解锁本身不立刻清除 Illegal。Mark safe 则保留原有立即退休、自动解锁并调度语义。

唤醒是独立于群发的操作：**发送配置消息、解除这条新消息的 Illegal 锁、移到队首**，三步同次提交后再尝试调度。它不清除 Manual、报告策略、Pause、lease、unknown owner 等安全 gate，不打断已交接执行。Pause 时队首唤醒仍等待 Resume；构造 Idle 不算恢复。并发操作按 queue_lock / writer 顺序结算；不同唤醒可各建消息，后一次位于队首，相同去重 ID 不重复移动/解锁。多选 Wake、启动恢复和定时恢复统一调用此操作；普通群发保持普通发送。

Behavior 增加 `session_lifecycle.wakeMessage`（非空、最长 32000 字符，缺省“继续”），各次唤醒在服务端读取当前配置；定时 Job 原有兼容 action 名与历史 text 可读，text 不再决定实际唤醒正文。

Oneshot 调查：CBC/Claude 的模型、权限、MCP、会话恢复及任务结果均有 stream 对应能力，特有的是每轮短进程隔离，不是独有产品功能。按用户决定关闭所有公共选择入口及 runtime 解析入口，旧配置有效解析为 stream，保留原持久配置和实现代码。API 显式新选 oneshot 拒绝；前端不再展示该选项。旧 Oneshot 测试显式 skip，保留停用边界测试与 stream/native 回归。CLI wrapper 内部使用的单轮 subprocess 和 Job one-shot 调度不属于已停用的 Pan Worker 模式，保留原功能和测试。

### 2026-10-19 最终补充：Illegal 即待恢复，删除独立恢复意图

最新用户决定覆盖下文早期设计：Illegal 和待恢复是同一状态，不再存在恢复意图与 Illegal 的独立组合。删除 `recovery_requested` / `recoveryRequested` 的模型字段、持久输出、摘要/详情/WS/诊断和前端类型。加载旧数据时丢弃旧键，在原有启动交接准备、有序 writer 内持久清理；不从旧布尔值制造事故，因为此前正常 Running 也写过 True。已有 operation、事故和队列来源保持权威。

启动恢复候选、自动恢复和后台恢复 Job 均直接选择 Illegal。正常执行 `legalOffline=false, illegalOffline=false` 不代表待恢复。手动“标记待恢复”与“标记 Illegal”同一个操作：核验 owner 死亡后，在现有账本中记录明确的 administrative operation（不伪造 provider 执行），同次提交 Illegal 与精确已有未锁 queued 快照；重复点击不扩大或重锁。取消待恢复即把已有责任安全退休为 offline，并解除仍匹配其事故归属的自动锁、保留 Manual 和独立安全 gate。活/未知 owner 拒绝手动状态转换。

详情按钮改为 **Mark as Illegal / Pending recovery**、**Mark as safely offline**。API 为 `/legality/mark-illegal` 和 `/legality/mark-safe-offline`，旧接口仅是新语义 deprecated 别名。启动询问弹窗取消待恢复操作同样改为安全下线，不再只是取消提示。设置页、Job 创建模板与详情均更新统一术语。`clear-illegal` 启动策略继续保留；解除锁后遵守原队列调度与 Pause，不额外停止正常队列。

### 2026-10-19 补充：显式清除 Illegal 的启动策略

用户批准用新的 `startupPreference=clear-illegal` 替换重复的设置项 `sync-actual`，英文显示 **Clear Illegal status for all Sessions and treat them as safely offline**。这是启动时自动执行的独立管理员策略，不是“每次询问”的别名。旧持久配置 `sync-actual` 兼容读取为 ask，后续保存设置时写入规范值；不因升级而启用事故清除。

启动已完成失主对账之后，新策略扫描所有仍标为 Illegal 的 Session，包括恢复意图已被用户清除的 Session。先取得原 Session spawn lock 和 queue lock，核验对应事故 owner 及持久 runtime owner 已死亡；活/未知 owner 拒绝该 Session 的清除并返回可诊断失败，继续其它 Session。仅把已确认 Illegal 的 operation 改为安全 offline（保留显式策略退休 proof），不会提前退休独立执行中的 operation。聚合安全时清除恢复意图；否则保留其它责任和其恢复意图。

在原有有序 Session JSON writer 内同次提交 operation、安全退休、批次 tombstone、精确归属匹配的 Illegal 解锁、pending/ledger/revision。保留 Manual（包括已从 Illegal 手动转换的消息）、reports 策略锁、Pause、lease 和 native ownership gate。失败不发布成功事实；重复执行、迟到 EOF 和再次启动不重锁、不重复清除。自动启动决策先落盘，失败可以从现有启动弹窗重试同一策略。普通启动询问弹窗原有“核对并清恢复意图”操作仍保留，不等于本策略。

该策略本身不发送继续消息或调用 Worker create；解除锁后队列仍遵守原调度与 Pause 规则，不能把“解除锁”当作暂停队列的替代。用户若希望解除锁后仍暂不消费，应保留 Pause。

### 2026-10-18 补充：旧字段迁移与 Pan 主服务退出（最新用户决定优先）

- 合法性权威为 `execution_operations` 聚合的 `legal_offline`；`illegalOffline` 仅代表仍未退休的 operation 已被确认 Illegal。执行中或等待可靠终态时可以是 `legalOffline=false, illegalOffline=false`。事故锁及继任恢复继续依赖精确 run/owner/generation，不依赖 UI 状态或旧字段。
- `recovery_requested` / API `recoveryRequested` 是独立的恢复意图，用于启动候选、退出快照和 Job resume。它不能证明执行正在运行、已经安全结束或发生 Illegal。新字段存在时（包括 False）优先；没有新字段的稳定 v1/v2 执行账本优先于旧 UI 状态。只有缺少新执行事实的历史数据才从旧 `last_legal_worker_state=running` 导入恢复意图，绝不制造 run、事故或锁。启动阶段在任何 provider 准入前经原有有序 writer 原子保存迁移；失败仅阻止该 Session 交接，可重试，重复迁移不覆盖新值。
- 本阶段完成删除前迁移：旧字段保留只读序列化/API 兼容，产品生命周期不再写入，不再用于新数据的恢复、退出或 Illegal 判断。React 使用新投影。新 API 为 `/legality/reconcile`、`/legality/request-recovery`；旧 `/legal-state/sync`、`/legal-state/running` 仅为 deprecated 路由别名，执行新语义，不再写旧状态。reconcile 核对失主执行并清除恢复意图，不能清除已确认 Illegal；request-recovery 仅持久化意图，不启动 Worker。
- **Pan 主服务** Ctrl+C/SIGINT、SIGTERM、服务管理停止、强杀均不是安全退出授权。信号边界立即关闭新准入并禁止迟到 result/ready 把待收尾责任合法化；正常可处理退出将责任判 Illegal 并原子锁快照，无法运行收尾的强杀在下次启动对账。已安全 Idle 且没有辅助未终态 operation 的 Session 保持合法；常驻 CLI 仍活不改变这一点。Worker、Job、原生终端内部 Ctrl+C 保留原有语义。
- 显式 Exit API 和既有 Restart API 才按选定策略安全停止、核验 owner 退休并持久化；选择保留策略则保留未收尾责任为 Illegal。安全退出也不能直接清除历史事故，仍须有效继任就绪。CLI/process/Job 停止失败不假报安全退休。
- 可交互的 Pan 服务控制台支持 `q` 后回车。首个 q 等待 2 秒覆盖窗口；已配置策略则调用同一 Exit API，未配置/ask 显示 1 安全退出、2 保留未收尾责任为 Illegal、3 强制保留全部未收尾责任为 Illegal。2 秒内三次 q（或 `qqq` 回车）通过 `preserveRunningIllegal=true` 覆盖已保存的安全退出策略。只读取 Pan 主控制台 stdin，不接管 Worker/Job 终端；无 TTY 时仍使用 API。q 不是额外的退出实现。
- Session 卡片、顶部 WorkerDot、Illegal 筛选、详情诊断均使用新权威事实；详情展示 coverage、revision、恢复意图、历史迁移不确定性和未退休 operation 原因。恢复意图和安全/Illegal 展示分开，不以旧 running 推导红灯。
- 下文早期 Codex 原生准入缺口描述保留为历史审阅依据；当前受控 ResidentTui 的事前 ACK 和多 operation 实现以 `PAN_MULTI_OPERATION_LEGALITY_PROPOSAL_20261018.md` 及已提交交付报告为准。外部未观测客户端仍不作全覆盖承诺，unknown owner 拒第二 writer 约束保留。

- `legal_offline` 保护一次执行，不要求正常 Idle 的 CLI 物理退出。开始执行前，将 False 与新的不可复用 run UUID 同次提交；安全终态在 history/result/恢复信息的同次提交中写 True。失败的提交不能放行 provider 写入。
- Worker owner token + generation 只负责当前所有权；持久 run UUID 负责跨 Pan 重启事件幂等。回调捕获原身份，并在 queue exclusion 及实际 writer ticket 内再次验证。旧状态刷新、Details 的 set-running、restore intent 不代表新执行或恢复。
- 所属 Session 完成失主交接对账后，在 queue exclusion 内以现有 Lock All 的 queued 选择范围形成快照。批次、精确新增锁成员、失败 run 的已判定状态、pending/可恢复 ledger 和 queue revision 同次保存到 Session JSON。物理死亡到检测之间没有持久记录的瞬间快照不作虚假保证。
- manual/illegal 仅是一个可撤销消息锁的互斥来源。Stop reports 保留既有独立 queueLockAutoReport 策略锁，Resume reports 仅解除该策略，不清除 manual/illegal；用户 Unlock/Unlock All 按既有 API 同时解除报告策略锁。Steer/self-consume unknown、force hold、edit lease、Pause、native ownership 均为独立安全 gate，手动或自动解锁不清除它们。
- 新消息不受历史 Illegal 影响。`queueAllLocked` 仍仅是非空公开 queued 投影中每项有效 locked 的摘要，不是 Illegal 消费开关。
- 任何来源全锁均不主动启动。用户 Start/Restart 或新未锁消息可建立继任实例。构造 Idle、spawn 返回、UI 刷新均不是恢复证明；stream adapter 的 init/native_ready 或真实执行完成提供就绪证明。尚未提供证明的 adapter 允许新未锁消息启动执行，但不先消费锁定积压；首次有效就绪在 queue 串行边界恢复确定先于本实例的批次，保持进行中执行的 False/run 身份。oneshot 没有常驻 Idle 就绪证明，需实际任务的 init 或安全终态。
- 批次以失败 run UUID 为键，空批次也保存幂等墓碑；不能因用户解锁、再次重启或新入队重复快照。E2 不接管 E1 已锁消息；有效继任可以逐批条件恢复 E1/E2，新恢复边界之后的异常由其新 run 独立处理。
- 正常 Stop/Restart 在物理停止及必要安全落盘后记录 True；watchdog 中止未安全完成执行不得因为复用 kill helper 而合法化。正常处理 Error/429 的安全终态合法，底层非零退出码不追溯改判。进程 EOF 后先确认 tracked runtime 退休，才能供应第二 writer。
- 缺失或损坏的旧合法性、没有稳定 run 身份的 False 是未验证历史，不从旧 legal 字段猜 Illegal，不改变原队列可消费性。已有消息锁迁为 Manual，重跑不覆盖新来源或手动操作。持久化 offline_migration_uncertainty 记录历史缺失/无有效 run 的不确定性，重复加载不覆盖已记录原因；新创建与历史迁移区分。旧恢复意图继续独立保留，新 Illegal 展示和判定不依赖它。
- 对未注册但持久 owner PID/create-time 仍活或无法核验的执行，恢复拒绝创建第二 writer；即使前序执行已安全 Idle 也保留此所有权约束，但绝不因此把合法 Idle 标成 Illegal。浏览器 detach/control 切换不属于运行结束或恢复事件。
- CBC native UserPromptSubmit 在执行前发出唯一 admission 请求，只有 Pan 原子保存 False/run 并确认相同 runtime 后才放行 hook；失败或超时拒绝提交。重复请求不产生第二执行。Codex app-server 的 turn/started 是事后事实，不能宣称事前 admission；其原生 TUI 当前能力不能关闭通知前崩溃窗口，必须列为未覆盖边界，不将 typing/Idle 猜成 Running。持久 owner PID/create-time 存活或未知时拒绝第二 writer（包括跨服务重启的合法 Idle）；无可核验身份的既有外部 takeover 不允许借此自动启动第二实例。


## 1. 目标与不可改变的决定

解决现有通过 `state`、`legalState`、`last_legal_state` 比较判断异常掉线时，因为异步更新时间差产生的误判、漏判与竞态。引入独立持久化 `legal_offline`，依据一次 Agent/Worker 运行是否经过合法结束流程判断异常。非法离线时对当时已有的队列消息执行现有 `lock all` 对应范围的锁定，并标记来源 `illegal`。Worker 后续有效进入 `running` 或 `idle` 时，自动解除仍归属该次 Illegal 的锁。

必须遵循：

1. `legal_offline` 独立于旧字段，禁止长期同步、双写或通过旧字段计算；逐步迁移、最终淘汰旧异常判定逻辑。
2. 正常运行时 `legal_offline=False` **不等于** Illegal；只有运行实例已结束、异常失联或恢复时发现上轮未合法结束才判 Illegal。
3. 正常 Stop、安全 Restart、正常完成进入 Idle 均合法；**当前所有正常捕获处理并进入 Idle 的 Error 也合法**。未来允许配置某些 Error 虽进入 Idle 仍非法，本次仅预留集中策略入口。
4. **历史条款（已被本文最高优先级授权覆盖，不再生效）**：Illegal 仅锁发生时已经存在而且尚未锁定的消息，后续新入队消息不会自动锁定。当前规则见文首：持续为真正新入队消息建立 Illegal 锁，独立安全 gate 不变。
5. **消息只有一个可撤销消息锁**，其来源是 `manual` 或 `illegal`（既有 reports 策略和协议安全 gate 独立保留）。来源只区分自动解锁资格，**不是两层互相隔离、可叠加的锁**。
6. 手动 Lock 可以把 Illegal 锁转成 Manual 锁；用户手动 Unlock / Unlock All 可以解除**任一来源**的锁；普通手动 Lock All 将范围内的锁视为 Manual。对用户而言两类锁操作无差异。
7. Worker 在本次 Illegal 之后有效进入 `running` 或 `idle` 时，自动解锁**仍为该次 Illegal 来源**的消息。来源已转 `manual`、已被手动解锁、已删除等均跳过。自动解锁不得影响 Manual。
8. 同一次 Illegal 多次检测、Pan 再次重启不能重做 Lock All 而扩大锁定范围，也不能重新锁定已被用户解锁的消息。
9. 不改变手动 Lock、Unlock、Lock All、Unlock All 的 API 和权限语义；允许为来源元信息进行最低限度底层改动。
10. 优先复用当前生命周期、状态与持久化队列机制，不引入不必要的事件系统或庞大状态机。

## 2. 调查现有实现

先定位 `state` / `legalState` / `last_legal_state` 所有读写和消费者；Session、Worker、Agent、CLI、Pan 服务的生命周期及 Idle/Running/Offline/Illegal 转换；Error→Idle、Stop、Restart、CLI 强杀及 Pan 崩溃恢复路径。确认现有 `lock all`、单消息 Lock/Unlock、Unlock All 的选择范围、实现、持久化与消费条件；确认 Worker 的状态转换入口、是否有运行代次/身份/版本、数据库事务及迁移机制。

必须区分 Pan 服务进程与各 Session 的 Agent/CLI 运行生命周期，不能因为服务进程重启就把所有原本 Idle 的 Session 标为 Illegal。

## 3. Legal Offline

新增持久化 `legal_offline: bool`，作用域为 Session 当前受保护执行单元；Session/Worker 身份另由持久 run UUID 与 owner token/generation 约束：

- `True`：该次运行已确认合法结束；
- `False`：本轮正在运行或上轮未合法结束；
- 不存在：首次创建或历史迁移，不能直接误判为 False。

确定首次创建的 Session 初始化 True；实际开始新运行**之前**持久化 False；完成安全收尾并合法进入 Idle / Stop / Restart 停止阶段时写 True。错误被正常处理并进入 Idle，首版写 True。写 True 之前必须确认当前执行已安全收尾、任务/恢复信息安全落盘；合法 Idle 允许常驻 CLI 仍存活。Stop/Restart 的物理停止阶段另须确认旧进程停止；不能因短暂显示 Idle 就写 True。

如果未安全收尾的执行处于 Running 时 CLI 被外部强杀、崩溃或 Pan 意外死去，执行期间的 False 会被保留，不依赖死亡瞬间的回调。正常 Idle 的常驻 CLI 退出不因此产生 Illegal。在线检测到 CLI 异常即触发 Illegal；恢复初始化时如果上轮 False 且旧实例已结束/失联，则触发 Illegal；如果上轮实际上仍运行有效，不因 False 误判。新一轮启动前先完成上一轮异常判定及应有锁定，不能让积压消息抢先消费。

为未来可配置 Error 规则预留单一集中合法性入口，如 `is_legal_offline(reason, next_state, context)`；首版默认正常处理进入 Idle 的 Error 合法，不做配置界面及规则引擎，配置未来变更不追溯历史。实际函数及事务接口遵照代码架构。

数据迁移不能简单从 `last_legal_state` 复制；一次性参考可靠的实际生命周期持久化信息初始化，明确正常 Idle 不应被大面积误判，无法确认的上轮运行按保守安全策略处理；迁移幂等。新机制运行时完全不依赖旧字段。旧字段仍有其他用途可暂留，分阶段迁移、最终清理。

## 4. 单一锁状态 + 来源标记

逻辑模型（实际字段设计自选）：

```python
locked: bool
lock_source: Literal["manual", "illegal"] | None
illegal_batch_id: str | None
```

状态不变量：

| locked | lock_source | 含义 |
| --- | --- | --- |
| false | null | 无锁 |
| true | manual | 手动锁，不因 Worker 恢复而解锁 |
| true | illegal | 异常锁，仅该次异常对应 Worker 恢复后自动解锁 |

`illegal_batch_id` 可由单独锁批次/关联记录承载。只要能精确区分新旧 Illegal 事件即可。**不要采用 `manual_locked OR illegal_locked` 双锁叠加模型。**

### 手动操作统一规则

- 用户 Lock：对目标设置 `locked=True, source=manual`；若原为 Illegal，转为 Manual，今后不会被该 Illegal 自动恢复解除。
- 用户 Lock All：对目标统一执行 Manual 锁定规则，包括原本 Illegal 的消息；接口及覆盖范围仍按现有语义。
- 用户 Unlock 或 Unlock All：不问来源，设置 `locked=False, source=None` 并移除/失效相关 Illegal 批次归属；手动解锁 Illegal 必须成功。
- 消息已被手动解锁后，同一次旧 Illegal 的重复检测不得重新上锁。

锁来源只影响**自动解锁**，不能阻止手动操作。手动 API 的对外行为、权限和界面不需要按锁来源分支。

### Illegal Lock All

发生一个新 Illegal 事件时，按照原有 `lock all` 的目标范围，**只给当前尚未锁定的消息**设置 `locked=True, source=illegal` 并关联本次批次。已有 Manual 或已有 Illegal 的消息保持原状态和来源；之后入队的消息不受历史 Illegal 影响。

记录本次**实际新增锁**的消息 ID、所属 Session/Worker、异常事件/批次 ID、批次是否已恢复。使用最小必要的表或字段实现。必须将新增锁与归属记录原子提交，不能产生无归属 Illegal 锁。重启后可找回未恢复批次。

必须有事件幂等标识，防止多入口重复检测、重启再识别同一异常时再次锁定后来的消息。同次异常只允许一次有效的 Lock All。若锁定操作因崩溃尚未提交，允许安全重试，不得留下部分不可解释状态。

## 5. Worker 状态恢复自动解锁

当**对应** Worker 在 Illegal 之后再次有效进入 `running` 或 `idle`，查询其未恢复批次，只对批次内消息执行条件解除：

```python
for message in batch.messages:
    if (message.locked
        and message.lock_source == "illegal"
        and message.illegal_batch_id == batch.id):
        message.locked = False
        message.lock_source = None
        message.illegal_batch_id = None
mark_batch_recovered(batch.id)
```

这是语义示例，实际用数据库原子条件更新。批次中消息已被用户手动 Unlock 的跳过；后来又手动 Lock、来源变为 Manual 的跳过；删除或已消费的跳过。不能仅凭 `source=illegal` 就解除其他批次产生的锁。

防止旧 Worker 延迟上报 Running/Idle、历史状态重播或无效刷新触发自动恢复。复用现有运行身份、状态版本或单调事件序号保障“Illegal 之后的有效再次进入”。批次恢复与解锁应事务化且可重试，重复状态更新不会重复解锁。

自动恢复完成后按现有队列调度逻辑继续；不新增消费引擎或永久门禁。

## 6. 迁移

如果历史队列只有 `locked` 布尔字段，则：

- 旧 `locked=True` → 来源 manual；
- 旧 `locked=False` → 来源 null；
- 不得猜测历史锁是 illegal；
- 迁移不改变已有可消费性，并保持幂等。

新 `legal_offline` 独立初始化，不与旧状态持续同步。全局检查旧字段消费者，先迁移异常判定链路，再按实际语义逐步迁移其他消费者；不为了清除所有旧字段而大面积重构无关代码。

## 7. 并发与安全

- Illegal Lock All 与批次归属原子提交；恢复解锁与批次恢复标记原子提交。
- 手动 Lock/Unlock 与自动恢复并发时，必须遵循最新明确手动操作，不能被迟到的批次恢复覆盖。
- 消费入口判断单一可撤销消息锁及既有独立策略/安全 gate，不清除未知投递、lease、Pause 或 native ownership。
- 恢复启动先判 Illegal 并锁定已有消息，再允许历史消息正常调度。
- `legal_offline=False` 的正常 Running 不自动 Illegal；安全结束后写 True 不提前。
- 重复检测、进程重启、状态刷新不得扩大同一批次的消息集合。
- 所有 DB 失败不能被当作合法退出或已成功锁定。
- 若单布尔运行合法性不足以处理并发 Worker，复用现有身份/代次，必要时补充最低限度所有权校验。

## 8. 必测场景

1. 首次 Session 正常启动，Running 期间 False 不 Illegal；安全 Stop/Restart/Idle 写 True。
2. Error 正常处理→Idle 合法；Error 处理中崩溃非法；CLI 被强杀、Pan 异常退出后恢复可识别上轮未合法结束；本来正常 Idle 的 Session 不误判。
3. A 原先 Manual 锁、B/C 未锁：Illegal 后 A 保持 Manual，B/C 成 Illegal，且记录 B/C；D 后入队不被锁。
4. Worker 再次有效 Running **或** Idle：只解除 B/C 中仍归属当前 Illegal 的锁，A 不变。
5. 用户对 Illegal 锁 B 执行 Unlock：立即无锁；Unlock All 同时解除所有目标 Manual/Illegal 锁。
6. 用户对 Illegal 锁 C 再次手动 Lock：来源转 Manual；Worker 恢复时 C 仍锁定。
7. 普通手动 Lock All 使目标 Illegal 锁转 Manual；普通 Unlock All 可清除任何来源。
8. 同次 Illegal 重复检测或 Pan 再次重启：不重锁被用户解锁的消息，不给 D 添加 Illegal 锁。
9. 旧 Worker 过期 Running/Idle 报告不触发错误解锁；不同 Illegal 批次不会交叉清除。
10. 锁定/归属事务、恢复/批次状态事务中间模拟崩溃，验证可安全恢复且幂等。
11. 与手动操作并发时结果正确；消息已删除/消费可安全跳过。
12. 旧数据库锁迁移为 Manual，Legal Offline 初始化正确，反复迁移不覆盖新数据，旧字段更改不影响新判定。
13. 现有手动 Lock/Unlock/Lock All/Unlock All 的对外接口和既有行为兼容。

## 9. 验收标准

- Illegal 判定不再依赖旧状态瞬时比较，而由独立持久化 `legal_offline` 与运行实例有效性决定。
- 当前正常 Error→Idle 合法，未来配置接口已预留。
- 队列消息只有一份可撤销消息锁，来源为 Manual/Illegal；来源**只用于自动恢复**。既有 reports 策略和安全 gate 独立保留。
- 手动锁、解锁、Lock All、Unlock All 不区分来源；Manual 可以覆盖 Illegal 来源，Unlock 可以直接清除 Illegal。
- Illegal 一次只锁当前尚未锁定的已有消息，准确记录批次和消息归属，不影响后来入队的消息。
- Worker 有效进入 Running/Idle 自动清除仍归属对应批次的 Illegal 锁，不清除 Manual 或用户已变更的锁。
- 同次异常重复识别、重启、并发与事务失败不会重锁、误解锁或遗失归属。
- 新旧字段独立，旧异常判定链路已迁移，后续清理可增量完成。

## 10. 执行与汇报

直接实施，不要只输出方案；根据实际代码自主采用最小安全实现。不要额外改造无关 Unlock UI、权限或协议。历史“不添加持续锁定未来消息的门禁”约束已由文首 2026-10-20 用户授权覆盖；必须实施持续入队 Illegal 锁。

完成后仅汇报：实现了哪些能力、关键验证结果、仍存在的缺口及原因。不需要逐文件说明，也不需额外汇报文件。

**最终原则：只有一种锁，来源只是自动恢复标签。所有手动 Lock/Unlock 对所有来源一视同仁；只有自动恢复时检查是否仍为对应 Illegal 批次的锁。**

## 11. 第二轮授权 A–G 与验收补充（优先于先前交付）

本轮按用户 A–G 修正首轮实现；首轮报告和证据保留为历史记录。批次恢复边界取实例注册时已存在的失败 run ID 集合；之后发生的失败不能由该实例迟到的 ready 解锁。恢复只修改条件匹配的 illegal，不覆盖当前执行 run、独立报告策略、pending/ledger/revision。所有异常检测失败只阻止对应 Session 新交接。

新增验收：Stop/Resume reports 与 manual/illegal 的交叉操作；全 Illegal 锁启动初始化/watchdog 不唤醒；延迟 init 的构造 Idle 不解锁，真实 init 才解锁；init 到达时当前执行 False 不被覆盖；边界后批次不解锁；旧/损坏历史不确定性持久且幂等；CBC 准入未确认/失败不放行，成功前 False 已提交；旧 runtime 迟到准入不能影响继任；owner alive/dead/unknown 分别拒绝/允许/拒绝。Codex 原生通知前窗口和不可识别外部 owner 必须分层报告，不以 mock 或后置 started 充当事前证明。

### A–G 实现核验后的具体能力边界

- 新增独立持久 offline_owner，记录当前进程 PID/create-time；登记 owner 不修改执行合法性、不恢复批次。init/result 就绪提交更新 owner 并恢复注册时捕获的前序集合。CBC 桥接的 pan.native_ready 仅证明 PTY 创建，不能代替 SessionStart/init；真正 adapter init 或已完成的结构化 result 才是就绪证据。普通合成终态（如启动失败）不提供恢复资格。
- 外部 takeover 在打开终端之前保存 ownershipUnknown 预留，Windows Codex 继续复用已有 Job Object 约束；活或未知 owner 不允许第二 writer。Job 成功停止后才释放预留；旧非 Job 终端须核验观察到的 PID/create-time 全部死亡，kill helper 返回不作证明。服务重启丢失 Job/终端所有权而只剩 unknown 预留时阻断自动恢复，需操作者独立确认所有执行实例退休并修复 owner 元数据，不能自动以根 PID 消失推断后代死亡。
- 已登记的 PID/后代快照可以核验 alive/dead/unknown，但并不构成 OS 全进程树永不脱离的证明。普通 spawn 到首次 owner 保存之间、晚于快照才派生且脱离的进程，以及历史从未登记的外部 writer，当前能力无法完整证明。此边界必须在交付报告列明。
- CBC hook 准入在本地协议/故障注入层验证；未运行真实供应商 CLI 或调用模型。持久准入成功但确认丢失的执行保留 False/所有权，不自动重发；须等待权威终态或用户 Stop 确认退休。Codex 原生 TUI 仍存在 turn/started 通知前崩溃窗口，本轮未声称闭合。

## 12. MA 批准后的 Codex 受控原生准入补充

本轮基于冻结 e681cb5a 重新实现，不应用归档 WIP。ResidentTui 私有代理的 turn/start，以及空闲时手动 thread/compact/start，必须在 peer.send 前收到 Pan 对相同 admission UUID 的持久 ACK。False、不可复用 run UUID、nativeAdmissionId、owner、task_seq、executionSeq 在真实有序 writer 内同次保存，再发布 Worker 投影；ACK 只回原 runtime stdin。started 绑定原 admission/turn，不分配第二 run。

reservation busy 与 admitted execution 分开；两者均遵守原交接 gate。ACK 由 stdin parser 直接分发，普通控制/任务/EOF 保持原有先后顺序但不阻塞 ACK 读取。代理等待时不持 owner/policy 锁，ACK 后再次核验 thread/control generation/owner 才发送。取消/迟到 ACK/发送尝试由一次性状态转换决定；发送后未知保留 False/owner，不重投。明确未发送或 provider 明确拒绝且未观察 started 冲突时按 admission/run 条件安全退休；旧取消、旧 started/completed 不得结束后继。正常 provider Error/429 仍按既有安全终态合法化，wrapper 自身异常不是 provider 安全结果，不能用合成 result 将发送未知的执行写 True。

保持现有功能：活动 turn 中的 compaction、未知 passthrough、Steer/审批续写和独立 takeover 不一律禁用。仅观察到才开始记录的原生 turn 显式记 nativeAdmissionTiming=after-start；不得宣称事前覆盖。空闲受控请求记 before-forward。独立 command/exec（没有 thread/turn）、detached review、活动 compaction 的后续排队操作和外部 takeover，需要独立完成/owner 映射或更大生命周期扩展，本轮仍未验收，详见交付报告。


## 13. MA 批准的多 operation 实施语义（覆盖第 11/12 节相应历史边界）

依据 f3e051fc 多 operation 设计和本 Session 后续授权，按三阶段复用 Session JSON、queue_lock、ordered writer 实施，不引入数据库或事件总线。v2 execution_operations 是执行责任权威，offline_run 仅作前台兼容投影；legal_offline 仅在全部已准入责任都有安全终态/安全退休证明时 True。每条责任保留不可复用 UUID、owner/generation、准入 revision，辅助请求另存 connection epoch/RPC/process 或子 thread/turn 绑定，不覆盖前台 seq/result/report/队列交接。正常处理 Error/429 只安全结束自己的操作，不清辅助责任；未确认合法性与 Illegal 分开。

同 owner 失效时，将全部未退休责任同次分类为一个事故 cohort；确定 carrier 保存本次已有未锁消息的精确快照，其余保存同 incident 的空墓碑。迟到 EOF/终态/准入不能扩展快照；失败 owner 不再接新请求，需有效继任。ready 仍只恢复捕获的前序批次，保留仍活/未知责任；安全旧终态历史只在继任 owner fence 后压缩，未退休项和事故墓碑不按数量删除。v1 稳定 run UUID 幂等迁入 v2；缺失旧身份仍记录不确定性且不臆造异常；已有 v2 辅助责任即使无 offline_run 也不能被初始化 True。

command/exec 与 detached review 经同一事前持久 ACK，但属于独立 operation。command 仅原 exec deferred final exitCode 响应收尾；write/resize/terminate ACK、前台 turn 结果均不代替它的终态。detached review 保留 delivery 原参数，只有 provider 返回的准确 reviewThreadId/turn.id 取得受限绑定；响应前通知有界缓存，冲突/溢出 uncertain，不按 FIFO 猜。start response 本身不当完成，准确子资源 terminal 或已返回真实 terminal turn 才可安全收尾。服务/连接失败不自动重投业务请求。

活动 compact 保持原转发时机，先持久独立责任；空 RPC reply、自动 compaction item、后到主 turn 完成不能证明本次请求已结束，无法唯一关联时保持 uncertain。未知 passthrough 继续 opt-in 原样转发，先保存 opaque 责任；普通成功 reply 不安全退休。已验证只读与纯 config/batchWrite、config/value/write 不臆造执行责任；MCP reload 不算纯设置，已绑定的 Steer/审批/command continuation 不重复建 run，无法关联的 command continuation 保守登记 opaque。未确认合法性但尚未判 Illegal 的状态不伪造 Worker Running，也不加 Illegal 锁；一旦确定 Illegal，按文首最高优先级条款持续锁新入队消息；API legalityDiagnostics 给出 scope、UUID/kind/state/reason。确认受控 owner 停止的 Stop/Restart 可按 scope 退休这些责任，手动消息解锁不能替代执行退休。

外部 takeover 保留 owner-only 覆盖边界：不追溯猜测外部每个 turn，合法 Idle 可以与活/未知 lease 共存，但拒第二 writer。Job 退休顺序为 TerminateJobObject → ActiveProcesses==0 → 持久 owner_retirements 与覆盖责任结算 → 关闭句柄/清 runtime 引用。replace/关闭失败都保留可重试证明，未保存 proof 前服务崩溃仍 unknown。非 Job 根 PID/已观察后代快照不足以证明完整覆盖时，不凭空清预留；没有新增绕过 unknown 的人工布尔重置 API。

当前 Codex 0.162.1 的 paginated thread 不支持 detached review；保持 upstream 明确拒绝语义，不改写 inline、不强行改变默认 history mode。真实本地 legacy thread + 合成 provider 已验证子 thread 完成映射；活动 compact/opaque 的自然终态自动关联仍受协议能力限制，其可证明 Stop/Restart 退休路径已实现，不能宣称任意未知方法都可自动收尾。外部不可观测客户端的逐 turn 保证仍不属于 Pan 管理执行范围。
