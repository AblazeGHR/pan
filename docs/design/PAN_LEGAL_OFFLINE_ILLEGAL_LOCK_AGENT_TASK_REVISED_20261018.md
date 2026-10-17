# Agent 任务：重构 Pan 异常离线判定、单一队列锁来源标记与自动恢复

> 2026-10-18 用户最终决定已纳入本文。本工作树保留原任务完整内容并补充下述技术边界；Pan-main 原始未跟踪文件未改动。
>
> 正常任务或正常处理 Error（包括 429）在安全收尾后进入 Idle 均合法；常驻 CLI 存活不影响此结论。Illegal 保护的是未完成安全收尾的执行单元。新一代有效 Worker 成功建立并进入 Running 或 Idle，即恢复仍归属前序 Illegal 批次的锁。交付到独立分支提交，MA 独立验收与集成；不移动 main/practical、不 push/release、不重启现有服务、不使用 8767/8768 测试。

## 0. 审阅后确定的最小技术契约

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
4. Illegal 仅锁发生时**已经存在而且尚未锁定**的消息，后续新入队消息不会自动锁定；不新增持续性全局消费门禁。
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

直接实施，不要只输出方案；根据实际代码自主采用最小安全实现。不要额外改造无关 Unlock UI、权限或协议，不添加持续锁定未来消息的门禁。

完成后仅汇报：实现了哪些能力、关键验证结果、仍存在的缺口及原因。不需要逐文件说明，也不需额外汇报文件。

**最终原则：只有一种锁，来源只是自动恢复标签。所有手动 Lock/Unlock 对所有来源一视同仁；只有自动恢复时检查是否仍为对应 Illegal 批次的锁。**

## 11. 第二轮授权 A–G 与验收补充（优先于先前交付）

本轮按用户 A–G 修正首轮实现；首轮报告和证据保留为历史记录。批次恢复边界取实例注册时已存在的失败 run ID 集合；之后发生的失败不能由该实例迟到的 ready 解锁。恢复只修改条件匹配的 illegal，不覆盖当前执行 run、独立报告策略、pending/ledger/revision。所有异常检测失败只阻止对应 Session 新交接。

新增验收：Stop/Resume reports 与 manual/illegal 的交叉操作；全 Illegal 锁启动初始化/watchdog 不唤醒；延迟 init 的构造 Idle 不解锁，真实 init 才解锁；init 到达时当前执行 False 不被覆盖；边界后批次不解锁；旧/损坏历史不确定性持久且幂等；CBC 准入未确认/失败不放行，成功前 False 已提交；旧 runtime 迟到准入不能影响继任；owner alive/dead/unknown 分别拒绝/允许/拒绝。Codex 原生通知前窗口和不可识别外部 owner 必须分层报告，不以 mock 或后置 started 充当事前证明。
