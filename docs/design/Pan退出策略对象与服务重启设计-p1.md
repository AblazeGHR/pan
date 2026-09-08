# Pan 服务退出与重启参数契约设计

状态：设计提案，首期目标契约，待实现。本文不证明现有代码已经支持 options、durable lifecycle Job、后台 supervisor 读取冻结参数或本文列出的未来能力。

更新日期：2026-09-07

本文只收敛首期 `/api/main/exit` 与 `/api/main/restart` 的可选 `options` 改造。保留原文件名，已有链接无需改动。

相关文档与核查入口：

- [Pan 启动器与 Windows 生命周期架构](Pan启动器与Windows生命周期架构.md)
- [queue-at-most-once](queue-at-most-once.md)
- `packages/core/main_lifecycle.py`
- `packages/core/worker.py`
- `packages/core/session.py`
- `packages/core/background_jobs.py`
- `packages/web/server.py`

## 1. 首期结论与边界

首期只增加两个既有操作入口对可选 `options` 的统一解析、持久化和传递约束：

```text
POST /api/main/exit
POST /api/main/restart
```

两条入口可以复用必要的服务停止流程，但不能因此合并它们的外部语义：

- `exit`：完成公共停止流程后结束，不启动新的 Core。
- `restart`：完成同一公共停止流程、确认旧 Core 已停止后启动新的 Core，并进入既有的启动/就绪收尾。

首期不借 options 改写既有默认行为。Worker 状态保留、恢复、插件启停、组件管理、失败处理、超时、队列和启动恢复的默认语义，以当前两条接口各自已有行为为准；实现前必须逐项核对，不能以“统一参数”名义偷偷改变它们。

首期明确不支持：

- Worker 状态保留或恢复策略字段；
- QQ、Tunnel、RuleWhisper 或其他组件的启停/保活字段；
- `drain`、`continue_interrupted`、新的超时和失败处理策略；
- preview 接口、预览指纹或高级弹窗协议；
- 任意预设、策略名、`restart=true`、`post_stop_action` 等替代参数；
- 任何尚未有实现和验收证据的字段。

上述项目只能作为未来扩展示例，不能出现在首期可接受的请求中。

## 2. 兼容默认与空对象契约

### 2.1 两个操作入口

操作由路径确定，首期保留以下接口名称：

```text
POST /api/main/exit
POST /api/main/restart
```

不在请求体中再次传入 operation，也不接受用字段把 `exit` 改成 `restart` 的隐式切换。

错误和执行状态继续通过现有的操作状态查询入口表达，名称保持不变：

```text
GET /api/main/exit/status
GET /api/main/restart/status
```

实现时应使这些查询（或其等价的持久化读取路径）能够定位 lifecycle Job；本文不宣称当前 status 响应已经包含本文规定的全部 Job 字段。

### 2.2 首期允许的请求形状

首期只允许“没有 options 覆盖”的请求。以下三种情况必须分别对两条接口都保持其既有对外行为，且归一化为同一类空 options 请求：

| 请求 | 首期解释 | 兼容要求 |
|---|---|---|
| 无 body | 未提供 options | 使用该入口原有默认行为 |
| 空 JSON 对象 `{}`，即无 `options` | 未提供 options | 使用该入口原有默认行为 |
| `{"options": {}}` | 显式提供空 options | 使用该入口原有默认行为 |

这里的“原有默认行为”包括但不限于状态保留、恢复、Worker/插件处理、停止失败处理以及 Exit/Restart 各自的收尾差异。首期不能因为保存 Job 或增加解析层而改变这些行为。

请求体若存在，`options` 必须是 JSON 对象。首期空对象以外的 options 均不开放；`null`、数组、字符串、数字以及其他非对象值都应在产生停止副作用前拒绝。

### 2.3 未知和未支持字段必须拒绝

以下情况都属于参数错误，不能接受后忽略：

- 顶层出现 `options` 以外的字段；
- `options` 内出现任意字段；
- 使用未来示例中的字段、策略名或组件名；
- 字段类型不符合请求 schema，或请求体不是允许的 JSON 形状。

