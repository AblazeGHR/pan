# Pan 服务退出与重启参数契约设计

状态：设计提案，待实现。本文中的参数、组件能力接口与预览接口均为目标契约，不代表现有代码已经支持。

更新日期：2026-09-07

适用范围：Pan Core 退出与重启、Worker 关停、Session 合法状态、组件启停、恢复意图和 Windows detached supervisor。保留原文件名以兼容已有链接。

相关文档与代码：

- [Pan 启动器与 Windows 生命周期架构](Pan启动器与Windows生命周期架构.md)
- [queue-at-most-once](queue-at-most-once.md)
- `packages/core/main_lifecycle.py`
- `packages/core/worker.py`
- `packages/core/session.py`
- `packages/core/background_jobs.py`
- `packages/web/server.py`

## 1. 决策摘要

采用“具体参数决定行为”的生命周期契约：

1. `/api/main/exit` 与 `/api/main/restart` 接受相同的停止参数，前者停止后结束，后者停止后启动。
2. API、CLI、MCP 和前端使用同一套参数语义，不通过命名策略隐式捆绑多个行为。
3. Worker 状态保留、下次恢复、组件启停、超时与失败处理分别选择。
4. 预设只负责填写默认参数，选择后仍可逐项修改。
5. 入口一次性补齐、校验并冻结完整 `effective_options`，持久化到 durable lifecycle Job。
6. Worker、组件控制、supervisor 和新服务启动执行同一份参数，不在下游重新读取配置推导行为。
7. 公共停止流程只实现一次；实时状态、最后合法状态、恢复意图与实际执行结果分别记录。

内部使用不可变的 `LifecycleOptions` 数据模型承载参数即可，不需要为每种组合创建策略类。二值行为可以使用布尔值，多值行为使用枚举；关键是语义明确、组合可验证。

## 2. 当前实现与设计边界

当前 Worker 实时状态是 `worker.status`，Session 持久化状态是 `session.last_legal_worker_state`。

当前 `shutdown_all()` 接受 `recovery_drain_timeout` 和 `mark_legal_offline`，取消相关任务、flush 历史、停止进程树，并在要求记录 offline 且 runtime 确认停止时写合法状态。Exit 使用 `begin_shutdown()` 和 `shutdown_all(mark_legal_offline=True)`。

原设计指出 Restart 尚未统一经过 Exit 的 Worker 停止阶段。实现时重新核验 Restart、supervisor 和 lifespan 调用链，确保服务级出口统一收尾，避免二次清理覆盖保留状态。

现有 Tunnel 控制与插件 manifest 加载不等于已有通用组件生命周期接口。QQ、Tunnel、RuleWhisper 的实际部署、归属、共享关系和可用动作需要分别核实。本文不将参数化接口、自动 continue、组件统一管理或 Windows E2E 视为已完成。

## 3. API 与参数

### 3.1 操作入口

保留现有路径：

```text
GET  /api/main/exit/status
POST /api/main/exit
GET  /api/main/restart/status
POST /api/main/restart
```

operation 由路由确定，不接受可能冲突的 `restart=true` 或 `post_stop_action`。内部停止后动作由 operation 派生一次并持久化。

示例：停止 Worker，仅保留符合条件的 running 合法状态；不新增恢复请求；停止 QQ、RuleWhisper，保留 Tunnel。

```json
{
  "workers": {
    "stop_mode": "terminate",
    "preserve_legal_states": ["running"],
    "resume_on_next_start": "none",
    "stop_timeout_seconds": 30
  },
  "components": {
    "qq": { "on_stop": "stop" },
    "tunnel": { "on_stop": "keep" },
    "rulewhisper": { "on_stop": "stop" }
  },
  "failure_handling": {
    "worker_stop_timeout": "abort",
    "component_stop_failure": "abort"
  }
}
```

组件 ID 必须对应实际注册项。当前部署不支持的动作必须在停止副作用发生前拒绝。

### 3.2 Worker 参数

| 参数 | 语义 | 建议内置默认值 |
|---|---|---|
| `workers.stop_mode` | `terminate`：必要持久化后终止；`drain`：禁止新消费，等待当前执行结束后停止 | `terminate` |
| `workers.preserve_legal_states` | 首期允许 `[]` 或 `["running"]`；扩展状态需单独定义语义 | `[]` |
| `workers.resume_on_next_start` | `none`、`recover_durable_queue`、`continue_interrupted`，仅开放已实现能力 | Exit：`none`；Restart 目标值：`recover_durable_queue` |
| `workers.stop_timeout_seconds` | 每个 Worker 的停止期限，含 drain 等待或 terminate 阶段；有限正数，上限由 schema 公布 | `30` |

