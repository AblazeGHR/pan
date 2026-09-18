# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)。旧 overview 已删除，历史内容已迁移到本文件，不再并行维护。
> 归档记录见 [developLog.md](developLog.md)。
>
> 维护者：Pan SMA；迁移日期：2026-09-12

## 工作流控制

### 当前概况

- 项目根：`D:\project\Pan-main`（`git rev-parse --show-toplevel` 已核对）。
- `main`：`HEAD`（当前工作流文档提交；代码基线 `37d7668`，未 push。T-040/T-041/T-042/T-043/T-044/T-045/T-046/T-047 均已本地合入）；已归档任务见 [developLog.md](developLog.md)，代码交付与无代码验证的开发者验收入口见下方唯一任务卡。
- `practical`：2026-09-18 上次核对 `D:\project\Pan` 已检出 practical、HEAD 为 `b466a76`；本轮 T-043/T-044 尚未同步 practical；前端 pnpm build 已通过。
- TA worktree 根：`D:\project\pan-worktrees`；新 TA worktree 必须先用 `git worktree add` 注册后交付。
- 受保护服务：`8768`（未经用户授权不得重启/停止/修改）；隔离验证实例按任务分配端口（当前 T-031 = `8767`、T-033 = `8766`；`8765` 被其他 worktree 占用，不得停止或复用）。

- 整体状态：批次 A、D 已由用户确认验收；当前新增的 UI 修复 `T-048` 正在执行，Job/Workspace UI 仍分别等待 `DEC-004`/`DEC-005`。
- 当前焦点：完成 `T-048` 的移动端 Manage 关闭行为与 Session 设置点击外关闭；其余已合入改动继续等待开发者验收。
- 可执行：`T-048` 无决策阻塞；Job/Workspace UI 仍按各自决策局部阻塞。
- TA 执行中：`T-048`，等待派发结果。
- 最近收口：批次 A、批次 D 已由用户确认全部验收（2026-09-18）。

### 需要用户处理

- 待开发者验收：[附件/运行时批次 B–E](tasks/acceptance-batches.md)——`T-027`、`T-027.1`、`T-027.2`、`T-030`、`T-031`、`T-033`、`T-034`、`T-037`、`T-038`、`T-040`、`T-041`、`T-042`、`T-043`、`T-044`、`T-045`、`T-046`、`T-047`。
- 待用户决策：`DEC-004`（Job 标签页/Create Job UI）、`DEC-005`（Workspace 标签页 UI）。
- 待授权：无。外部阻塞：无；真实 provider/CLI、HTTP/WS、Chromium、移动端等未验证项随对应验收清单保留。

### MA 正在推进

- 为 `T-048` 核对现有移动端 Manage 路由、Sidebar 弹出状态和 SettingsPopover 的点击边界，随后派发实现与回归测试。

### 等待与暂停

- 决策阻塞：`T-043` UI 阶段 → `DEC-004`；`T-044` UI 阶段 → `DEC-005`；解除条件：用户明确对应 UI 形态和交互范围。
- 用户暂停：T-026 → 等待用户恢复；T-029 → 等待用户明确恢复；T-039 → 等待用户开启方案讨论。

### 资料入口

- 长期约束：[constraints.md](constraints.md)
- 归档日志：[developLog.md](developLog.md)
- 任务详情：以下四个状态索引中的唯一任务卡；复杂证据按任务条目中的链接按需阅读。
- 开发者验收单：[acceptance-batches.md](tasks/acceptance-batches.md)
- 唤醒条件：TA 报告、用户决策/验收、外部条件变化、overview/约束变更或再次启动工作流。

## 已决策/授权

### AUTH-001：测试通过后直接合入本地 main

- 状态：已授权；作用域：隔离 worktree 中测试通过且无冲突的功能修复和文档整合
- 允许：创建 worktree、提交、合入本地 `main`、更新工作流文档；不包含 push、发布、受保护服务操作或覆盖用户 dirty/untracked 文件
- 来源/时间：用户会话，2026-09-12；持续有效，直至用户撤销或修改
- 详情：[决策方案附件](tasks/decisions.md)

### DEC-001：文件复制/拖入输入框的正式实现方案

- 状态：已决定（A + C，2026-09-13）；不再等待用户选择
- 结论：普通文件复制/粘贴和拖入，目录第一阶段拒绝；采用结构化 `AttachmentRef/parts`，兼容旧 text/Markdown 协议
- 影响：T-023 决策阻塞已解除，T-025 已承接正式实现；目录能力另立任务
- 来源：用户明确回复“A+C”，2026-09-13
- 详情：[决策方案附件](tasks/decisions.md)

