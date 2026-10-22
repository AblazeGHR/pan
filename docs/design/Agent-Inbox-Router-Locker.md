# Agent Inbox / Router / Locker 冻结设计

状态：2026-10-23 用户冻结，TA 实现候选；起点 140455976b7c60097fafdd4dc3f8e3b18a699ccc。

## 接收对象与范围
Router 按接收对象区分 User Inbox、Agent Inbox、Session Queue；撤销 Scepter 统一 Session Inbox / External Mailbox 方案。插件可选声明目标消息容器，并非所有插件消息必须经过 Router。首批实现 Agent Inbox Locker 及现有 Session Queue 的实际路由。User Inbox 仅保留明确扩展契约，对未实现容器必须明确拒绝，不能返回假成功。用户审批 UI 和前端 UI 不在本批。

Locker 是 Agent Inbox 内按通信双方/来源组合唯一的共享容器，不属于某个 Agent。QQ 至少包含插件实例、bot、私聊/群聊类型、联系人/群 ID。所有现有权限范围内的 Agent 共享消费状态，不增加 Agent 私有消费游标、不扩大访问权限；未来可见性隔离和独享订阅不实现。

## 内容、订阅与消费
默认静默积压。Notification 只提醒，不携带正文、不消费。全文订阅将原消息持久加入目标 Session Queue 即为该目标接纳，不等待 CLI。稳定消息身份、正文、附件与 parts 保留，尽量复用 History 内容格式；仅进入 Locker 不写作已进 context。

开启订阅默认只接新消息，积压通过显式 backlog 操作转发。一次匹配多个全文订阅者时固定接收者集合，持久化逐目标意图/回执；已经入队的目标不重复投，明确失败可待投，全部持久接纳后完成共享消费。队列落盘与 Locker 提交间不承诺跨文件事务：以稳定原 ID、确定性 QueueID、意图与 Queue 持久回执核对；unknown 不盲重投。内部投递记录不是消费者私有进度。

手动消费与自动转发必须原子领取，避免竞争重复/丢失。满容量明确拒绝并保留未消费内容，不能静默截断。停用/卸载来源停止接收及自动转发，保留内容并标明不可用，删除为独立操作。

## 来源身份与迁移
插件提供稳定来源消息 ID；同来源同 ID 同内容幂等，内容不同明确冲突。QQ 必须核对真实 OneBot message_id，保留 bot/source 域。旧无原生 ID 记录使用确定性迁移身份并标明 legacy，不宣称可证明的原生身份。

QQ selective 继续独立写 QQ history；现有 qq_read_inbox / qq_bind 作为兼容入口接新抽象。consume=false 保留，true 共享消费；QQ history 不因消费删除。旧 qq_subscriptions 保留并映射全文模式：转发并消费是本次用户明确变更，历史积压不自动重放。旧 JSON 上限截断改动须明确说明，迁移测试只用临时数据，不触碰真实文件。

mirror 创建/绑定 Session → /api/task → Worker → 回 QQ、前缀命令直接 HTTP、qq_send_message/file 出站均保留，不强制改为 Locker 消费者。微信/未来插件获得统一内部契约，微信实际迁移属于后续批次。

## 实现与交付边界
先核对插件生命周期、队列能力；新增模块加薄适配，不引入任意 URL/shell 路由。提供必要 HTTP/MCP list/read/consume/subscribe/mode/backlog，权限复用现有机制；提供稳定前端 schema 与 API 文档。保留 Pause/Illegal/lease/owner/generation、原 QueueID 及 unknown 语义。不修改 Job/scheduler/automation 业务，不合入其他 TA 未提交内容。

先写确定性并发/故障回归：多目标部分失败、Queue 落盘后 Locker 提交前中断/冷恢复、手动抢占、去重冲突、模式与新消息边界、容量、插件停用、附件、bot 隔离、旧兼容。真实闭环仅随机非保护端口、自有临时 profile、fakeCLI，核实实例身份及全部 data 根；不接真实 QQ、不用收费模型/账号/用户 Worker、不访问 8767/8768 真实 data、不装共享依赖、不放宽断言/skip、不派子任务、不动 main/practical、不 push/release、不重启服务。

交付 clean 候选、精确源码 SHA、命令退出码/JUnit、迁移/恢复边界、前端契约后停止，由 MA 独立验收。

## 实现协议 v1

### 存储与状态机

新增 `packages/core/agent_inbox.py`：SQLite（`data/agent_inbox/inbox.sqlite`，FULL synchronous）是 Locker、消息、共享消费、订阅、固定投递意图及来源去重的单一持久存储。SQLite 事务负责接收/手动消费/自动领取互斥；不与 Session JSON 声称跨文件原子性。消息内容消费后保留，稳定来源 ID 的 tombstone 不自动回收。显式删除 Locker 会删除其内容与订阅，仅来源摘要与消息身份 tombstone 仍保留，不保留已删除正文；删除存在未完成全文意图的 Locker 会明确拒绝。

