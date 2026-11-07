# Job 功能迁移、Action 链与 Scheduler Event 设计汇总

日期：2026-10-24。

状态：供用户审阅的设计汇总，尚未据此宣布实施完成。本文汇总本轮对话中的新增要求、已确认方向及建议细则；“待确认”部分不能当作已冻结要求。本文不包含账号、额度或模型自动策略。

## 1. 目标与迁移标准

最终目标：将旧 Job 的全部功能无损收纳进新 Job 抽象，复用现有成熟底层执行逻辑，以新抽象组合旧能力。

迁移对象包括功能与创建能力，而不仅是既有实例或数据读取。范围至少覆盖 background task、Session 消息、群发、定时任务及 Pan 生命周期任务；实施前须以源码盘点补全清单。

新抽象采用 Action、Job、Trigger、Event 和可选 Job Template。旧 kind 可保留于兼容层，不再作为新产品模型的主要分类；不能仅增加统一投影就宣称功能迁移完成。

保留原有进程管理、独立 Job service、群发目标处理、Queue 持久投递、通知、取消、恢复等执行路径。System Action 封装并调用这些能力，不以简化实现替换原逻辑。

必须保留权限、目标身份、Pause/Illegal、owner/generation/lease、原 Queue ID、幂等和完成边界。unknown/uncertain 不自动重投；持久入队不等于 Agent 已消费；独立运行及进程树清理能力不能丢失。

过渡期间保留两套明确区分的 Job 入口。旧 Job 在 Job panel 使用独立标签，文档说明边界。在用户确认新 Job 完整承担旧能力前，不撤旧入口、不强制转换。

## 2. 旧 Job MCP 对 Agent 无感迁移（用户已明确确认）

调用路径：

```text
Agent 原有 MCP 调用
  → 兼容 MCP 工具入口
  → 系统 Action 与新 Job 执行生命周期
  → 原有底层执行逻辑
```

兼容标准：

- 工具名不变，不要求 Agent 改用 Group 前缀名称或先 discover 再调用。
- 参数、默认值、调用方式及校验含义兼容。
- 返回结构、Job ID、错误含义与通知契约兼容。
- 查询、取消、删除、重启恢复等后续操作继续作用于同一个 Job。
- Agent 无需自行创建 Action、Group、Build 或 Template；兼容所需系统 Action 由产品提供维护。
- 某项功能迁移完成后，原 MCP 创建的实际是经过 Action 的新 Job；兼容层仅适配参数和结果，不能仍偷偷创建旧执行实例。

Group Build 的 MCP 模式本身已通过 MCP tools/list、tools/call 暴露独立工具。无感迁移不要求把旧工具改名为 Build 生成的工具。用户自定义 Group Build 与旧 MCP 兼容导出可以共用 Action 能力，但属于不同入口需求。

按需补齐 Action 的输出 schema、MCP 结果类型及工具描述；不能因封装将原 MCP 结果压成文本而损失既有能力。是否实施额外 MCP 协议能力，以旧工具实际契约和迁移需要为准，不扩成无边界协议重写。

## 3. Job 支持 Action 链（用户新增要求）

一个 Job 从仅引用一个 Action，扩展为允许一条有序 Action 链。Job Template 可保存完整链配置；Template 可选，无 Template 也可创建 Job。Template 是被动配置，不增加启用、停用或执行生命周期。

以下为本轮提出的第一阶段建议细则，供本次审阅一并确认：

| 项目 | 建议规则 |
|---|---|
| 配置 | 有序 steps，每步具有稳定 stepId、Action 引用和参数映射 |
| 顺序 | 串行；前一步满足其完成契约后才启动下一步 |
| 参数 | 可引用 Job 输入、触发 Event 字段和前序步骤结构化输出 |
| 兼容 | 原 actionId + arguments 等价单步骤链；保留旧 API/MCP 调用 |
| 成功 | 全部步骤成功后 Run 才成功，逐步骤保留状态、结果与错误 |
| 失败 | 默认阻断后续步骤，不隐式忽略失败 |
| 取消 | 不再启动后续步骤，通过当前 Action 原取消机制处理运行中步骤 |
| 未知结果 | unknown/uncertain 阻断链，不自动重投或进入下一步 |

重试与恢复以步骤为边界：后续失败不能自动重跑已完成步骤；当前步骤能否重试由幂等、副作用和恢复契约决定。不默认提供补偿或回滚，已发送消息等效果通常不可撤回。

建议审计层次：Run 表示整条链的一次逻辑运行；Step execution 表示该 Run 内某一步；Attempt 表示该步骤的一次实际尝试。需要与现有 Run/Attempt 存储设计逐项适配，不直接假定已实现。

建议在 Run 接纳时固定整条链的 Action 定义、参数映射及执行含义；每步副作用前继续检查当前授权、停用状态及目标门禁。固定执行内容不能绕过后续撤权。

第一阶段建议限于串行链、前序输出映射、逐步骤恢复及完整审计。并行分支、循环、条件跳转和补偿链未纳入本轮已确定范围。Action Group 是工具集合，不等价于 Action 执行链。

