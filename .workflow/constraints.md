# Pan 工作流约束策略

> 当前长期规则、有效授权和少量例外。任务状态与证据见 [overview.md](overview.md)，复杂事实见 [.workflow/tasks](tasks/acceptance-batches.md)。本文件不保存运行日志，也不维护逐任务状态表。

## 绝对边界

- `D:\project\Pan-main` 是当前开发项目根；`D:\project\Pan` 是独立 practical 工作树。不得把一个工作树的 dirty/untracked 内容覆盖到另一个。
- `8768` 及其相关进程是绝对红线：不得重启、停止、kill、修改、复用、实验、创建测试会话/worker 或写入式探测；只读观察允许。需要重启等动作时由用户执行，本规则无例外。
- 真实服务、API、WS、浏览器和 OS 验证使用自有隔离实例与独立 worktree；先核对端口和进程归属，不能停止他人进程，端口冲突时改用隔离端口并记录。
- 新 TA worktree 放在 `D:\project\pan-worktrees`，先注册 Git worktree 再派发；不得覆盖用户 dirty/untracked 文件，不自动 stash、reset、破坏性清理、关系迁移或 Session handoff。

## 默认策略

### Git 与整合

- 默认采用 `GIT-1`：TA 在隔离 worktree 实现并验证；满足范围、提交、测试、依赖、决策和 clean 条件后，MA 可自动无冲突合入本地 `main`。
- 自动合入不包含 push、发布、生产服务、8768 或 `D:\project\Pan` 操作，也不替代 MA 验收或开发者验收；冲突、范围外 dirty、未决行为或完成条件不足时停在合并边界。
- 每次派发/整合前核对项目根、worktree、分支、HEAD、dirty 状态和提交文件清单；只处理授权范围。

### TA、MA 与事件更新

- 新 TA/返工默认优先使用 CBC `deepseek-v4.1-flash`、`effort=high` 或更高、`permission_mode=bypassPermissions`；若 CBC 或该模型不支持所选高 effort，则回退为 `effort=auto`。这是默认优先级而非强制规则；任务明确指定其他 adapter/model/effort/能力，或存在兼容性理由时，以任务级指定为准。模型不可用时应记录实际模型并报告，不得静默切换；必要时按 `deepseek-v4.1-flash` → `glm-5.3-flash` 级联。Codex `gpt-5.6-luna` 及其 `effort`/thinking 规则仅在任务明确指定 Codex 时使用；已运行任务不回溯切换。
- 目标、数据语义、兼容性、范围、成本和授权不变时，MA 可自主调查、实现、测试、普通返工、派发和按 GIT-1 整合；需要改变这些内容或进行 push、发布、受保护服务操作时，先建立决策/授权并停止受影响动作。
- 普通任务默认只更新两次：派发成功一次，验收结论一次。仅决策、授权、阻塞、责任人或范围变化允许额外更新；TA 中间过程不整段复制到 overview。
- 独立验收任务使用稳定 `taskId` 派发；报告通过订阅/队列核对。`queue_pending` 是待处理事实，`sent_to_cli` 不是业务完成；TA done、提交、合入、自动化测试和 MA 复核彼此不替代。

### 测试与验收

- 分开记录源码/静态检查、单元或 jsdom、Python 回归、build、真实服务/API/WS、browser/mobile/OS E2E 和开发者验收；未运行必须写未验证，不把合成 DataTransfer 当 OS 文件拖放证据。
- UI 任务覆盖缺失值、错误请求、竞态/切换、离线或 Worker 缺失等风险；多个任务共享协议、Session/Worker、队列、持久化、WS、广播、权限、流式渲染或跨 Session 语义时，按风险补定向批次回归，不机械重跑全套。
- 开发者验收必须提供环境、用户动作、可观察预期、结果、证据入口和关闭范围；验收通过不等于所有未验证边界已验证。
- 交付完成、真人验收、完整最终 hash 可确定且无直接未完成事项或活跃 TA 后，按归档规则把短摘要写入 `developLog.md` 并从首页清理；独立后续不阻塞归档。

## 有效授权

- `AUTH-001`：完成条件满足且无冲突时自动合入本地 `main`；不含 push、发布、8768、生产服务或 `D:\project\Pan`。
- `AUTH-002`：批次完成后由 MA 按共享风险决定定向或链路回归；至少保留每个任务的定向证据，记录通过、失败和未验证范围。
## 任务例外与已决定语义

- `DEC-001/002`：普通文件 paste/drop 与结构化附件采用既定 A+C；目录第一阶段拒绝，待发送附件按 Session 隔离，合法正文 editor/server-file 引用可跨 Session 复用，发送失败恢复可编辑副本。
- `DEC-003`：正式任务使用稳定 `taskId`；普通消息继承目标 TA 当前任务上下文，无任务时允许 `null`，不实施 sourceQueueItemId、deliveryUnitId 或按历史猜报告归属。
- `DEC-006`：Job Registry、一次性/周期性消息、定时群发和 Agent/MCP 创建/取消按既定后端语义推进；该决定不预设 Job UI 外观。
- `DEC-004/005` 只阻塞对应 Job/Workspace UI 形态，不冻结已明确的后端、Agent/MCP 和持久化验收；`T-029/T-039` 在用户恢复授权前保持暂停，`T-062` 已获用户恢复授权，当前按 T-062.2/T-062.3 等阶段实施；仍不得操作 practical、8768 或未授权真实服务。
- `T-FRONTEND-CONSOLIDATED-20260922` 是一次边界明确的选择性前端整合：`cba9df0` 的 TA 交付已获 MA 整合验收，并由 `92b363c` 合入本地 `main`，但不代表已 push 或已通过真人验收；真实 provider、生产 8768 和 Steer receipt 服务端协议仍分开记录。
- 工作树“待清理”清单只表示待后续人工核对的候选，不授权删除 worktree、branch、session 或未跟踪证据；有未跟踪报告、测试或运行证据的调查树必须保留。
- 其他任务无专属策略映射；需要新的例外时只记录范围、责任人、恢复/失效条件和来源，并在覆盖后删除旧口径。