### DEC-002：输入框、跨 Session 附件与发送事务语义

- 状态：已决定（2026-09-15）；不再等待用户选择
- 结论：structured chip/inline 不跨 Session；合法正文 editor/server-file 可复用；Send 乐观清空，失败恢复可编辑副本
- 影响：T-027/T-027.1/T-027.2 已按此实现；T-031/T-037 提供隔离证据
- 来源/时间：用户会话，2026-09-15
- 详情：[决策方案附件](tasks/decisions.md)

### DEC-003：报告与任务身份的最小继承规则

- 状态：已决定（2026-09-17）；不再等待用户选择
- 结论：正式任务使用 `taskId`；普通 `agent_send` 不需要独立业务编号，入队时继承目标 TA 当前任务的 `taskId`（没有任务上下文时可为空）；report 只回传本次执行输入携带的 `taskId`
- 不实施：`sourceQueueItemId`、`deliveryUnitId`、完整事件模型；`queueItemId` 仅保留为队列内部技术 ID
- 约束：不得在 report 生成时按历史“上一条/最近任务”猜测归属；任务归属必须在输入入队或 Worker 领取时确定并随本次执行保存
- 影响：关闭原 DEC-003；T-032 调查结论转为 T-040 实现依据；相关 D-2 改由 T-040 处理
- 来源/时间：用户明确回复“按照这个原则推进”，2026-09-17
- 详情：[DEC-003 方案与证据](tasks/decisions.md)

### DEC-006：时间 Job 的创建者、接收者与 Agent 入口

- 状态：已决定（2026-09-18）；不再等待用户选择
- 结论：时间 Job 分离保存 `creatorSessionId`/所有者与 `targetSessionId` 或 `targetSessionIds`/实际接收者；创建者不等于接收者。Agent A 可以创建发送给 Agent B 的 Job，终态通知按目标接收者路由，不自动复制回创建者。
- 身份语义：创建者 ID 用于所有权、审计和权限；目标 Session ID 用于实际消息投递。系统 Job 无创建者时显示 `automation`，不显示 `unknown`。
- 通知结构：终态通知单独暴露 `jobId`、状态、目标和可用的创建者元数据；调度器/后台 Job 的完成、失败、取消通知使用 `////by pan system` 前缀。
- 消息区分：Agent 创建的定时消息沿用 `////by agent : <creatorSessionId> | <title>` 和 `sourceSessionId`；只有调度器产生的终态通知使用 `////by pan system`。
- Agent 入口：复用现有 Pan MCP 的 `agent_message_job_create/get/list/update/cancel` 体系，不新建重型 MCP 服务。
- 影响：T-043/T-045 后端 Job 模型、终态通知和 Agent/MCP 文档已完成；不解除 `DEC-004`/`DEC-005` 的 UI 阻塞。
- 来源/时间：用户明确要求，2026-09-18

## 待决策与待授权

### DEC-004：Job UI 形态与时间编辑入口

- 状态：待决策
- 影响：T-043 的 App Settings 独立 Job 标签页、Create Job 窗口、时间显示与调整交互
- 已核实事实：用户要求所有时间类型 Job 在 UI 中显示时间且可调整；具体标签页布局、创建/编辑控件和时间编辑交互尚未确定。
- 未决定时：T-043 可以先设计和实现后端/Agent/MCP 契约；UI 实现阶段暂停。
- 阻塞：T-043 的 UI 实现与 UI 验收阶段
- 最终决定：待用户确定
- 决策来源：用户会话，2026-09-18

### DEC-005：工作区标签页 UI 形态与交互范围

- 状态：待决策
- 影响：T-044 的工作区标签页、Session 展示布局、切换和归属编辑 UI
- 已核实事实：后端已支持 Workspace 持久化、Session 多工作区归属、查询和管理接口；用户明确暂不决定 UI。
- 未决定时：T-044 后端进入开发者验收，不冻结其他后端、Agent/MCP 或测试工作；UI 实现阶段暂停。
- 阻塞：T-044 的 UI 实现与 UI 验收阶段
- 最终决定：待用户确定
- 决策来源：用户会话，2026-09-18

> 以下四节是兼容性的状态索引；每个任务只在其中保留一张任务卡。阶段/状态、下一动作、解除条件和关系以唯一任务卡为准。代码任务必须先实际合入 `main`，之后才进入开发者验收；未合入时只写“开发者验收：合入后”。无代码任务明确“合入 main：不适用”后进入开发者验收。

## 已合入main的改动/已完成的任务