拒绝必须发生在停止 Worker、关闭入口、启动 supervisor 或其他破坏性副作用之前。错误响应沿用现有 API 的参数错误契约；错误事实也应记录到可查询的生命周期 Job（若 Job 尚未创建，则至少保留现有请求错误语义，不把请求伪装成已接受的 Job）。

## 3. 统一校验、解析与传递

两条入口必须共用同一套解析器和校验规则，只由路由提供 operation：

```text
识别 operation（exit 或 restart）与权限
→ 解析请求体
→ 将无 body、{}、options={} 归一化为空 options
→ 拒绝未知字段、非对象 options 和首期未支持字段
→ 解析当前 operation 的既有默认行为，但不改写其语义
→ 原子创建 lifecycle Job，并冻结 requested_options / effective_options
→ 关闭新的生命周期入口
→ 公共停止流程读取冻结结果并执行
→ 按 operation 执行 Exit 或 Restart 的专属收尾
```

`options` 是请求覆盖层，不是让每个下游模块重新解释的一组布尔开关。解析完成后，后台执行、Worker 停止协调、supervisor 和 Restart 的新服务启动都只能读取 Job 中的冻结结果；不能重新读取请求体、当前配置或浏览器状态来推导另一个行为。

空 options 的 `effective_options` 必须明确表示“沿用该 operation 的既有默认行为”，而不是凭空引入一组新的首期默认值。其具体序列化字段由实现与现有生命周期状态模型核定，但必须满足：

- `requested_options` 与归一化后的空对象可追溯；
- `effective_options` 创建后不可变；
- Exit 与 Restart 的 operation 不被下游覆盖；
- 配置或运行状态在 Job 创建后变化，不会让同一 Job 重新解析出另一份结果。

如果解析、能力核验或 Job 持久化失败，不得进入后续破坏性停止步骤。活动生命周期 Job 的并发和重入规则也必须在创建时原子约束，不能只依赖进程内存标志。

## 4. Durable lifecycle Job 与错误查询

首期需要把生命周期请求和执行事实放进 durable lifecycle Job；本文描述的是必须具备的设计事实，不宣称当前仓库已有相同 schema。

Job 至少应能关联和查询以下信息：

```text
job_id / request_id / operation / schema_version
requested_options / effective_options
当前 phase / outcome / 时间戳
公共停止步骤与 operation-specific 步骤的结果
旧 Core 身份、必要时的新 Core 身份
错误类型、错误详情、日志或诊断位置
```

约束如下：

1. 在任何可能导致请求连接中断的停止动作前，Job 和冻结参数必须已经持久化成功。
2. `effective_options` 是执行输入的冻结副本；step result 是执行事实，不能反向改写 options。
3. 后台 worker、detached supervisor 和新 Core 启动流程通过 `job_id` 读取同一 Job，不从原始 HTTP 请求中取参数。
4. supervisor 必须能在旧 Core 退出后继续写入最终 phase、outcome 和错误；Exit 结束后原 HTTP 连接不可作为完成通知的唯一载体。
5. status 查询或等价的持久化读取路径必须能区分“未接受”“执行中”“停止失败”“Exit 已结束”“Restart 旧服务已停但新服务未就绪”等事实，不能只返回一个模糊的 offline/ready。
6. 错误必须可查询，且不能被后续的 Core offline、Restart ready 或二次清理覆盖。未确认的步骤不能伪装成成功。

Job 的最终结果应区分至少以下语义：公共停止未完成、Exit 已停止、Restart 正在启动、Restart 已就绪、部分失败、失败或超时。具体枚举名称以实现核定，但不能把 Exit 的结束误报成 Restart 的 ready，也不能把旧 Core stopped 误报成新 Core ready。

## 5. 公共停止流程与 operation-specific 收尾

### 5.1 公共停止流程

两条入口只复用必要的停止阶段；它们不共享后置动作。公共流程的目标顺序为：

