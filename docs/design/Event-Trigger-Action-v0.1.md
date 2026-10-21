# Event–Trigger–Action v0.1 设计稿

日期：2026-10-22
状态：讨论基线，可用于实现拆分；文中明确区分已确认方向与建议默认值。
范围：本文件汇总本次讨论，不表示授权实现、派发、合并或服务操作。

## 1. 目标与决策状态

建立 Core / Plugin Event → Trigger → 可选实时 State → Action 的自动化闭环。

### 1.1 用户已确认

- Trigger 直接调用 Action，不强制经过 Job；审批暂不纳入。
- Action 是可复用、可参数化的操作定义，能力范围允许覆盖所有 API 和命令；当前较窄限制不是长期产品边界。
- Trigger 直接引用 Action ID，独立于 Group/build。修改 Action 可以影响后续执行；需要隔离时复制 Action 后引用副本。现有 Group immutable release/pin 语义不变。
- 参数支持固定值和 Event 字段取值，首版不引入脚本表达式。
- 同一 Trigger 首版串行，后续触发排队；不同 Trigger 独立运行。未来支持串行／并行双模式。
- Trigger 记录创建来源，当前支持 user、agent（附 Agent ID），未来可扩展 plugin。创建来源不参与鉴权。
- 用户通过前端 enable Trigger；启用后自动调用 Action 不再进行调用者鉴权或逐次审批。
- 初期接受旧审批设计的本地密钥换取内存 Bearer token 的用户鉴权程度。
- 技术方向采用 Registry + 进程内 Event Bus + 外部适配器，先持久化触发执行记录，不立即建设完整 Event Store。
- 首个案例由 restart 改为：running 计数正数归零后，对目标 Session 执行现有 Pause Everything，便于通过暂停及队列状态验收。
- 形成“对符合条件的 Session 执行操作”的 Action 模板方向。

### 1.2 本稿采用的建议默认值

以下来自讨论中的建议，尚未逐项获得用户确认，不能视为冻结决议：

- 新建 Trigger 默认关闭；多个 Event 为 OR；首版一个 Trigger 调用一个 Action。
- State 支持布尔与比较；同次求值缓存同字段，匹配时求值一次。
- TriggerRun 固定 Action ID 和解析参数，执行开始时读取 Action 最新定义。
- 停用时取消尚未开始的执行，已开始执行继续；修改已启用 Trigger 的执行配置要求用户 token。
- 普通失败不自动重试；崩溃中断的 running 记录标记 uncertain，不盲目重跑。
- Session 选择器的三种来源、查询条件首版 AND、执行开始时固定目标集合，以及首例手选目标。

## 2. 概念与边界

| 对象 | 职责 |
|---|---|
| EventDefinition | 事件类型的声明：稳定 ID、归属、schema、版本、说明 |
| Event | 一次发生的事实：唯一 ID、类型、来源、时间、payload |
| Trigger | 持久化规则：事件订阅、可选条件、Action 引用、参数、执行模式、启用状态 |
| State | Trigger 激活后实时读取的状态字段 |
| Action | 复用当前操作定义及执行基础，承载 API 或命令 |
| TriggerRun | 一次事件命中一个 Trigger 后形成的执行记录，用于排队、追溯和恢复 |
| Job | 有明确意图及执行周期／条件的任务实例；本版不要求每次触发都创建 Job |

注册事件类型与发布事件实例分离。Event 表达事实，不作为审批决定命令。TriggerRun 不预先等同于 Job，未来延时、周期、审批等需求再与 Job 衔接。

## 3. 主链路

```text
Core / Plugin 发布 Event
  → 校验事件定义、发布资格和 payload
  → 匹配 enabled 且依赖可用的 Trigger
  → 求值可选 State 条件
  → 解析 Action 参数
  → 持久化 TriggerRun
  → 按 Trigger 串行调度
  → 读取最新 Action，校验参数并执行
  → 保存结果
```

