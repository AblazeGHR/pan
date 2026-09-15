# Pan 工作流约束策略

> 本文件是当前项目工作流的约束真源；任务状态与证据见 [overview.md](overview.md)。本文件由旧的 [constraints-and-acceptance.md](../docs/plans&overviews/constraints-and-acceptance.md) 迁移而来，旧文件保留为历史来源，不再作为当前控制面。
>
> 更新日期：2026-09-15

## 基础边界

- `D:\project\Pan-main` 是当前开发项目根；`D:\project\Pan` 是 practical 工作树。
- **绝对红线：`8768` 及其相关进程无论如何都不得触碰。** 它是用户正在实际使用的实用服务；禁止重启、停止、kill、修改、复用、实验、在其上创建测试会话/worker 或写入式探测（只读观察如 `netstat` 允许）。需要重启等操作时由用户自己执行，MA/TA 不得代劳；本规则无例外，也不需要通过询问确认。
- 真实服务/运行时验证一律使用自有隔离实例与独立 worktree：先探测端口占用，再按任务分配（当前 `8767` 归 T-031、`8766` 归 T-033 接续、`8765` 被他人残留 `vite preview` 占用）。**他人的进程不得代为停止**，遇占用改端口并在报告中记录。
- 新 TA worktree 统一放在 `D:\project\pan-worktrees`，必须先用 `git worktree add` 注册，再交给 TA。
- 不覆盖用户已有 dirty/untracked 文件；特别保护 `D:\project\Pan\scripts\setup.bat`、`scripts\start_pan.bat` 及当前 `main` 上的用户文档和本地缓存。
- 不自动应用 stash，不进行未授权的 reset、破坏性清理、关系迁移或 Session handoff；删除 worktree 前须先核对没有用户未提交文件。

## Git 策略

### GIT-1：测试通过后直接合入本地 main

- 状态：可用；来源：用户会话明确授权，2026-09-12。
- 规则：功能在隔离 worktree 中实现；MA 独立核对改动范围、提交、测试和工作树 clean 后，可直接提交并合入本地 `main`。
- 允许：创建 worktree/分支、提交功能改动、无冲突合入本地 `main`、更新当前工作流文档。
- 不包含：push、发布、生产服务操作、覆盖用户 dirty/untracked 文件。
- 失败处理：测试失败退回原 TA 修复；合并冲突停止在合并边界并记录阻塞，不重置或强行覆盖。

### GIT-2：保护既有工作

- 状态：强制。
- 规则：每次派发和整合前核对项目根、worktree、分支、HEAD、dirty 状态及提交文件清单；只 stage 任务授权范围内的文件。
- 失败处理：发现重叠或来源不明的 dirty 内容时，保留现场并停止相关写入。

## TA 模型策略

### MODEL-1：Luna 高推理端到端 TA（当前首选）

- 状态：用户于 2026-09-15 明确指定为**当前首选 TA 策略**（Codex 五小时额度未耗尽前优先使用）。
- 规则：实现、测试、局部返工优先派给 `codex` adapter 的 `gpt-5.6-luna`，`effort=high`，默认使用 `permission_mode=bypass`；需求明确且调查不会改变产品行为时，一次端到端完成调查、实现、验证和报告。用户明确要求更严格权限时，以任务级要求覆盖默认值。
- 回退：Codex 五小时额度触发限额后按 `MODEL-3` 级联切换到 `cbc` 的 `deepseek-v4.1-flash`；模型或依赖不可用时记录外部阻塞，不静默换用未经授权的模型。

### MODEL-2：低成本验证 TA

- 状态：可用。
- 规则：极短、低风险、仅信息核对的任务可使用 Luna `low`；不得用于替代高风险实现或真实 E2E。

### MODEL-3：CBC DeepSeek / GLM 级联（Codex 额度用尽后）

- 状态：用户于 2026-09-15 明确指定；同日由“默认 TA 模型”调整为“Codex 额度触发限额后的级联档位”。
- 规则：Codex 五小时额度触发限额后，新 TA 与返工任务改用 `cbc` adapter 的 `deepseek-v4.1-flash`，权限默认 `bypassPermissions`，effort 默认 `auto`（按任务风险和 TA 表现可上调为 `high` 或 `xhigh`）；任务若显式指定 `cbc`，直接采用本条。
- 限速回退：DeepSeek 被限速时切换到 `cbc` 的 `glm-5.3-flash`；两者均限速时切回 `deepseek-v4.1-flash`，并在任务报告中记录实际模型和限速证据。不得静默切换到其他 adapter/model。
- 额度规则：每轮工作流对话开始检查 Codex 五小时额度；达到 98% 时进行 SMA 替身交接，目标配置为 `cbc + deepseek-v4.1-flash + high + bypassPermissions`。
- 级联顺序（2026-09-15）：`MODEL-1`（codex `gpt-5.6-luna` high）→ 五小时限额触发 → `cbc deepseek-v4.1-flash` → 限速 → `cbc glm-5.3-flash` → 仍限速 → 回 `deepseek-v4.1-flash`。已完成任务不回溯切换模型，只作用于新派发与返工。
- 用户追加口径（2026-09-15，Codex 五小时额度实测 96% 时明确）：**新任务直接使用 `cbc deepseek-v4.1-flash`**，不必先把残余 Codex 额度耗尽；**已在跑的 codex 任务不重做**，等其停下后用 `session_handoff` 接续到 `cbc deepseek-v4.1-flash`，交接材料为原任务 brief + worktree 现场 + 源 session 历史，不得要求从零重来。
- **最新覆盖口径（用户 2026-09-15）**：当前批次（至少包括 T-033、T-037 及其直接收束/复验）不切换已有 TA；该批次全部完成后，所有新 TA 与返工任务统一使用 Codex `gpt-5.6-luna`，`effort=high`，按任务风险可升为 `xhigh`，权限默认 `bypass`。该条覆盖上一条“新任务直接使用 CBC”的安排，仅对后续批次生效；已在运行任务不回溯切换。若 Codex 达到硬性额度/服务不可用，按本文件额度规则停在外部阻塞或执行 SMA 替身交接，不静默改用其他 adapter。