### T-005：practical 启动脚本与 MCP 依赖检查

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是（`9138a79`）｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-006：Codex 全局额度缓存

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-008：带行号 Markdown 文件链接在 Editor 中打开

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是（`cea2611`）｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；T-034 的独立浏览器证据仍按其任务卡追踪
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-010：Codex Session 上下文窗口与压缩阈值设置

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-011：附件 Markdown 链接与附件下载改造

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-012：附件选择与 New Session 目录输入统一改造

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-013：浏览器后台恢复前端状态

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；T-034 的独立浏览器证据仍按其任务卡追踪
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：T-034 提供独立浏览器证据；不承接未完成验收
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-014：Memory 关闭时的 minimal requirements 分层

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-015：Pan 通知、系统提醒与 msgBridge

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；未验证桌面能力按详情保留
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：T-034 提供部分独立浏览器证据；桌面未验证项可另立任务
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-016：Pan MCP 工具清单与 skill 同步审计

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-017：搜索结果双击文件夹后搜索目录不更新

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-018：Windows Markdown 带行号链接打不开

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；T-034 的独立浏览器证据仍按其任务卡追踪
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：与 T-008 能力相邻；T-034 提供部分独立证据
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-019：Codex Steer 按 Worker running 时可见性

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；真实 Codex running Steer 未验证项按详情保留
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：T-034 提供部分独立证据；不承接未验证项
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-020：Session Detail、System prompt 与 Codex quota

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是（`c1a5ace`）｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：T-035 是独立移动端后续，不是本任务未完成项
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-021/T-022：附件拖放 UI 与真实后端链路

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收；T-027 组继续追踪后续完整语义
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：后续完整语义由 T-027/T-027.1/T-027.2 承接
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-024：Pan 解释器 config.json 配置与环境变量优先级

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是（`17632cc`）｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 A 已验收
- 解除条件：已解除（用户确认批次 A 全部验收）
- 关系：无
- 详情：[T-005～T-024 详情](tasks/legacy-merged.md)

### T-027.1：输入框、发送事务与粘贴/拖入状态修复

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`7a9c7e89`）｜开发者验收：待确认
- 下一动作：开发者验收输入清空、失败恢复、竞态与粘贴/拖入；解除：确认
- 解除条件：开发者完成该任务验收
- 关系：T-027 的实现子任务；与 T-027.2 分工
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-027.2：服务端路径投影与 editor 文件链接跨 Session 拖动

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`99f1774a`）｜开发者验收：待确认
- 下一动作：开发者验收路径投影、editor 拖入和跨 Session 引用；解除：确认
- 解除条件：开发者完成该任务验收
- 关系：T-027 的实现子任务；与 T-027.1 分工
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-030：done 事件与状态指示灯延迟

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`7f2667b`）｜开发者验收：待确认
- 下一动作：开发者验收状态指示灯终态刷新；T-036 是独立后续
- 解除条件：开发者完成该任务验收
- 关系：T-036 是独立后续，不是 T-030 未完成项
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-035：移动端 Session Details 全屏

- 任务类型：代码改动｜阶段/状态：验收 / 最终完成｜合入 main：是（`82823ad`）｜开发者验收：已确认（用户，2026-09-18）
- 下一动作：无；批次 D 已全部验收
- 解除条件：已解除（用户确认批次 D 全部验收）
- 关系：无；用户已接受验收清单中未单独完成的真机 safe-area、触摸惯性、软键盘和地址栏收缩验证
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-037：structured attachment 跨 Session 隔离

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`083a09d`）｜开发者验收：待确认
- 下一动作：开发者验收 structured 隔离及合法 server_file/editor 复用；解除：确认
- 解除条件：开发者完成该任务验收
- 关系：修复 T-031 发现的缺陷；T-031 复验已完成
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-038：主动 kill running worker 的完成报告

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`317e46e`）｜开发者验收：待确认
- 下一动作：开发者验收 running/queued kill 的单次 completion report；D-2 由 T-040 处理
- 解除条件：开发者完成该任务验收
- 关系：承接 T-033 的 D-1；D-2 已由 T-040 独立承接
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

## 已完成且合入 main 不适用的验证任务

### T-031：附件与发送链路真实隔离实例 E2E

- 任务类型：无代码任务｜阶段/状态：验证 / 待开发者验收｜合入 main：不适用｜开发者验收：待确认
- 下一动作：开发者验收真实 8767 HTTP/WS/Chromium 隔离证据；解除：确认或另立未验证项
- 解除条件：开发者完成该任务验收
- 关系：为 T-027/T-027.1/T-027.2 提供独立验证；T-037 已承接其发现的直接缺陷
- 详情：[T-031 及运行时详情](tasks/attachment-runtime.md)