## 4. Scheduler 改为 Event 发生器（已确认方向）

Scheduler 复用现有时间计算、时区、等待和恢复实现，将原“到点直接执行”的出口改为“到点持久产生时间 Event”。不改为长期 sleep 的 Action。

```text
Scheduler 事件发生器
  → 时间 Event / Event Store
  → 所有订阅 Trigger 独立匹配、准入
  → Job 的 Action 链，或现有支持的直接 Action 执行
```

职责分工：Scheduler 决定何时产生事件；Trigger 决定条件、参数映射和执行目标；Job 决定链、重叠、恢复、清理与通知；Action 执行具体操作并定义完成契约。

### 4.1 广播语义（用户明确确认）

Event 是广播事实，不是竞争消费的消息。所有订阅该事件源的 Trigger 都能独立处理；一个 Trigger 不会取走 Event，也不能阻止其他 Trigger。

“收到”不等于必然执行：各 Trigger 仍检查条件、enabled、权限及准入规则，并分别记录结果。某订阅者失败或停用不影响其他订阅者。

### 4.2 注册来源与事件实例

引用已有 Scheduler Event，指引用持续产生时间事件的注册来源，不是引用某条历史 Event 实例。

建议每个 Scheduler 有稳定 ID、名称、revision、时间规则、时区及错过触发策略。每次产生的 Event 具有独立且稳定的身份，建议包含：

| 字段 | 含义 |
|---|---|
| scheduleId | 产生事件的调度定义 |
| scheduleRevision | 本次使用的调度版本 |
| scheduledAt | 原定触发时间 |
| emittedAt | 实际产生时间 |
| eventId | 本次触发的稳定去重身份 |

以上为语义字段，具体 JSON 路径及与 canonical system 字段注册表的关系待实现契约审阅，不宣称已经定稿。

Event 持久接纳后，Scheduler 才推进发布进度。发布成功、Trigger 接纳、Job 完成是不同结果。不能假定多个存储天然具备一个事务；需要稳定身份、持久回执和恢复来避免漏发或重复效果。

停机错过的触发策略属于 Scheduler；运行重叠属于 Job overlap；已经开始的步骤失败属于步骤 recovery。三者不能混为一次“重试”。重启不得重投已有 unknown/uncertain 执行。

## 5. Automation 前端结构（用户提出并确认方向）

Automation 包含三个子页：

| 子页 | 内容 |
|---|---|
| Job | 管理任务、Action 链、触发方式、重叠、恢复与通知 |
| Trigger | 管理事件订阅、条件、参数映射、执行目标及 Job 关联 Trigger |
| Event Register | 查看事件来源/结构，注册和管理事件发生器 |

Event Register 管理来源/发生器定义，不把单条发生记录当作注册对象。目前允许用户新建的事件发生器只有 Scheduler；系统与插件的事件类型可供查看和引用，具体可用范围遵循实际注册与授权。

面向用户隐藏组合细节。创建 Job 时若添加 Scheduler 配置，系统自动建立相应 Scheduler（如需新建）和 Trigger，用户无需手工创建三个对象。

Job 显示可读调度摘要，并可跳转关联 Trigger/Scheduler。自动生成的 Trigger 标明所属 Job。Action 和 Template 保留管理入口，Job 编辑器可以选择或跳转，不重复实现管理逻辑；具体导航位置仍待 GUI 设计。

## 6. Job 中添加 Scheduler 的两种方式（用户最新确认方向）

每次添加时间触发时提供两个选项：

### A. 已存在 Scheduler Event

用户搜索并选择已有 Scheduler 事件源，显示名称、调度规则、时区和启用状态。系统创建或维护当前 Job 的关联 Trigger，订阅选中来源并启动当前 Job。

### B. 新建 Scheduler

用户填写名称、时间规则、时区等，系统新建事件发生器，然后创建关联 Trigger 引用它。

```text
添加时间触发
  ○ 已存在 Scheduler Event
      搜索并选择 → 展示调度摘要
  ○ 新建 Scheduler
      填写时间配置 → 新建并引用
```

两种方式最终形成相同引用关系。新建的 Scheduler 进入 Event Register，以后其他 Job 可以搜索复用。不按相同时间配置自动合并来源。

本节替代较早的“默认专属来源、以后再开放复用”建议；从 Job 新建的 Scheduler 不作为特殊私有对象。

### 6.1 关联编辑与生命周期建议

- 修改 Job 的 Scheduler 引用只影响该 Job 的关联 Trigger。
- 修改 Scheduler 定义会影响全部订阅者；编辑时展示引用关系与影响范围。
- 停用 Job 不停用 Scheduler；停用 Scheduler 停止其后续时间 Event，但不等价取消已接纳 Run。
- 删除 Job 清理其自动管理的 Trigger，不自动删除 Scheduler。未引用来源由 Event Register 显式清理。
- 用户从多个页面编辑关联配置时，应有明确归属与 revision 冲突检查；自动管理的 Trigger 默认从 Job 编辑，避免多处配置互相覆盖。
- 新建并引用必须可恢复且幂等，失败不留下意外启用的半成品，不因重试重复创建。