```text
1. 完成统一鉴权、解析、校验，并持久化 lifecycle Job
2. 以原子方式关闭新的生命周期入口，防止同一实例并发操作穿透
3. 启动并确认 detached supervisor 已取得 job_id 和冻结结果
4. 按现有语义协调 Worker、历史/结果持久化和进程停止
5. 执行现有服务级停止清理，并记录每一步的实际结果
6. 形成停止屏障，验证旧 Core 的 PID、创建时间、checkout/entry 身份和 listener 归属
7. 将公共停止阶段的 phase、错误和诊断写回 Job
```

公共流程不得顺便增加状态保留、恢复、QQ/Tunnel/RuleWhisper 控制或新的 drain 行为。组件和 Worker 的处理必须调用现有语义；首期没有对应实现和能力核验时，不增加通用组件生命周期接口。

supervisor 的职责是继续执行已接受的 Job 和验证过的低层服务操作，不自行解释未来 options。它不能杀未知进程，也不能仅凭脚本返回码或 PID 文件宣布旧服务已停止/新服务已就绪。

### 5.2 Exit 的专属收尾

```text
公共停止流程完成
→ 不启动新的 Core
→ 写入 Exit 的终态与逐项结果
→ 生命周期 Job 结束
```

Exit 停止后结束是首期必须保留的外部语义。即使 Exit 与 Restart 使用相同的停止函数，也不能因为复用 supervisor 形状而启动服务或套用 Restart 的恢复流程。

### 5.3 Restart 的专属收尾

```text
公共停止流程完成
→ 确认旧进程和 listener 已消失
→ 启动同一 checkout 的新 Core
→ 以真实 HTTP、进程身份、listener 和持续存活证据确认 readiness
→ 写入新 Core 身份、启动结果和 Restart Job 终态
```

Restart 只有在旧服务停止屏障满足后才能启动新 Core。旧服务已停但新服务未就绪时，Job 必须保留两件事的区分：旧 Core 的停止事实，以及 Restart 的失败/未完成事实。新 Core ready 也不能掩盖前面未确认或失败的 Worker/停止步骤。

## 6. 状态、恢复与组件边界

首期只保证不改变既有语义，不把下列概念借 options 暗中引入：

- live Worker status、Session 的 last legal state、合法 offline 与生命周期 Job 是不同事实；保存某个持久状态不等于进程仍在运行，也不等于自动继续。
- 无 body、无 options 和空 options 不新增恢复意图、不清空 durable queue、不改写既有状态保留规则。
- QQ、Tunnel、RuleWhisper 的部署边界、进程归属、共享关系和动作能力尚未由本文统一定义；“插件 manifest 存在”不等于已有可调用的组件生命周期接口。
- 首期不增加 `keep`、`restore_previous`、组件依赖排序、Tunnel 断线后动作或跨 checkout 资源控制。

实现或验收中若发现现有 Exit/Restart 的默认行为不一致，先记录事实和迁移方案，再决定是否需要独立兼容层；不能用 options 解析器直接替换默认行为。

## 7. 未来扩展示例（非首期契约）

以下只是未来可能的表达方式，用于保留设计方向；当前实现不得接受这些字段，也不能把它们写入“首期已支持”列表：

```json
{
  "options": {
    "workers": {
      "stop_mode": "drain",
      "preserve_legal_states": ["running"],
      "resume_on_next_start": "continue_interrupted"
    },
    "components": {
      "qq": { "on_stop": "stop" },
      "tunnel": { "on_stop": "keep" },
      "rulewhisper": { "on_stop": "stop" }
    },
    "failure_handling": {
      "worker_stop_timeout": "abort"
    }
  }
}
```

这些示例仍需分别完成 schema、能力发现、状态竞争、持久化恢复、权限、UI/CLI/MCP 和真实 Windows E2E 设计后，才能成为某一后续阶段的契约。尤其是：

- `drain` 必须定义完成事件与停止屏障的竞争边界；
- `continue_interrupted` 必须有可发现、可重入的恢复记录和 provider/CLI 协议；
- QQ、Tunnel、RuleWhisper 必须先确认实际受管实例和作用域；
- `preview` 若未来增加，必须是无副作用接口，不能替代正式提交时的再次校验；
- 高级弹窗只是交互方式，不能让后端在停止中途等待浏览器，也不能成为唯一参数校验入口。