### T-033：Worker 生命周期、队列与报告投递真实 E2E

- 任务类型：无代码任务｜阶段/状态：验证 / 待开发者验收｜合入 main：不适用｜开发者验收：待确认
- 下一动作：开发者验收真实 8766 生命周期与报告证据；D-2 由 T-040 处理
- 解除条件：开发者完成该任务验收
- 关系：T-038 已承接 D-1；D-2 已由 T-040 独立处理
- 详情：[T-033 及运行时详情](tasks/attachment-runtime.md)

### T-034：已合入批次的浏览器/UI 真实运行证据补全

- 任务类型：无代码任务｜阶段/状态：验证 / 待开发者验收｜合入 main：不适用｜开发者验收：待确认
- 下一动作：开发者验收 8792 浏览器证据；未验证项需明确另立或接受
- 解除条件：开发者完成该任务验收
- 关系：为 T-008/T-013/T-015/T-018/T-019 提供独立验证，不承接其未完成验收
- 详情：[T-034 详情](tasks/paused-followups.md)

## 当前执行与待验收的近期改动

> `T-048` 是当前执行中的 UI 修复；其余条目为已实现或已合入、等待开发者验收或收口的近期交付。

### T-048：移动端 Manage 关闭行为与 Session 设置点击外关闭

- 任务类型：代码改动｜阶段/状态：待派发 / 实现中｜合入 main：否｜开发者验收：待确认
- 来源：用户于 2026-09-18 明确新增 UI 改动任务 1。
- 目标：移动端 Manage 使用与 Session Details 一致的右上角 X 关闭；关闭后保持 Sidebar 弹出状态，不直接导航进入 Session。Session 设置弹层改为点击设置窗口外任意位置即关闭，窗口内控件交互不误关闭。
- 验收：移动端从 Sidebar 打开 Manage、点击右上角 X 后仍停留在 Sidebar 弹出态；桌面端既有 Manage Modal 行为不回归；打开 Session Settings 后点击聊天区、Sidebar、其他页面空白和窗口外控件均关闭；点击设置窗口及其下拉/按钮不关闭；Escape/原有 gear toggle 保持有效。
- 决策/授权：无新增决策门；用户已授权实现。UI 具体形态仅限本任务明确的两项交互，不扩展 Job/Workspace UI。
- 边界：使用隔离 worktree；不改 `D:\project\Pan`、不 push、不操作 8768；补充相关 Vitest/组件测试，并在依赖可用时执行前端 build。
- 阻塞：无。
- 下一动作：派发 TA 实现并报告 worktree、commit、测试及未验证项。
- [ ] 实现移动端 Manage 右上角 X 与 Sidebar 保持逻辑
- [ ] 实现 Session Settings 点击窗口外关闭
- [ ] 补充/更新交互测试并通过前端 build（若依赖可用）
- [ ] 合入 main 后开发者验收

### T-042：本地文件链接可拖入附件且保留 Editor 打开

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`c5e0a19beac448f76b5415714446d794b68fec6a`）｜开发者验收：待确认
- 来源：2026-09-18 用户明确“开始做任务”。
- 目标：正文原始本地文件链接首次拖入即可成为附件，点击仍打开 Editor 并定位行号；合法 server_file 跨 Session 复用，保留现有附件隔离。
- 关系：T-027.2 的直接补交付；原任务不能因先前自动化通过而归档。
- 工作树：`D:\project\pan-worktrees\local-link-drag-20260918`；分支 `feature/local-link-drag-20260918`；基线 `b466a76d714fcd68852d3b466de07f71a180aa65`。
- TA：`ses_0010355a6b0bf824`；派发 taskId `T-042-local-link-drag-20260918`；派发返回 queued / worker-4，完成报告已订阅。
- 下一动作：开发者在 practical/main 同步版本验收原始链接点击、首次拖入、发送和跨 Session 复用；解除：开发者完成验收。
- 边界：不改 practical、不 push、不操作 8768；保留 main 既有 dirty 文档；不得削弱 opaque 引用及服务端校验。
- 阻塞：无。
- [x] 实现与自动化/隔离浏览器验证
- [x] 合入 main
- [ ] 开发者验收（合入后）

### T-027：输入框、附件与发送链路审查及方案