## MA 自主权策略

### AUTONOMY-1：目标内端到端自主推进

- 状态：可用。
- MA 可自主：在用户目标、数据语义、兼容性和授权范围不变时，选择调查方法、实现路线、测试、普通返工、TA 调度以及按 GIT-1 整合。
- 必须上报：调查证明需要改变产品行为、兼容性、数据语义、范围、成本、破坏性操作、push、发布、受保护服务操作或新的外部授权。
- 上报后：在 overview 建立 `DEC-nnn` 或 `AUTH-nnn`，停止受影响动作，继续无依赖工作。

## 测试与验收策略

### TEST-1：分层证据

- 状态：强制。
- 分别记录源码审查、静态检查、单元/jsdom、Python 回归、build、真实服务/API/browser/mobile E2E 和开发者验收；不能相互替代。
- TA `done`、自动化测试通过、MA 复核通过都不能勾选“开发者验收”。

### TEST-2：UI 缺失与错误路径

- 状态：强制适用于 UI 改动。
- 至少覆盖缺失值、空值、错误请求、竞态/切换、离线或 Worker 缺失等实际风险路径；未运行的 browser/mobile E2E 必须明确写“未验证”。

## Pan 编排策略

- Session 是持久身份和上下文，Worker 是可重建的临时进程；报告优先走 `report_subscribe → queue_pending`，服务或端口不匹配时使用定向 `session_get` 兜底。
- 新任务用 `agent_assign`，普通补充用 `agent_send`，需求方向失效或必须立即停止时才用 `agent_send_force`。
- **报告计数纪律（2026-09-15 采纳自 T-032 第 1 档，零代码）**：① 需要独立验收的任务（含执行中追加的任务）一律用 `agent_assign(task_id=稳定唯一值)`，`agent_send` 只作不要求独立完成证明的补充信息；② 收到某任务的报告后**不得**据此判断"后续队列任务未执行"——一条注入消息可能包含多个 report 段（连续 report/QQ 项会被合并成一个 delivery unit），必须按 report 段 / `taskId` 计数，而不是按消息条数；③ 重试前用**同一个** `task_id` 重发，依据返回的 `pending` / `sent_to_cli` / 缓存终态决定动作，不换新 id 重派；④ `queueItemId` 只在接口实际返回时记录（当前 `agent_send` MCP 既不接受 `task_id` 也不返回 `queueItemId`，不得假装具备）；⑤ 需要判断中间态时用 HTTP `GET /api/sessions/{id}/queue`（仅 `queued` 子集）或 `queue.item_delivered` 事件，并明确"`sent_to_cli` ≠ 业务完成"。
- TA、Session、Worker、worktree 和服务实例分别记录；不把 TA `done` 当作验收或合并。
- 无更多编排动作时回到 idle；收到 TA 报告、用户消息或外部状态变化后重新读取 overview 并重算可执行集合。

## 默认策略与任务映射

- 默认 Git：`GIT-1`；当前批次沿用已派发模型，批次完成后的默认 TA 为 Codex `gpt-5.6-luna` high，按风险升 xhigh（受 MODEL-1 与最新覆盖口径约束）；默认 MA 自主权：`AUTONOMY-1`；UI 默认追加 `TEST-1`、`TEST-2`。

| 任务 | Git | TA 模型 | MA 自主权 | 其他 |
|---|---|---|---|---|
| `T-001`–`T-020` | `GIT-1`、`GIT-2` | `MODEL-1`；轻量核对可用 `MODEL-2` | `AUTONOMY-1` | 按功能追加 `TEST-1`/`TEST-2` |
| `T-021` | `GIT-2`；UI demo 暂不合入 | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；开发者确认后再进入后端与 `GIT-1` |
| `T-022` | `GIT-1`、`GIT-2` | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；后端测试通过后直接合入本地 `main`，不 push |
| `T-023` | `GIT-2`；调查阶段不改正式代码 | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；先报告方案，开发者决策后再执行 |
| `T-024` | `GIT-1`、`GIT-2` | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；测试通过后直接合入本地 `main`，不 push |
| `T-025` | `GIT-1`、`GIT-2` | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；按 `DEC-001=A+C` 实现，测试通过后直接合入本地 `main` |
| `T-027`、`T-028` | `GIT-1`、`GIT-2` | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；按用户最新语义执行，默认 bypass |
| `T-030` 及后续新 TA | `GIT-1`、`GIT-2` | `MODEL-1` 优先；Codex 额度触发限额后用 `MODEL-3` 级联 | `AUTONOMY-1` | `TEST-1`、`TEST-2`；用户 2026-09-15 指定 luna high 优先，再 DeepSeek，再 GLM |
