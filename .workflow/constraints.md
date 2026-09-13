# Pan 工作流约束策略

> 本文件是当前项目工作流的约束真源；任务状态与证据见 [overview.md](overview.md)。本文件由旧的 [constraints-and-acceptance.md](../docs/plans&overviews/constraints-and-acceptance.md) 迁移而来，旧文件保留为历史来源，不再作为当前控制面。
>
> 更新日期：2026-09-12

## 基础边界

- `D:\project\Pan-main` 是当前开发项目根；`D:\project\Pan` 是 practical 工作树。
- `8768` 是受保护的 Pan 服务，未经用户明确授权不得重启、停止、修改或用于实验；真实服务验证使用自有隔离实例，通常为 `8765` 或 `8767`。
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

### MODEL-1：Luna 高推理端到端 TA

- 状态：默认可用。
- 规则：实现、测试、局部返工优先派给 `codex` adapter 的 `gpt-5.6-luna`，`effort=high`；需求明确且调查不会改变产品行为时，一次端到端完成调查、实现、验证和报告。
- 回退：模型或依赖不可用时记录外部阻塞，不静默换用未经授权的模型。

### MODEL-2：低成本验证 TA

- 状态：可用。
- 规则：极短、低风险、仅信息核对的任务可使用 Luna `low`；不得用于替代高风险实现或真实 E2E。

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
- TA、Session、Worker、worktree 和服务实例分别记录；不把 TA `done` 当作验收或合并。
- 无更多编排动作时回到 idle；收到 TA 报告、用户消息或外部状态变化后重新读取 overview 并重算可执行集合。

## 默认策略与任务映射

- 默认 Git：`GIT-1`；默认 TA：`MODEL-1`；默认 MA 自主权：`AUTONOMY-1`；UI 默认追加 `TEST-1`、`TEST-2`。

| 任务 | Git | TA 模型 | MA 自主权 | 其他 |
|---|---|---|---|---|
| `T-001`–`T-020` | `GIT-1`、`GIT-2` | `MODEL-1`；轻量核对可用 `MODEL-2` | `AUTONOMY-1` | 按功能追加 `TEST-1`/`TEST-2` |
| `T-021` | `GIT-2`；UI demo 暂不合入 | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；开发者确认后再进入后端与 `GIT-1` |
| `T-022` | `GIT-1`、`GIT-2` | `MODEL-1` | `AUTONOMY-1` | `TEST-1`、`TEST-2`；后端测试通过后直接合入本地 `main`，不 push |