- 任务类型：代码改动（总任务）｜阶段/状态：整合 / 待开发者验收｜合入 main：不适用（实现由 T-027.1/T-027.2 合入）｜开发者验收：待确认
- 下一动作：开发者验收 T-027 总体语义与 T-027.1/T-027.2 交付；解除：完成该验收批次
- 解除条件：开发者完成 T-027/T-027.1/T-027.2 验收批次
- 关系：T-027.1/T-027.2 为实现子任务；T-031/T-037 为独立验证/修复；T-036 不承接未完成项
- 详情：[T-027 及运行时详情](tasks/attachment-runtime.md)

### T-032：MA→TA 多任务排队时的报告粒度调查

- 任务类型：无代码任务｜阶段/状态：调查 / 已完成，结论已转实现｜合入 main：不适用｜开发者验收：T-040 合入后待确认
- 下一动作：调查结论已转入 T-040；T-032 保留为调查记录，不再等待用户决定
- 解除条件：T-040 完成并通过验收
- 阻塞：无；后续实现由 T-040 承接
- 关系：调查任务；T-040 是基于本调查结论的新实现任务
- 详情：[T-032 及运行时详情](tasks/attachment-runtime.md)

### T-040：普通消息继承当前任务 taskId，report 回传本次输入 taskId

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`22254eec284ae1418095e4bfb517622edd05a3d1`）｜开发者验收：待确认
- 下一动作：开发者验收 taskId 继承、任务切换、重启恢复与 report 配对语义；验收后再判断是否归档
- 解除条件：开发者完成该任务验收
- 阻塞：无
- 关系：承接 T-032 的最小实现结论；不实施 sourceQueueItemId、deliveryUnitId 或完整事件模型
- 工作树/分支：`D:\project\pan-worktrees\task-id-inheritance-20260917` / `feature/task-id-inheritance-20260917`，基线 `4fa4bb4a2dfee80f654c3d004d89d450acdf965a`
- TA/任务：`ses_0010355a6b0bf824` / `T-040-impl-01`，已完成；业务任务号仍为 `T-040`
- 提交/测试：实现提交 `22254eec284ae1418095e4bfb517622edd05a3d1`，已合入本地 `main`；定向 5 passed、相关回归 110 passed、核心队列/Worker 基线 66 passed、compileall 和 skill 检查通过；未 push、未操作 8768
- 未验证：真实 Pan HTTP/WS、真实 provider/CLI、Chromium E2E、前端 TypeScript 编译；提交钩子因既有类型依赖缺失使用 `--no-verify`
- 详情：[DEC-003 方案与证据](tasks/decisions.md)

### T-041：基础终态先持久化广播，enrich/usage 异步串行后处理

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`b466a76d714fcd68852d3b466de07f71a180aa65`）｜开发者验收：待确认
- 下一动作：开发者验收真实 provider/CLI 下的终态时序、usage 最终一致性以及 HTTP/WS/Chromium 表现
- 解除条件：开发者完成该任务验收
- 阻塞：无
- 关系：承接 T-036 调查结论；不改变 T-040 的 taskId 继承语义，不引入完整事件模型
- 工作树/分支：`D:\project\pan-worktrees\terminal-broadcast-impl-20260917` / `feature/terminal-broadcast-20260917`，基线 `22254eec284ae1418095e4bfb517622edd05a3d1`
- TA/任务：`ses_c28ff8194c88f3dc` / `T-041`，已完成
- 提交/测试：实现提交 `b466a76d714fcd68852d3b466de07f71a180aa65`，已合入本地 `main`；TA 定向回归和排除两项既有失败后的 tests 全集通过；MA 合入后复跑终态/oneshot/taskId 关键回归 `15 passed`
- 未验证：真实 HTTP/WS、真实 provider/CLI、Chromium E2E、真实 cbc/kimi usage 延迟及 Worker 崩溃后的线上恢复时序；仓库级 pytest 因可选 nonebot 缺失无法收集 QQ 测试
- 边界：未 push、未操作 8768；未过滤全量仅有既有 quota 文档断言失败
- 详情：[T-036 详情](tasks/paused-followups.md)

## 后续验收、决策依赖与暂停事项

> 本节中的后端交付已实现并合入 `main`，尚未完成开发者验收；UI 阶段等待 `DEC-004`/`DEC-005`，其余暂停项等待用户恢复。