Event Bus 同时可以向插件订阅者分发。每个订阅者独立接收，同一个事件不是被多个消费者竞争取走。

## 4. Event 与 State 契约

事件 envelope 建议如下，字段命名在实现时统一现有约定：

```json
{
  "id": "evt_...",
  "type": "workers.all_idle",
  "schemaVersion": 1,
  "source": {"type": "core", "id": "workers"},
  "occurredAt": "2026-10-22T00:00:00Z",
  "payload": {"runningCount": 0}
}
```

- 稳定事件类型 ID 不引用 Python 模块路径或 MCP 工具名；命名规范实施前统一。
- payload 符合注册 schema；事件 ID 用于关联和同一事件去重，不自动提供副作用去重。
- 多 Event 订阅按 OR；不支持事件 AND、NOT、历史窗口。
- State 在事件匹配后实时读取，支持 AND / OR / NOT 及 eq、ne、gt、gte、lt、lte。
- 建议同次求值对同字段只读一次；无条件直接通过。
- 读取或求值失败应留下可见诊断，不执行 Action；条件不满足属于正常跳过。
- 排队后不重新求值；执行时业务有效性由 Action 自身检查。

## 5. Trigger 配置与运行

### 5.1 数据

| 字段 | 内容 |
|---|---|
| id / name / description | 身份与展示 |
| createdBy | `{type: user}` 或 `{type: agent, id: ...}`，未来 plugin |
| enabled | 启用状态，新建建议 false |
| eventTypes | 事件类型列表，OR |
| condition | 可选 State 条件 |
| actionId | Action 引用 |
| arguments | 固定值或 Event 字段映射 |
| executionMode | 首版 serial，未来 parallel |
| revision / 时间字段 | 配置并发控制及追溯 |

createdBy 用于追溯和展示，不构成身份凭证；Agent ID 复用现有身份，不另造身份体系。

参数映射示例：

```json
{
  "sessionId": {"from": "event", "path": "payload.sessionId"},
  "message": {"from": "literal", "value": "Task finished"}
}
```

不支持任意脚本。字段缺失、类型不符或参数无效产生错误记录，不调用 Action。

### 5.2 串行与配置变化

- 同 Trigger 按接收顺序串行，不同 Trigger 独立；不承诺跨生产者全局顺序。
- TriggerRun 保存命中时的 Trigger revision、Action ID、解析参数、事件及条件信息。
- Trigger 配置修改影响后续生成的记录；Action 修改影响尚未开始的执行。
- Action 开始时读取一次定义，本次执行期间不随定义修改而变化，保存实际使用定义的摘要或标识。
- Action 删除、停用、参数契约变化等导致不可执行时，明确失败，不静默改用其他 Action。
- 建议关闭 Trigger 时取消 queued 记录，running 继续；删除前先停用，历史记录保留。
- 未来并行模式在执行器中扩展，具体并发上限、积压和顺序策略另定。

## 6. Action 与用户授权

### 6.1 执行能力

扩展现有 Action 执行基础，覆盖所需 API 和命令，不维护另一套事件专用 Action 白名单。具体 API 描述、命令执行配置、超时和输出格式结合现有实现收口。

API 调用复用已有业务逻辑；参数校验、业务前置条件和错误处理继续生效。启用后的调用不再因原请求者身份、用户 token 过期等重新鉴权。需要鉴权上下文的现有 API 应通过明确的内部自动化执行入口适配，避免重复实施用户鉴权，同时保留业务检查。

Group/build 继续承担插件发布，Trigger 不要求构建 Group。

### 6.2 用户 enable

复用旧审批最小用户鉴权方向：

- 本地持久随机密钥，用户输入后换取 12 小时 Bearer token。
- token 仅保存在前端内存，刷新重新登录。
- 后端 enable 接口必须验证 token，不能只隐藏前端开关。
- Agent 创建 Trigger 强制 disabled；普通创建／更新入口不能夹带 enabled=true。
- token 过期不停止已启用自动化；Action 修改不要求重新 enable。