所有 stop_mode 最终均停止目标 Worker，不包含退出 Core 后让 Worker 独立继续运行。terminate 不能跳过历史 flush 等必要持久化。

drain 等待期间必须继续处理 stdout 和完成事件，不能先取消结果处理再等待完成；不支持该模式的 adapter 在预检时拒绝。

普通停止确认后写 offline，只有符合第 5 节条件的状态才保留。无活 Worker 的 Session 不因本次退出被批量改写。

Restart 的恢复默认值必须与实际恢复能力一起发布，不得接受后不执行。恢复参数边界见第 6 节。

### 3.3 组件参数

| 参数 | 语义 | 建议内置默认值 |
|---|---|---|
| `components.<id>.on_stop` | `stop`：停止指定实例；`keep`：保持该实例存活 | 可独立存活的外部组件为 keep；Core 内组件只能 stop |
| `components.<id>.on_start` | `none`：本轮不启动；`restore_previous`：恢复操作开始时正在运行的实例 | Restart 中 stop 对应 restore_previous，keep 对应 none |
| `components.<id>.stop_timeout_seconds` | 每个组件停止动作期限；有限正数 | `30` |

on_start 仅适用于 Restart，Exit 提交该字段返回参数错误。Exit 不隐式设置未来某次手动启动的组件行为。

`keep + restore_previous` 为冲突组合，保留实例不重复启动。`stop + none` 表示本轮重启后保持该组件停止。

省略的组件在入口依据注册表和默认规则展开到完整参数。受影响组件集合冻结后，下游不得静默追加新组件。

### 3.4 失败处理

| 参数 | 语义 | 建议内置默认值 |
|---|---|---|
| `failure_handling.worker_stop_timeout` | `abort`：停止后续破坏性步骤；`force_stop`：supervisor 对已验证归属的残余运行时和 Core 强制收尾 | `abort` |
| `failure_handling.component_stop_failure` | `abort`：停止后续破坏性步骤；`continue`：记录失败后继续停止 Core | `abort` |

以上是新契约建议默认值，不宣称完全兼容旧接口 best-effort 行为。发布时明确无参数调用的兼容差异，不能以重构为名偷偷改变行为。

force_stop 不允许杀未知进程、跳过错误记录或重放任务。非超时的 Worker 持久化/停止错误默认中止，不能借超时参数掩盖。服务身份不匹配、必要 Job 写入失败等错误始终阻止后续破坏性步骤。

## 4. 默认值、校验与执行一致性

统一优先级：

```text
显式请求字段 > 用户配置默认值 > 接口内置默认值
```

- 嵌套对象按字段补齐；列表整体替换，不做集合并集。
- 显式 false、空数组必须生效，不通过 truthy 判断回退。
- `preserve_legal_states: []` 明确表示不保留，配置不能重新加回 running。
- null 不等于缺省，除非 schema 明确允许。
- 预设由调用方展开为参数，编辑后的显式字段照常覆盖默认值。
- 系统约束用于校验，冲突返回错误，不暗中修改用户选择。
- 未知字段、组件、未实现的枚举和能力必须拒绝，不接受后忽略。

入口执行：

```text
确定 operation 与权限
→ 合并参数、解析组件能力与依赖
→ 校验当前实例和完整参数
→ 原子占用生命周期操作并持久化 effective_options
→ 关闭新入口
→ 执行冻结参数
```

不可变 `LifecycleOptions` 模型与 API 使用相同字段语义，共享 schema 驱动校验、客户端类型和表单约束。Worker 快照、组件快照与步骤结果另存，不反向改写请求参数。

下游只读取 Job 中的完整参数，配置变化不影响已经接受的操作。执行时能力或身份变化应报告失败，不能自动降级为另一种行为。

## 5. Worker 状态保留

### 5.1 分离事实

- live status：当前运行时事实，不跨进程证明存活。
- last legal state：最近确认的合法状态，可选择不被此次受控退出覆盖。
- 恢复记录：未来恢复哪个任务、采用何种方式。
- lifecycle Job：本次请求与实际执行结果。

保留 running 表示“停止前确认的合法 running 未被此次退出改写”，不表示进程仍在运行，也不是自动继续命令。