### T-043（用户任务 1）：App Settings 独立 Job 标签页与定时消息 Job

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`62ffffb63b4a6ae4c42e9d88f44f4f5a325ca473`）｜开发者验收：待确认
- 目标：在 App Settings 下新增独立的 Job 标签页，展示当前后台 Job；Job 具备 Session 归属和描述字段；用户可以 kill Job。点击 Create Job 打开创建窗口，现阶段提供“定时向指定 Session 发送消息”和“周期性发送消息”两个 Job 选项。
- 调度语义：一次性定时消息支持设置绝对执行时间，或设置相对当前时间的等待时长；周期性消息支持设置固定间隔，或设置每周指定星期的指定时间。
- Agent/MCP 能力：为一次性和周期性 Job 提供 Pan agent 模板/MCP 快捷创建能力，便于 agent 创建、查询、调整和终止时间类任务。
- 消息输入：Job 不限于预设的发消息/群发类型，允许直接输入要发送给 Session 的命令文本；同时面向用户和 agent 提供一键创建“发消息”和“群发”的快捷入口。agent 创建或触发发送时继续复用 `send`，保留 agent ID 和 MA 消息格式。
- UI 时间要求：所有带时间的 Job 都要在 UI 显示当前调度时间，并支持调整；具体 UI 形态等待 DEC-004。
- 后续范围：群发功能先核对历史任务；已有则考虑复用/合并，未有则另立实现任务。群发默认入口为选择 Session 后执行，Job 标签页为第二入口；群发完成后，再在 Job 创建窗口增加“定时群发”能力，并沿用上述一次性/周期性调度语义。
- 当前结果：后端 Job Registry、持久化恢复、一次性/周期性单 Session 消息、Job MCP、即时群发 API/MCP 已实现并合入；UI、真实 Provider 到期发送和开发者验收仍未完成。
- 下一动作：开发者验收已合入后端；T-045 承接定时群发后端；DEC-004 解除后再实施 Job UI。
- 阻塞：UI 具体形态等待 DEC-004；后端、Agent/MCP 和调度能力不阻塞。
- 关系：独立新需求；与 Session Worker kill 区分，需保留 Job 与 Session 的归属关系；定时群发依赖群发功能完成。
- [x] 调查现有 Job 字段、列表 API 和 kill 能力
- [x] 设计并实现一次性/周期性调度模型、持久化、恢复和取消语义
- [ ] 实现 App Settings 独立 Job 标签页、Job 列表、Session 归属与描述展示
- [ ] 实现 Create Job 窗口与一次性定时发送消息到指定 Session
- [ ] 支持绝对执行时间与相对等待时间
- [ ] 支持按间隔或按每周星期/时间周期发送消息
- [x] 为一次性/周期性 Job 提供 Pan agent 模板与 MCP 快捷创建、查询、调整和终止能力
- [x] 支持直接输入发送给 Session 的命令文本
- [ ] 提供用户与 agent 共用的 UI 发消息/群发快捷创建入口（后端 `agent_send_many` 已实现；UI 等待 DEC-004）
- [ ] 所有时间类 Job 在 UI 显示并可调整（等待 DEC-004 后实施）

- 工作树/分支：`D:\project\pan-worktrees\jobs-backend-20260918` / `feature/jobs-backend-20260918`，基线 `c5e0a19beac448f76b5415714446d794b68fec6a`；已合入本地 `main`
- TA/任务：`ses_0010355a6b0bf824` / `T-043-backend-jobs-20260918`，已完成报告；定向回归已复跑通过
- [ ] 实现用户 kill Job 的 UI 入口（后端 cancel 已实现，等待 DEC-004）
- [x] 核对并复用或实现即时群发功能
- [x] 群发完成后增加定时群发 Job 类型（由 T-045 承接）

### T-045：定时群发 Job 后端与 Agent/MCP

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`6250c8a63082b840a01cf54b78dc383b9f770eda`）｜开发者验收：待确认
- 目标：在 T-043 已有 Job 调度引擎和即时群发语义上，增加一次性与周期性定时群发；不实现 UI。
- 范围：支持目标 Session 列表去重、绝对时间/相对等待、固定间隔/每周星期与时间、时区、取消/编辑、重启恢复、逐目标结果和错误；沿用 `agent_send` 的 agent 身份与 MA 消息格式。
- Agent/MCP：提供创建、查询、调整、取消定时群发的 MCP 入口；与单 Session Job 保持一致的权限、sourceSessionId 和持久化语义。
- 约束：复用现有 Job Registry、调度器和 broadcast/send 实现，不直接写 CLI stdin；不触碰 8768；UI 仍受 DEC-004 局部阻塞。
- 下一动作：开发者验收已合入的定时群发后端；UI 不在本任务范围，继续等待 DEC-004。
- 阻塞：无；后端已完成，开发者验收尚未完成。
- 工作树/分支：`D:\project\pan-worktrees\scheduled-broadcast-backend-20260918` / `feature/scheduled-broadcast-backend-20260918`，基线 `c97b95768c2d273cd7ef3db0af6ed4525ab7e681`
- TA/任务：`ses_0010355a6b0bf824` / `T-045-scheduled-broadcast-backend-20260918`，已完成报告；提交 `6250c8a63082b840a01cf54b78dc383b9f770eda`
- [x] 设计并实现 scheduled broadcast Job 模型与 API/MCP
- [x] 覆盖一次性/周期性、时区、去重、取消/编辑、重启恢复和逐目标结果
- [x] 分离 creatorSessionId 与 targetSessionId(s)，实现系统通知结构化 jobId（`////by pan system` 的通用修复已由 T-046完成）
- [x] 定向回归、compileall、Pan MCP 工具一致性检查
- [x] 合入 main
- [ ] 开发者验收（合入后）