建议用户控制启停、删除及修改已启用 Trigger 的执行配置均校验 token；Agent 可维护未启用草稿。此管理边界尚待确认。

接受初期本机信任边界：能够读取密钥文件或控制已登录浏览器的 Agent 不在隔离保证内。此机制也不等于给所有 Pan HTTP API 加鉴权。

## 7. 技术结构与插件

| 层 | 方案与职责 |
|---|---|
| Capability Registry | 扩展事件声明、schema、插件归属和可用性 |
| Core Event Bus | asyncio 进程内发布、订阅、取消订阅与扇出 |
| Trigger Engine | 匹配、State 求值、参数解析、持久化 |
| Trigger Executor | 独立串行队列、Action 调用、结果保存 |
| Plugin Adapter | 外部插件传输与 Core 接口转换 |

Event Bus 属于始终可用的 Core Service。内部插件使用稳定接口，不依赖私有路径；外部插件通过适配器接入，不要求 Python 或同进程运行。首版不引入独立 Broker。

插件启用时注册声明并建立订阅；停用时撤销运行时订阅和发布资格，事件定义标记不可用。依赖它的 Trigger 保留配置并展示不可用状态，重新启用后恢复。

发布身份来自插件运行时／连接上下文，不能仅凭 payload 冒充其他插件。这里的发布资格和 schema 校验，与 Trigger 启用后不再进行 Action 调用者鉴权是不同边界。

各订阅者错误、耗时隔离；队列有容量边界，满载可见，不静默丢弃或无限创建任务。容量、拒绝方式在实现中明确。普通插件订阅首版仅实时通知，不承诺断线补投。

## 8. 持久化、状态与恢复

Trigger 配置与 TriggerRun 持久化。具体存储复用当前项目设施，实施前核实，不为此另造服务。

TriggerRun 保存 Run/Event/Trigger/Action ID、Trigger revision、事件与解析参数、条件信息、实际 Action 摘要、各阶段时间、结果和错误。

```text
queued → running → succeeded / failed
queued → cancelled
running（进程中断）→ uncertain
```

| 重启前状态 | 恢复 |
|---|---|
| queued | Trigger 启用且依赖可用时恢复；停用则取消；临时依赖不可用则保留等待并展示原因 |
| running | 标记 uncertain，不自动重跑 |
| succeeded / failed / cancelled | 保留，不重跑 |
| uncertain | 等待类型专属对账或用户处理 |

同 Event ID + Trigger ID 只形成一条执行记录。普通失败不自动重试；未来按 Action 幂等性及对账契约扩展。

可靠性边界：

- 执行记录落盘后可恢复；Event 发布到落盘前存在丢失窗口。
- 同一事件的匹配与执行记录持久化阶段结束后，才启动对应 Action；部分落盘失败要明确报告，已落盘记录仍保留。
- 不承诺所有匹配记录事务性全有或全无，不承诺副作用 exactly-once。
- 尚未执行记录的恢复不代表已产生副作用的操作可以安全重试。
- Event Log / Outbox、历史重放及插件消费确认后续按需要增加。

## 9. Session Operation Action 模板

用户要求形成“对符合条件的 Session 执行操作”的模板。以下是建议的首版结构，筛选细节尚待确认。

```text
执行开始 → 解析 Session 选择器 → 固定目标 ID 集合
         → 对每个目标执行操作 → 保存逐目标结果
```

### 9.1 选择器

| 模式 | 用途 |
|---|---|
| explicit | 指定 Session ID 列表；建议首例采用手选 |
| event | 从 Event 字段取得 Session ID／列表 |
| query | 按当前 Session 字段筛选 |

后续 query 任务已明确采用白名单字段与有界 and/or/not 谓词；字段为 id/name/adapter/managedBy/readonlySession/pinned/workspaceIds，按字段类型限制 eq/ne/in/contains。禁止脚本、SQL、正则与任意属性遍历。执行开始一次查询并冻结目标；限制与精确定义见实施补充及 core-contract 的 Session query 小节。此前 AND-only 和候选字段建议由此次明确授权替代。