这些细则是本轮建议，尚需用户此次审阅确认；跨存储操作的具体实现方式不能仅用“原子保存”四字代替设计。

## 7. 旧 Scheduler 与 MCP 的迁移

旧 MCP、HTTP、GUI 原有可表达能力必须逐项映射。旧定时创建调用内部改为 Schedule → Event → Trigger → Job → 系统 Action，Agent 不改原调用。

已有记录迁移保留原身份、时间配置、下次触发信息及执行证据。每个任务切换后只有一个调度 owner，不能让新旧调度同时触发。

无法无损解释的记录保留旧路径并明确缺口，不能猜测为 immediate、开启 recovery 或扩大目标。旧格式与新 schema 的字段映射必须结合真实 loader 和调度代码另出实施清单。

手动立即执行创建手动 Run，不擅自移动周期计划。Schedule 的修改、补发和已产生 Event 的关系须有版本与稳定身份约束。

## 8. 实施与验收门禁

先形成逐项矩阵：旧功能 → 旧 MCP 契约 → 新 Action/Job/Scheduler 表达 → 复用执行路径 → GUI/HTTP/MCP 入口 → 验收证据。

验收至少覆盖：

1. 原 MCP 调用样例不修改即可完成同一业务，同时内部证明确实经过系统 Action 和新 Job。
2. 原查询、取消、删除操作可继续定位同一 Job，返回和通知契约兼容。
3. Action 链顺序、参数映射、失败阻断、取消、逐步骤恢复及 unknown 不重投。
4. 同一时间 Event 广播给多个 Trigger，独立失败和停用互不阻塞。
5. 选择已有与新建 Scheduler 两条 GUI 路径，以及引用关系、共享修改、停用和删除。
6. Scheduler 重启、错过时间、重复发布、接纳中断恢复及单 owner 切换。
7. 旧数据、未知字段、原身份、权限及安全门禁保持；真实副本证据与合成负控分别报告。

TA 交付、MA 验收、主线整合、远端 CI、真实迁移和用户验收是不同状态。未覆盖事项不得宣布完成。最终由用户确认新 Job 完整承担旧能力后，才决定旧入口退役。

## 9. 本次审阅后仍需细化的契约

以下没有在本轮对话中最终确定，不阻碍审阅整体方向，但实施前必须明确：

- Action 链具体 schema、输出引用语法、缺失值和类型错误，以及旧 Run/Attempt 的兼容映射。
- 是否允许同一 Job 添加多个时间触发，以及各关联 Trigger 的命名、编辑和删除方式。
- Schedule 具体时间类型、错过触发选项、修改后下次触发计算与发布游标；以旧能力无损盘点为依据。
- Scheduler Event 类型名、字段路径、身份算法、来源命名和授权范围。
- 新建 Job/Scheduler/Trigger 的跨存储恢复协议、禁用草稿及失败后清理规则。
- 已接纳 Run 后修改链或 Scheduler 的精确版本行为；默认建议运行使用接纳时固定配置。
- 共享 Scheduler 的删除保护、被停用来源的选择提示及引用影响展示。
- 新旧 UI/MCP 对应清单，以及无感迁移所需输出类型与错误兼容细节。

这些事项应在方案和源码核对中主动提出；不能静默选取会改变旧功能的默认行为。

## 2026-11-06 P0 对照说明（不改写上文）

上文是 2026-10-24 的设计汇总，其中两项已被后续决定取代，其余能力清单继续作为兼容基线：

1. **§2"旧 Job MCP 对 Agent 无感迁移"中的"工具名不变、参数不变、返回不变"**：`JOB_FINAL_MODEL_20261025.md` §0 已明确取代"长期保留原 MCP 工具名"，当前基线把创建类旧工具移除并给出 canonical guidance。以哪一方作为验收依据需 MA 明确签收，P0 矩阵按现状记为"工具移除 + 显式拒绝"，不写成迁移完成。
2. **§4 图中"或现有支持的直接 Action 执行"**：D2-A 一度要求 Trigger 效果全部收敛到 Job，用户 2026-11-04 的 U01/U11 又恢复了"直接 Action 一次执行、Trigger 可指向 Action 或 Job"。当前代码只实现了 `targetJobId`（`packages/jobs/final_contracts.py`），Action 目标是 P4 交付。

继续有效并已进入 P0 矩阵的部分：background task、Session 消息、群发、定时任务、生命周期的能力保全要求；Event 广播语义（一个 Event 可被多个 Trigger 独立匹配）已由 `packages/automation/engine.py` 的匹配/准入分离实现；"不重投 unknown/uncertain""持久入队不等于已消费""进程树清理不能丢失"等边界继续作为验收条件。

逐项映射见 [P0 覆盖矩阵](../action-unification/ACTION_UNIFICATION_P0_CAPABILITY_MATRIX_20261106.md)。