### 5.2 快照与竞争处理

弹窗和预览中的状态只供展示。实际停止前，与完成事件和状态迁移通过同一锁或等价机制串行协调，再建立最终快照。drain 在等待当前任务结束后、实施停止前建立收尾快照。

快照包含 Session ID、Worker ID、generation、live status、last legal state、in-flight task 标识和时间。

仅当快照中 live status 与 last legal state 均为 running，且请求保留 running，才保留已有值。二者不一致时记录诊断，不伪造或回写 running。

用户确认期间或停止协调期间任务已经完成，应接受完成事实，不能按旧快照把 done 改回 running。停止发起后到达的有效结果也要按 generation 与既有结果协议协调，最终保留资格必须反映已接受结果。

### 5.3 统一收尾

```text
停止未确认：不写合法 offline，记录失败
停止确认且符合保留条件：不覆盖该合法状态
停止确认且不符合保留条件：执行正常 offline 收尾
```

收尾记录 job_id、session_id、worker_id、generation、source。shutdown、kill 与 lifespan 清理避免重复写入覆盖已保留结果。

普通 Worker kill/restart、EOF、watchdog 和崩溃继续遵循自己的协议，不继承服务参数；异常消失不能冒充合法停止。

API/UI 分别展示 live status、last legal state 与恢复状态。无法连接 Core 时显示连接未知或最后观测状态，不能仅因浏览器断线就宣称服务 offline。

## 6. 恢复与队列

状态保留和恢复意图是独立维度：

| 状态处理 | 恢复选项 | 含义 |
|---|---|---|
| 保留 running | none | 只保留状态，不新增自动继续请求 |
| 写 offline | recover_durable_queue | 按队列协议恢复未交接工作 |
| 保留 running | continue_interrupted | 独立恢复记录指导继续 |
| 写 offline | continue_interrupted | 不依赖 running 字段，通过独立记录继续 |

continue_interrupted 仅在恢复协议与 adapter 已支持时开放。不能仅因 offline 与 continue 同时出现就拒绝；缺少可恢复任务信息则明确拒绝。

none 表示本次不新增恢复意图，不删除队列、不清除既有恢复记录，也不全局禁止启动时既有恢复机制。UI 必须说明此边界；若需要下次完全禁止自动恢复，应另设计显式抑制契约。

recover_durable_queue 请求下次启动处理符合条件的未交接项；continue_interrupted 额外要求任务、CLI session/transcript、generation、最后可靠持久化位置及 provider 恢复方式。相关意图必须能被下一次启动发现，不能只留在内存。

queue_pending 是持久真源，交接收据不是第二条队列。已越过 CLI 交接边界的项不能因 running 被重发；退出不清空未处理项。

恢复记录使用稳定 ID 与接受/执行状态保证重入不重复。恢复被持久化接受且结果可查询后才确认消费；失败或信息不足保留诊断记录。Core ready 与恢复完成分别报告。

## 7. 组件生命周期

### 7.1 能力与目标

每个受管组件声明：稳定 ID、实例标识、所属 Core/checkout、运行位置、支持动作、能否脱离 Core 存活、依赖、状态探测及启停接口、可验证的进程身份或控制句柄。

运行位置区分 Core 内、独立受管进程、外部共享服务。Core 内组件无法 keep；共享服务停止要求明确作用域与管理权限。

“关闭 QQ”必须对应明确实例，例如 Pan QQ 插件，不能顺带关闭 QQ 客户端、OneBot 服务或共享后端。不同受管资源使用不同目标 ID。

### 7.2 本轮行为与永久配置

on_stop=stop 不修改永久 enabled；永久禁用仍使用配置接口。

Restart 的 on_start=none 是本轮启动约束。启动器、新 Core 初始化和自动拉起逻辑都要读取 Job 启动上下文，避免按全局配置重新拉起。本轮约束在启动完成前有效，下一次独立启动遵循自己的请求/配置。

restore_previous 使用操作开始时持久化的组件运行快照，仅恢复此前运行的实例。发现已有合法实例时复用或报告冲突，不能重复启动。

### 7.3 依赖与生存保障

停止按依赖逆序、启动按依赖正序。保留组件依赖将停止的服务且不能离线存活时，预检拒绝该组合，不把 keep 偷改为 stop；依赖循环也拒绝。

keep 必须保证进程实际存活。如果组件仍在 Core 最终清理的进程树中，先建立经过验证的独立进程归属机制；无法保证就不提供 keep 能力。