Trigger State 判断“本次是否执行”；选择器判断“对哪些 Session 执行”，两者独立。

### 9.2 执行及结果

- 在 Action 开始时查询并固定目标，保存匹配时间与目标 ID；执行中不不断加入新目标。
- 建议首版逐目标串行，单目标失败不阻止其他目标；不与 Trigger 未来并行模式混为同一个开关。
- 0 个目标记录成功且 matched=0；全部成功为 succeeded，任一失败则总体 failed，并保留部分成功事实。
- 目标被删除、状态变化等由操作自身校验并逐项报告。
- Run 保存 matched/succeeded/failed 数量及逐目标结果；中断时已落盘结果保留，未确定目标不盲目重跑。
- 模板生成普通 Action，复用现有参数和执行体系，不提前推广为任意资源批处理引擎。

## 10. 首个闭环与真人验收

### 10.1 行为

1. 严格使用现有 Worker running 字段计数，不建立另一份权威计数。
2. 正数 → 0 发布 workers.all_idle；0 → 0 不发布；再次正数 → 0 可再次发布。
3. 示例 Trigger 默认关闭；用户登录后 enable。
4. 调用基于 Session Operation 模板的 Action，对明确选择的目标执行现有 Pause Everything。
5. 暂停范围完全沿用现有产品语义；实现前核实其真实 API、持久状态及对队列的作用，不在模板中另造暂停生命周期。

### 10.2 验收证据

- 在目标 Session 准备可观察的待处理队列，选取不会影响本次验收控制的目标。
- 确认默认关闭时不自动暂停；启用后通过真实正数 → 0 变化触发。
- 检查 Event、TriggerRun、目标列表及逐目标操作结果可关联。
- 检查目标现有 Pause Everything 状态，以及待处理队列在该状态下不再继续交付。
- 对照未选中的 Session 不受此次操作影响。
- 单独验证同 Trigger 串行、错误隔离及 queued 重启恢复。

Action 返回成功不能单独证明队列实际停止；真人验收应同时观察真实队列行为。具体如何在归零时保留可观察积压，需要结合现有队列交付和 running 变化时序确定验收步骤，不能仅靠模拟状态推断。

## 11. 第一版管理界面与交付标准

界面保持现有产品英文惯例，提供 Trigger 列表、配置、启停、依赖状态和执行记录。能够查看失败原因、实际目标及逐目标结果；用户 enable 时展示所引用 Action 和配置。

第一版完成标准：

- Core 与示例插件能够注册、发布、订阅事件。
- 用户配置 Event、可选 State、Action、参数，登录后 enable；Agent 不能绕过 enable。
- 串行排队和不同 Trigger 隔离符合约定。
- Action 动态引用生效，不依赖 Group 重建。
- TriggerRun 持久化、恢复及 uncertain 可观察。
- Session Operation 模板首例可用，Pause Everything 真人队列验收通过。

代码／静态检查、自动测试、真实服务联调和真人验收分别报告，不能相互代替。

## 12. 实施前需核实及后续范围

实施前有界核实当前 Action 执行层、Capability Registry、Pause Everything API、running 事件源、队列时序及存储方式。旧审批实现只能作为鉴权和恢复机制参考，不直接整树合入。

优先确认本稿 1.2 的建议默认值，尤其已启用 Trigger 修改权限、停用队列处理及 Session 筛选字段。未来扩展 parallel、并发／积压策略、幂等与重试、可靠事件投递、外部插件确认、Job 衔接。

参考：

- [Scepter 探索稿](D:/project/Pan-main/docs/design/Scepter_Event.md)：历史探索，不整体视作当前冻结决议。
- [旧审批 Job 设计](D:/project/pan-worktrees/integrate-jobs-conditional-api-20260930/docs/design/job-unification/CONDITIONAL_JOBS.md)：旧分支机制参考，不证明当前可合或服务验收完成。