### T-046：Pan 系统后台 Job 通知身份与结构化 Job ID

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`926022d`）｜开发者验收：待确认
- 目标：修复独立后台 Job 终态通知的身份投影；系统通知使用 `////by pan system`，不再出现 `@@@@by agent : unknown | unknown`。
- 范围：后台 Job 持久化创建者 `creatorSessionId`（如有），与 `targetSessionId` 分离；终态通知只投递给目标，不自动复制回创建者；通知结构单独暴露 `jobId`、状态、目标和创建者元数据。
- 兼容：Agent 主动创建的定时消息仍使用 `////by agent` 发送语义；没有创建者的系统 Job 使用 Pan system 前缀；不新增 MCP Server。
- 下一动作：开发者验收真实后台 Runner 完成、重启恢复、`queue_pending` 消费和前端终态展示；解除：开发者完成验收。
- 阻塞：无；与 T-045 并行，UI 决策不影响本任务。
- 工作树/分支：`D:\project\pan-worktrees\pan-system-notice-20260918` / `feature/pan-system-notice-20260918`，基线 `c97b95768c2d273cd7ef3db0af6ed4525ab7e681`
- TA/任务：`ses_c28ff8194c88f3dc` / `T-046-pan-system-notice-20260918`，已完成报告
- 提交/测试：实现提交 `29dbce2cf54de4c9a8e622d4b0e7fde1533e979d`；合并提交 `926022d`；TA 定向 145 passed，合并后相关定向与排除已知基线问题的 tests 全集通过，compileall、skill 工具一致性和 diff check 通过
- 未验证：真实 Pan HTTP/WebSocket、真实 provider/CLI、Chromium、真实 Scheduler/Runner 跨重启；未触碰 8768、未 push
- [x] 后台 Job 保存 creatorSessionId 与 targetSessionId
- [x] 系统通知使用 `////by pan system` 并携带结构化 jobId
- [x] 覆盖无创建者、有创建者、目标与创建者不同、重启重试和去重
- [x] 更新 Pan skill/通知协议文档
- [x] 定向测试、compileall、合入 main
- [ ] 开发者验收（合入后）

### T-047：持久队列投递/清除状态恢复

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`fae290b`）｜开发者验收：待确认
- 目标：修复消息已经领取或尝试发送后，`queue_pending`、delivery ledger、history receipt 与 Worker 恢复状态不一致，导致前端持续显示但没有自动再次消费的问题。
- 证据：`ses_0010355a6b0bf824` 的 q_a4850617e2f943fbbff4a3c7d983dc80 仍在 `queue_pending`；ledger 出现过 worker reservation，但无 `sent_to_cli` 或 history receipt；T-045 完成后出现同 taskId 的 durable duplicate terminal 日志，Worker 随后消失。
- 范围：覆盖 inherited taskId 普通消息不被正式任务幂等误伤；明确 provider hand-off 未确认时可恢复重试，已确认但清除失败时通过 durable receipt 对账避免重复；无活 Worker 且有可投递队列时必能 recovery。
- 约束：不改变正式 assign taskId 幂等语义；不把普通 send 的继承 taskId当作幂等键；不触碰 8768；不通过真实服务强制重发现场消息。
- 下一动作：开发者验收真实 Worker/Provider hand-off、恢复与现场同类队列状态；本次不对原现场消息执行强制重发；解除：开发者完成验收。
- 阻塞：无；独立于 UI 和开发者验收。
- 工作树/分支：`D:\project\pan-worktrees\queue-delivery-recovery-20260918` / `feature/queue-delivery-recovery-20260918`，基线 `6250c8a63082b840a01cf54b78dc383b9f770eda`
- TA/任务：`ses_2c93476e30d3a676` / `T-047-queue-delivery-recovery-20260918`，已完成报告
- 提交/测试：实现提交 `116cc2e156f069b094d3f9b3487c2da433a99285`；合并提交 `fae290b`；相关队列交付/恢复、taskId 继承、终态和执行状态回归全部通过，compileall、diff check 通过
- 未验证：未对真实 `ses_0010355a6b0bf824` 重发/清除；未启动隔离服务或进行真实 HTTP/WS/provider E2E；未触碰 8768、未 push
- [x] 复现并定位 reservation/handoff/terminal dedup 竞态
- [x] 修复队列清除、receipt 对账和无 Worker recovery
- [x] 增加 inherited taskId、重启和重复终态回归
- [x] 定向测试、compileall、合入 main
- [ ] 开发者验收（合入后）