通过 Tunnel 发起操作且要求停止 Tunnel 时，先交付接受响应，再执行可能断开连接的步骤。supervisor 必须能在连接丢失后完成必要收尾；其控制能力和执行上下文不能仅存在于 Core 内存。

## 8. 公共停止流程与 supervisor

```text
1. 校验并持久化 Job、effective_options、组件快照
2. 关闭新入口，建立 detached supervisor 并确认就绪
3. 协调 Worker、保存历史、停止并持久化状态收尾
4. 按依赖处理组件 on_stop 并记录逐项结果
5. 写停止屏障，或明确的强制收尾授权及失败事实
6. supervisor 验证旧 Core 身份，停止目标服务
7. 确认旧进程与 listener 消失
8. Exit 结束；Restart 启动同一 checkout 的新 Core
9. 新启动流程应用组件 on_start 和恢复上下文
10. 分别记录 Core readiness、组件结果与恢复结果
```

Worker 与组件步骤的具体依赖顺序由持久化执行计划确定。Worker 持久化依赖的组件不能提前关闭，不按插件名称硬编码绕过依赖。

supervisor 必须在旧 Core 被杀前脱离，等待持久屏障后才执行停止脚本。建立失败时不进入后续破坏性步骤，解除尚可安全解除的闸门并记录失败。

身份检查包含 PID、创建时间、checkout/entry 标识和端口归属，禁止杀未知进程。旧服务仍可能存活时不能启动第二个 Core。

新服务 readiness 必须有真实 HTTP、新进程身份、目标 listener 和持续存活证据，不以脚本返回码或 PID 文件代替；探测端点与现行生命周期协议统一。

Windows 脚本负责低层进程操作，不解释 Worker 参数。生命周期模块负责 Job 协调，组件接口负责各自实例控制。

## 9. Job、并发与失败事实

### 9.1 持久字段

在现有 schema 上扩展或映射以下字段，避免两套同义真源：

```text
job_id / request_id / operation / schema_version
requested_options / effective_options
service_identity / new_service_identity
component_snapshots / worker_stop_snapshots
execution_steps / step_results
workers_stopped / components_stop_finished
phase / core_state / outcome
recovery_record_ids / errors / logs
```

effective_options 不可变；step_results 是受状态机约束的执行事实。旧 Job 按历史版本明确迁移，不用今天的配置重算。

phase 表达 preparing、stopping_workers、stopping_components、stopping_core、starting、restoring、finished 等阶段；core_state 表达 running、stopped、ready、unknown；outcome 表达 pending、succeeded、partial_failure、failed、timed_out。

旧 status 字段可兼容映射，但单一 offline/ready 不能掩盖 Worker 或组件失败。

### 9.2 并发和重入

同一经归一化确认的 Core 实例只允许一个 active Job，通过原子创建/锁定约束，不能仅靠内存标志。

相同 request_id 与相同参数返回原 Job；同 ID 不同参数返回冲突。步骤使用稳定标识，重入前检查已有结果和进程身份，避免重复启动。

关停闸门覆盖新派发、spawn/restart、消费与 recovery。新输入按既有契约拒绝或持久化等待，不接受后丢弃，不穿透闸门运行。

### 9.3 中止与部分失败

abort 只阻止后续破坏性步骤，不代表回滚已停止的 Worker/组件。保留部分完成清单；Core 尚存活时保持可诊断维护状态，协调恢复完成后才重新开放入口。

component_stop_failure=continue 允许继续停止 Core，但最终至少报告 partial_failure，并说明组件是否仍存活。身份校验等不可绕过的约束不受 continue 影响。

force_stop 记录超时和强制决策，不能伪造 workers_stopped 成功屏障；未确认的 Worker 状态保持未确认，Core 停止不能证明前面收尾成功。

旧服务已停但新服务启动失败，分别记录 core_state 与 outcome 并保留恢复记录。Core ready 但组件恢复失败，也不能报告全操作成功。

## 10. 前端、预览与查询

“退出时询问”属于前端交互偏好，后端没有 ask_user=true，不在关停中途等待浏览器继续回答。

弹窗提供状态保留、恢复请求、组件启停选项；高级区提供停止方式、超时与失败处理。组件能力决定可选项，禁用项说明原因。前端最终提交具体值，CLI/MCP 使用相同权限和参数校验。