全文意图状态：`ready → in_flight → accepted | failed | unknown`。仅队列适配器明确返回 rejected 才标记 failed，允许下次 recover/backlog/新事件触发重试。异常、取消中断或无法证明未写入统一保留 in_flight/unknown。恢复仅按原 QueueID 查现存 queue_pending/queue_delivery_ledger；查到则接受，缺失则 unknown。Queue 回执可能按既有保留策略过期，过期不代表从未投递，不能自动重放。unknown 无自动清除/强制重发接口，应由用户核查实际 Session 数据与备份后单独处理。

固定全文接收者在首次自动领取时持久化；后续订阅变更不重写既有计划。每个目标意图不是消费游标。全文目标全部 accepted 后共享 consumed=true，通知回执不影响共享消费完成。accepted 与 consumed 单调，通知完成不会撤销并发手动消费。手动操作对 claimed 的消息返回 busy。

订阅 mode 为 silent / notification / full。创建或实际变更 mode 时边界取当前最大 sequence，相同 mode 重试不移动边界。不存在 Agent 私有读取进度。backlog 对当前所有全文订阅者选择未消费历史消息；已有固定计划继续原接收者，不改投新的 Session。

### HTTP 与 MCP

HTTP 沿用现有本机 API 边界；本批未新增全站认证。MCP 必须有可信 Pan caller，并对订阅目标复用 `_check_access` self/managed 权限，不增加 claim 或用户审批操作。Locker 内容共享可见性沿用现有 QQ Inbox 共享访问边界；未来私有 Inbox/独享订阅不在 v1。

| 入口 | 参数 | 结果/语义 |
| --- | --- | --- |
| `GET /api/agent-inbox` 或 `POST /api/agent-inbox/list` | 无 | `{schemaVersion:1,lockers:[Locker]}` |
| `POST /api/agent-inbox/read` | lockerId, limit(1..500,默认100), includeConsumed(默认false) | `{messages:[Message]}`，只读 |
| `POST /api/agent-inbox/consume` | lockerId, messageIds[] | `{consumed:[原ID],busy:[原ID]}`；未找到或已消费不重复返回 |
| `POST /api/agent-inbox/subscriptions` | lockerId | `{subscriptions:[Subscription]}` |
| `POST /api/agent-inbox/subscribe` 或 `/mode` | lockerId, sessionId, mode | `{subscription:Subscription}`，默认 silent，只接边界之后消息 |
| `POST /api/agent-inbox/backlog` | lockerId | 显式转发积压；全部当前全文订阅者参与，既有计划不变 |
| `POST /api/agent-inbox/recover` | lockerId | 继续现有计划与满足边界的新消息，不自动包含旧积压 |
| `POST /api/agent-inbox/delete` | lockerId | 独立删除容器，未完成全文意图阻止删除；MCP 不导出 delete |
| `POST /api/plugins/{plugin_id}/route` | source, sourceMessageId, content, target | 插件须仍在已加载 catalog。target.kind=`agent_inbox` 自动按 source 建 Locker，或 `session_queue`+sessionId；user_inbox 明确 409 |
| MCP `agent_inbox` | operation, locker_id, message_ids, session_id, mode, limit | 上述 list/read/consume/subscriptions/subscribe/mode/backlog/recover；不执行 URL 或 shell |

`source` 是 `{pluginId,instanceId,botId,scope,peerId}` 字符串对象，HTTP 路由 pluginId 由 URL 覆盖，其他字段由插件提供；botId 可为空，其他字段非空。`sourceMessageId` 必须是稳定非空字符串。content 是可 JSON 序列化对象，正文通常 text，结构内容使用 History 的 parts、attachments、envelope。来源身份与内容冲突返回 HTTP409，容量拒绝同样明确409；未注册插件不接收。内部插件可直接调用 `Router(Inbox).route(...)`，无须 HTTP，未选择 Router 的现有业务出口保持独立。

Router 返回 stored 仅证明原消息在 Locker 持久存在，不声称 context/CLI 已接收；队列路由返回 accepted 只证明 Queue 落盘。转发状态在 read.messages[].deliveries 中逐目标可核查。`status:processed` 是一次处理扫描结束，可能仍有 failed/unknown，不等于所有目标投递成功。

前端稳定结构以 [Agent-Inbox-Router-Locker.schema.json](Agent-Inbox-Router-Locker.schema.json) 为准。Locker.id / Message.id / sourceMessageId / queueItemId 各有不同含义，不得互换；界面待处理计数取 pendingCount，共享消费后内容可用 includeConsumed 查询。available=false 仍允许读取历史，不允许新接收/自动转发；不得渲染为已进 context。

### QQ 兼容与生命周期

QQ selective 入站继续先独立写 history，再写共享 Locker，随后 best-effort `/api/qq/notify` 只携带 lockerId 触发 Core 转发。Core 不再把 notify 的 text 当新消息或全文再复制一遍。真实 OneBot 层传入 `event.message_id` 及 channel config.name 作为 instanceId；bot self_id 仍独立保存。新记录没有原生 ID 时明确拒绝，绝不使用当前时间伪造原生 ID。scope=user/group、instanceId 和 botId 都参与唯一 Locker 身份。

