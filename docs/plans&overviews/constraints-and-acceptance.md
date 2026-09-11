# Pan 项目约束与验收口径

> 维护者：Pan SMA
> 更新日期：2026-09-11
> 用途：记录跨任务长期有效的工作约束、编排记忆和验收定义。具体功能的进展、工作树和合并状态只记录在 [overview.md](overview.md) 对应功能条目中。

## 一、不可违反的边界

- `8768` 是受保护的 Pan 服务：不得重启、停止、修改或用于实验。
- 禁止调用 `session_handoff` 及任何替身交接接口。需要接续任务时使用普通消息/任务编排，并等待明确授权。
- 不处理、不 claim、不修改、不迁移旧 SMA `ses_0ddbf0f3b0987ae8` 的托管关系。
- 不覆盖用户在 `D:\project\Pan\scripts\setup.bat` 和 `scripts\start_pan.bat` 中的未提交修改。
- 未经明确授权不 push；合并、push、服务操作、关系迁移分别需要明确授权。
- 新 TA 工作树统一放在 `D:\project\pan-worktrees`，必须先执行真实 `git worktree add` 注册，再交给 TA；不能把普通目录或 Session workdir 当作 Git worktree。
- 不自动应用本地 stash，不删除 Session，不进行未授权的分支重置或破坏性清理。

## 二、Pan 编排记忆

- Session 是持久身份和任务上下文，Worker 是可重建的临时进程；Worker 被回收不等于 Session 被删除。
- 普通新任务使用 `agent_assign`；多轮补充使用 `agent_send`；只有紧急打断才使用 `agent_send_force`。
- 完成通知优先使用 `report_subscribe` → `queue_pending`；服务版本或端口不匹配时，明确记录并使用定向 `session_get` 轮询兜底。
- 新 TA 默认使用 `codex` adapter；模型按任务选择 `gpt-5.6-luna`，不要误用 CBC 模型列表。实际低成本行为测试使用 `gpt-5.6-luna`、`low` 和极短提示。
- TA 工作树、Session、Worker、服务实例必须在报告中分别列出，不能混称。
- 需要删除工作树时，先确认没有未提交用户文件，再删除 Git worktree 注册和实际文件目录；仅删除 Session 不会删除磁盘工作树。

## 三、服务与实验口径

- 真实服务验证必须使用自己启动的隔离实例，通常使用 `8765` 或 `8767`，并记录 checkout、PID、端口、数据根和清理结果。
- 不得把 jsdom/Vitest、静态分析、import probe 或单元测试写成真实服务 E2E。
- 真实 Codex 行为实验应优先使用命令参数覆盖；如果用户要求不改全局，则不得修改真实 `config.toml` 或 `auth.json`。允许真实 `CODEX_HOME` 污染时也必须记录 SQLite/临时状态变化和未终止进程。
- 不触发高消耗的自动 compact 实验，除非用户明确授权；“参数被接受”与“压缩实际触发”必须分开报告。

## 四、验收口径

- TA 报告 `done` 不等于功能已经验收，更不等于已经合入 main。
- 每项功能验收至少核对：工作树路径、分支、HEAD、工作树 clean、改动文件、测试命令及结果、未验证项。
- 前端功能优先验证相关 Vitest、全量 Vitest、lint、build；缺少 `node_modules` 时只能报告阻碍，不能宣称通过。
- Python 功能按改动范围运行相关回归、compileall 和 `git diff --check`；依赖缺失必须单独记录。
- 真实服务/API/浏览器/mobile E2E 必须分别标注，不能用单元测试替代。
- 只有在正确主仓库拓扑中完成补丁审查、测试通过、工作树 clean，并实际执行合并后，才可标记“已合入 main”。
- UI 变更需验证缺失值、空值、错误路径、边界滚动/输入和提交前二次校验；不能只验证正常路径。
- 运行期设置变更若属于进程相关设置，必须复用现有生命周期：idle 自动 respawn，running 设置 `pending_restart`，任务结束回 idle 后自动 respawn，无 Worker 时由下一次 spawn 生效。

## 五、文档维护规则

- `overview.md` 只记录项目阶段、功能计划、实现内容、工作树、测试状态、合并状态和关键功能结论。
- 约束、验收口径、编排操作记忆统一记录在本文，不在 `overview.md` 重复展开。
- 一个功能的调查结论、实现计划、代码状态、测试状态和合并状态必须集中在 `overview.md` 的同一功能条目内，不拆到多个章节。
- 每次功能状态发生变化，同时更新对应功能条目；不要只追加时间线而留下互相矛盾的旧结论。