### T-044：工作区后端与 Session 分组

- 任务类型：代码改动｜阶段/状态：整合 / 待开发者验收｜合入 main：是（`c97b95768c2d273cd7ef3db0af6ed4525ab7e681`）｜开发者验收：待确认
- 目标：允许将 Session 分组到不同工作区；工作区在产品概念上相当于一个标签页，每个工作区展示其包含的 Session。
- 第一阶段范围：先实现后端工作区模型、Session 与工作区的归属关系、持久化、查询和基础管理能力；UI 标签页、布局与交互暂不实施。
- UI 决策：等待用户决定工作区标签页的 UI 形式和交互范围后再立项 UI 实现阶段。
- 下一动作：开发者验收已合入后端；DEC-005 解除后再立项 UI 标签页、布局和交互。
- 阻塞：UI 实现等待用户决定；后端模型、接口和持久化不阻塞。
- 关系：独立新需求；后端能力应为未来 UI 标签页提供稳定接口。
- [x] 调查现有 Session 持久化、分组和 API 边界
- [x] 设计工作区模型与 Session 归属语义
- [x] 实现后端持久化、查询和基础管理接口
- [x] 后端回归与隔离验证
- [x] 合入 main
- [ ] 开发者验收（合入后）

- 工作树/分支：`D:\project\pan-worktrees\workspaces-backend-20260918` / `feature/workspaces-backend-20260918`，基线 `c5e0a19beac448f76b5415714446d794b68fec6a`；已合入本地 `main`
- TA/任务：`ses_2c93476e30d3a676` / `T-044-backend-workspaces-20260918`，已完成报告；定向回归已复跑通过

### T-026：附件 AI 绝对路径与 UI 下载 API 渲染分层

- 任务类型：代码改动｜阶段/状态：实现 / 暂停｜合入 main：未开始｜开发者验收：合入后
- 下一动作：用户明确恢复 T-026 后，先重核 T-027 已交付范围；解除：用户明确恢复
- 解除条件：用户明确恢复或开启该任务
- 阻塞：用户暂停
- 关系：要求已并入 T-027/T-027.1/T-027.2；恢复时只处理剩余缺口
- 详情：[T-026 详情](tasks/paused-followups.md)

### T-029：Session queue 查询与修改 MCP

- 任务类型：代码改动｜阶段/状态：方案 / 暂停｜合入 main：未开始｜开发者验收：合入后
- 下一动作：用户明确恢复 T-029 后进行方案调查；解除：用户明确恢复
- 解除条件：用户明确恢复或开启该任务
- 阻塞：用户暂停
- 关系：恢复时与已决定的 taskId 继承规则、T-027 已交付范围一起核对
- 详情：[T-029 详情](tasks/paused-followups.md)

### T-036：终态广播被 enrich/落盘推迟

- 任务类型：无代码任务｜阶段/状态：调查 / 已完成，结论已实现｜合入 main：不适用｜开发者验收：T-041 已合入后待确认
- 下一动作：随 T-041 一并进行开发者验收；T-036 保留调查事实
- 解除条件：开发者完成 T-041 及本调查结论验收
- 阻塞：无；T-041 是独立实现任务
- 关系：T-030 的独立后续；T-041 承接本调查结论
- 详情：[T-036 详情](tasks/paused-followups.md)

### T-039：Worker 主动消费 queue 信息的 MCP 工具方案讨论

- 任务类型：无代码任务｜阶段/状态：方案 / 暂停｜合入 main：不适用｜开发者验收：方案完成后
- 下一动作：用户明确开启方案讨论；解除：用户开启讨论并确认范围
- 解除条件：用户明确恢复或开启该任务
- 阻塞：用户暂停
- 关系：若涉及任务上下文归属，与已决定的 DEC-003 最小继承规则对齐
- 详情：[T-039 详情](tasks/paused-followups.md)

新需求必须分配新的 `T-nnn`，不得复用已完成任务 ID。