未来扩展仍应遵守同一原则：入口统一解析，副作用前拒绝不支持的组合，Job 持久化并冻结完整结果，后台和 supervisor 只执行冻结结果。

## 8. 首期验收矩阵

| 场景 | 请求形状 | 预期解析/执行 | 必须验证 |
|---|---|---|---|
| Exit 兼容调用 | `POST /api/main/exit`，无 body | 走 Exit 既有默认行为 | 不改变状态保留、恢复、Worker/插件处理；停止后不启动 |
| Exit 空 JSON | body `{}` | 与无 body 等价 | 不产生新的 options 语义 |
| Exit 显式空 options | body `{"options":{}}` | 与无 body 等价 | 归一化结果一致，Job 可追溯 |
| Restart 兼容调用 | `POST /api/main/restart`，无 body | 走 Restart 既有默认行为 | 公共停止后才启动；保留 Restart 原有差异 |
| Restart 空 JSON/空 options | body `{}` 或 `{"options":{}}` | 两者与无 body 等价 | 不改变恢复或组件启停默认行为 |
| 未知顶层字段 | 例如 `{"foo":1}` | 副作用前拒绝 | 不停止 Worker、不启动 supervisor、不创建“已接受”假 Job |
| 未知/未来 options 字段 | 例如 `{"options":{"workers":{...}}}` | 副作用前拒绝 | 不能接受后忽略，错误原因可诊断 |
| 非对象 options | `null`、数组、字符串或数字 | 副作用前拒绝 | 不进入公共停止流程 |
| Job 持久化失败 | 任一兼容调用 | 拒绝继续破坏性步骤 | 不出现“已停止但 Job 不可查”的成功假象 |
| 冻结后配置变化 | Job 已接受后修改配置/运行状态 | 后台和 supervisor 按原 Job 执行 | 不重新解析请求，不生成第二份 effective options |
| 公共停止失败 | Worker、旧 Core 或身份验证失败 | 记录逐步事实和错误 | 不把未确认步骤标为成功，不启动第二个 Core |
| Exit 收尾 | 公共停止完成 | 结束并写最终 Job | 没有 Restart 的 start/readiness 步骤 |
| Restart 收尾 | 公共停止和旧 listener 消失 | 启动同一 checkout 的新 Core | 旧/新 Core 身份与 readiness 分开记录 |
| 错误查询 | 后台或 supervisor 产生错误 | status/持久化读取路径可查询 | 不被 offline、ready 或二次清理覆盖 |

验收必须分开记录：参数拒绝、Job 持久化、公共停止、Exit 收尾、Restart 启动、错误查询以及真实 Windows 进程/HTTP 证据。单元测试、mock supervisor、静态文档检查不能替代真实生命周期 E2E；未验证的 QQ、Tunnel、RuleWhisper、恢复、drain、preview 和高级弹窗能力不得标为首期完成。

## 9. 实现前核实事项

1. 逐条记录无 body、无 options 和 `options={}` 在当前 Exit/Restart 中的真实默认行为，作为兼容基线。
2. 核对两条入口的现有停止调用链、supervisor 脱离点和旧 Core 身份验证边界。
3. 确认 lifecycle Job 的持久化位置、原子创建、阶段迁移、重入和服务重启后的发现方式。
4. 明确 Exit 结束、Restart 启动这两个 operation-specific 收尾不会被公共停止流程覆盖。
5. 在单独的后续设计中核实 Worker 状态保留、QQ/Tunnel/RuleWhisper、drain、continue_interrupted、preview 和高级弹窗是否真的可开放。

本文的首期完成标准只有：空 options 兼容、未知字段拒绝、统一校验/解析/传递、Job/effective options 冻结、公共停止流程复用且收尾语义分离，以及后台/supervisor 错误可查询。其他完整参数方案均不属于首期实现。