可选增加无副作用的 `POST /api/main/lifecycle/preview`，接收 operation 与 options，返回完整参数、影响范围、能力错误和执行顺序。不创建 Job、不关闭闸门、不锁定 Worker 状态。

正式提交重新校验。前端提交已审阅完整参数，并携带预览返回的作用域版本/目标指纹；组件或实例范围变化返回冲突供重新预览，不静默扩大资源范围。Worker 运行状态变化按第 5 节实际快照处理。

接受响应返回 HTTP 202、job_id、operation、effective_options 和阶段。参数错误 422，活动操作或作用域冲突 409，权限错误沿用现有鉴权契约。

status 返回参数及实际结果，浏览器断开或停止轮询不取消 Job。Core offline 后原 HTTP API 不可访问；supervisor 将最终结果落盘，供本地工具或下次启动读取，不能承诺离线期间原接口仍可查询。

## 11. 模块职责与实施

- API：鉴权、schema、默认值、预览、接受请求和状态响应。
- lifecycle：实例互斥、冻结参数、阶段协调、supervisor、启动上下文。
- Worker：停止模式、并发快照、历史持久化、进程停止与合法状态收尾。
- 组件层：能力、作用域、依赖、身份、启停和探测。
- Job 存储：原子创建、状态迁移、执行事实与重入。
- 启动器/脚本：已验证目标的低层启停，不自行解释业务参数。

实施顺序：

1. 共享 schema、resolver、能力校验、Job 持久化和状态回显，只开放已实现参数。
2. 统一 Exit/Restart Worker 收尾和 supervisor 屏障，实现独立 running 保留。
3. 逐组件接入已核实的生命周期能力与本轮启动约束。
4. 接入前端弹窗、预览与 CLI/MCP，验证显式参数一致性。
5. 接入队列恢复记录，单独实现和验证 continue_interrupted 后再开放。

无参数旧调用、status 和旧 Job 需明确兼容迁移表。默认行为改变作为可见契约变更交付，过渡映射只放入口，下游始终执行同一完整参数。

## 12. 验证与验收

### 12.1 参数与 Worker

- 各入口相同输入生成相同 effective_options。
- 空数组、false 覆盖默认值，不做隐式并集；未知或未支持值在副作用前拒绝。
- 状态保留与恢复独立，不能接受未实现的恢复能力。
- 确认/停止期间完成的任务不被旧快照改回 running。
- idle、held、error、无活 Worker、generation 变化不误保留 running。
- lifespan 二次清理不覆盖保留结果，停止未确认不写合法 offline。
- drain 仍处理完成事件，abort/force_stop 留下不同的实际结果。

### 12.2 组件与跨进程

- stop 不改 enabled，keep 的进程确实在 Core 退出后存活。
- stop + none 不被本轮自动启动覆盖；restore_previous 不拉起原先停止的组件。
- 依赖顺序正确，其他 checkout、共享服务和实例不受影响。
- Tunnel 断开后 supervisor 继续完成并落盘。
- 配置变化不重算冻结参数，重入不重复启动。
- 部分失败不被 Core offline/ready 掩盖，中止不谎报回滚。

### 12.3 Windows 隔离 E2E

使用确认归属的独立 checkout 和测试端口，保护其他实例及共享组件。检查真实进程树、listener、HTTP、Job 和 Session metadata：

- 普通 Exit、保留 running 且不新增恢复的 Exit、普通 Restart。
- 组件 stop/keep 与 Restart restore_previous/none 的实际效果。
- 完成事件与停止竞争、超时、启动失败、断连。
- 旧 Core/Worker 已停、新 Core 身份正确，无非预期孤儿或重复实例。
- 队列未丢失，未越过交接协议重放，恢复结果与记录一致。

单元测试、mock supervisor 和前端静态检查不替代真实 Windows 验证；未测组件和 provider continue 能力必须明确列出。

## 13. 实现前核实事项

1. QQ、Tunnel、RuleWhisper 的服务边界、进程归属、共享关系与依赖。
2. 无参数 Exit/Restart 的现行失败处理与启动恢复行为，确定迁移差异。
3. Worker 完成事件、状态迁移的锁与 generation 边界。
4. Job 与 Session 恢复记录的原子交接、持久发现及幂等方式。
5. 启动器和 Core 初始化应用本轮组件约束的位置。

这些核实决定首期开放哪些参数。契约保持一致：调用方明确选择行为，入口补齐并冻结参数，后台执行并返回逐项事实。