`qq_read_inbox` 保留旧 target_id / bot_uin / consume / limit 参数与 messages 响应，额外返回 lockerId/sourceMessageId/identityKind/内容字段。支持可选 target_type(user/group) 和 instance_id 精确读取。省略这两项时，同一 target/bot 的多个 scope/instance 合并 FIFO（旧入口无法区分原来就重叠的目标）；新前端应使用准确 lockerId。consume=true 只返回成功原子消费的条目，不返回竞争中已被自动领取的正文。clear 共享消费可领取项并保留 tombstone，遇到进行中计划返回 delivery_busy；可能已有部分条目消费，调用者需重读。

旧 `qq_subscriptions` 字段与 qq_bind/qq_unbind 接口保留。已有关系在首次 Locker 对齐时映射 full，旧历史默认排除；旧关系同时保存来源匹配默认值，新 Locker 创建事务在首条消息之前应用全文订阅，多个消息先到、通知后到或通知丢失都不移动新消息边界。历史积压不回放。显式 mode 不会被后续通知覆盖；qq_bind 是显式重新设 full，qq_unbind 在无其他匹配旧来源订阅时设 silent。已有固定投递目标不会因解绑而被替换。

插件全局移出 manifest/catalog 时，热加载/配置重载及启动对齐会将已有来源置 unavailable。选中 QQ 网关的显式 Stop 在操作前关闭 QQ 来源接收/转发，内容保留；Start 成功后恢复 QQ 来源。关闭某 Session 的 QQ 工具绑定不会停用共享来源或既有后台业务。内部插件生命周期可调用 `Inbox.set_available(pluginId,bool)`；卸载与内容删除是两个操作。重新安装通用插件后，来源恢复须由插件生命周期显式 set_available(true)，不自动删除/重发 unknown。

### 旧文件迁移与兼容边界

启动与 QQ 首次访问扫描本 profile 的 qq_inbox JSON。迁移不改写、移动、截断或删除旧文件；成功的逐文件 marker 防止消费后复活。导入 ID=`legacy_`+路径/索引/原记录内容的确定性摘要，identityKind=legacy，原旧 id 留在 content。无 bot 域记录留在 botId=""，绝不归到当前默认账号；旧记录缺少可证明的 instanceId 时使用兼容 instanceId=qq。同源旧 ID 即使看似数字也不伪装 native。

旧 inbox 的超过500条保留“最后500条”行为已撤销：新 Locker 容量默认500条未消费内容，满则拒绝新接收，已有消息完整保留。旧文件本身超过容量则明确诊断，不截断；人工应先在隔离副本审阅并安排容量/消费处理。QQ history 现有保留上限不在此改动。迁移期间中断可按确定性 ID 重入；部分已导入内容不会换 ID，但不承诺旧文件与新 SQLite 的跨文件事务。迁移完成后不支持旧版本继续向原 JSON 并发写入；回退必须停源并备份/明确迁移，不能直接启动旧插件并宣称双写兼容。

SQLite 与 Session data 必须位于同一实例 profile。Core 新存储根由 SESSION_DIR.parent 派生，QQ 插件由其 _INBOX_DIR.parent 派生；部署自定义 profile 时两者必须一致。测试 launcher 明确覆写所有根并返回实例 PID/nonce/port/data 根证据，不依赖默认端口。

微信本批不改入站/通知业务；内部统一 Router 已可供后续插件使用，不能把现有微信 notify 宣称为已完成 Locker 迁移。mirror、prefix route、QQ 出站及独立 history 保持原业务。

### 候选运行边界

Queue 适配器先返回持久接纳，再异步广播/唤醒 Worker；CLI 启动迟缓或信号失败不拖延 Locker 消费，既有 Queue/watchdog 保持后续恢复责任。Queue 项保留原 queueItemId、messageId、sourceMessageId、inboxSource、sourceIdentityKind、parts/attachments/envelope/inboxContent。Notification 项仅保留来源/身份元数据及无正文提醒，不包含原 parts 或正文。

Notification 和 full 订阅可以同时命中同一消息；通知失败不阻止全文接纳后的共享消费。已被全文共享消费的消息不因通知失败回到未消费集合，失败通知可在 includeConsumed 的内部回执中诊断。来源停用不取消已持久接纳的 Queue；原 Queue 的 Pause/Illegal/lease/owner/generation 与 CLI handoff 状态仍由既有实现控制。本批没有修改 Worker/Job/scheduler/automation 业务。

显式删除除未完成全文意图外，也拒绝 in_flight/unknown 通知意图，避免删除与正在写 Queue 竞争。已删除消息保留不可复活的身份 tombstone；来源摘要不含已删除正文。此处逻辑删除不会承诺 SQLite 页/备份中的安全擦除。
