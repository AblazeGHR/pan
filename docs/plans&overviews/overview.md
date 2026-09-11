# Pan 项目总览

> 维护者：Pan SMA
> 更新日期：2026-09-11
> 说明：本文是当前任务、功能状态、工作树和主分支合并状态的总览。提交、测试和服务状态以实际 Git/TA 报告为准；未明确标记“已合入”的内容不得视为已进入主分支。

## 一、当前基线

| 对象 | 路径 | 分支 | HEAD | 状态 |
|---|---|---|---|---|
| main | `D:\project\Pan-main` | `main` | `8731518ab9deedf33fb8e006a7e348faf002c43a` | clean；当前主分支 |
| practical | `D:\project\Pan` | `practical` | `1f05127f9d4a65ae315e532c449e748313e003dc` | clean；不得覆盖用户脚本 |

当前约束：

- `8768` 是受保护的 Pan 服务，禁止重启、停止、修改或用于本次实验。
- 需要服务验证时只允许使用明确隔离的 `8765`/`8767` 实例，并记录 PID、checkout、数据根和清理结果。
- 禁止调用 `session_handoff` 或任何替身交接接口。
- 新 TA 工作树统一放在 `D:\project\pan-worktrees`，且必须先用 `git worktree add` 实际注册，再交给 TA。
- 未经明确授权不 push；当前本批内容均未 push。
- `D:\project\Pan\scripts\setup.bat` 与 `scripts\start_pan.bat` 的用户修改必须保留，不得覆盖。

## 二、已合入 main 的功能

| 功能 | 合入提交 | 当前状态 |
|---|---|---|
| Session prompt 拆分：`original_prompt` / `handoff_prompt`，避免交接简报递归叠加 | `bf73b5b`（由 practical 的 `77a2e66` 合入） | 已合入 main；未执行真实 handoff |
| Session Details 基础 usage/rename 相关主分支承接 | `3c2a455`、`5d5491f` 等主分支历史 | 已在 main；与旧 TA feature 分支存在拓扑差异 |
| practical 的 MCP 依赖检查脚本修复 | `9138a79` | 已合入 main；不得覆盖用户脚本 |
| ChatMessages 底部跟随区域、Scroll to bottom 行为 | `51a159c`（基于 practical `1f05127`） | 已合入 main；保留 48px 底部跟随判定 |
| Session Details / Rename / Usage / System prompt UI 整合 | `f497d351` | 已合入 main；未做真实浏览器/mobile E2E |
| GitHub Windows 窄编码日志兼容 | `c734d6a` | 已包含在 main 当前历史；未 push 新分支 |
| CI Python 3.12/3.14 调整 | `31e1ee3` | main 当前 HEAD；已合入并同步 origin/main |

## 三、已完成但尚未合入 main

### 1. Codex 全局额度缓存

- 状态：实现完成，前端验证完成，待集成审查。
- 工作树：`D:\project\pan-worktrees\codex-quota-cache-luna-20260909`
- 分支：`feature/codex-quota-cache-luna-20260909`
- 最终提交：`466782c209edfe29205c3e5e8388ce046cd5e899`
- 父提交：`dd9ef954ea11be515ce6e5e91394c45e40ac081d`
- 内容：全局 `data/codex/quota/<profile-key>.json` 缓存、app-server push 持久化、离线 API、profile 隔离、窗口时长归一化、可选 WHAM 刷新、Session Details API projection。
- 验证：Python quota 回归 `97 passed`；全量 Vitest `46 files / 404 tests passed`；lint 0 errors；build 通过；compileall 和 diff check 通过。
- 未完成项：未做真实服务/浏览器 E2E；未合入 main；未 push。

### 2. Session Details / Rename / Usage / System prompt UI

- 状态：已完成 main 基线整合并合入 main；旧 feature 分支仅作为历史来源保留。
- 旧工作树：`D:\project\pan-worktrees\session-detail-usage-rename-20260909`
- 旧分支最终提交：`e716f617fc670c620c01642f38869ff5735df389`
- 包含：Session name 可复制、rename 预填并全选、Usage 默认折叠、Codex 只显示周/月额度、System prompt 默认折叠可展开、滚动按钮行为。
- 旧分支验收结果：全量 Vitest `407 passed / 3 failed`；lint 通过；build 失败。原因是旧分支相对当前 main 基线过旧，且存在异步 quota 测试与 TypeScript 类型问题。
- 整合提交：`f497d35196e7bbddde3aee564619871acc277edc`
- 整合方式：以 main 为基线手工移植 main 缺失的 System prompt/quota 过滤和测试，保留 main 已有的 ChatMessages 48px bottom-follow；未机械覆盖旧实现。
- 验证：全量 Vitest `46 files / 407 tests passed`；lint 0 errors；build 通过；Python 相关测试 `39 passed, 1 skipped`。
- 未完成项：未做真实服务/浏览器/mobile E2E；Python MCP 用例仍缺少 `mcp` 环境依赖。
- 已合入 main；未 push。

### 3. MCP `model_list` adapter 发现改进

- 状态：TA 报告完成，尚未完成主仓库集成验收。
- 报告提交：`e66613e6fa4db7578381a5475a8b66193c2bf863`
- 报告目录：`D:\project\pan-worktrees\mcp-model-list-adapter-discovery-20260909`
- 内容：取消默认 CBC；无 adapter 时返回结构化 `adapter_required`、可用 adapter 和 Codex 调用提示；指定 `codex` 时返回 Codex 模型；未知 adapter 返回可行动错误。
- 报告测试：`55 passed, 1 skipped`；compileall/diff check 通过。
- 集成注意：该目录虽可作为独立 Git 仓库读取提交，但不在 `Pan-main` 的已注册 worktree 列表中；合入前必须核对基线和补丁，不能直接假定已处于主仓库拓扑。
- 未合入 main；未 push；未做 live MCP E2E。

## 四、已完成调查 / 待实现任务

### Context window 与压缩阈值

- 状态：调查完成；未修改代码，待后续实现决策。
- 工作树：`D:\project\pan-worktrees\context-window-threshold-experiment-luna-20260911`
- 分支：`audit/context-window-threshold-experiment-luna-20260911`
- 基线：`31e1ee310cbf1082b01a7f0282fc817f4a1f5a79`
- TA：Codex `gpt-5.6-luna` high；本次没有启动实际测试模型，因此没有消耗模型 token。
- 目标：确认 `model_context_window` 与 `model_auto_compact_token_limit` 是否能在 Session 创建后修改，并在下一次 spawn/respawn 生效；区分运行中 App Server 热更新、Codex thread resume 和新进程读取配置。
- 硬性限制：不使用 practical 服务、不触碰 8768；不触发真实 compact，不进行长对话；仅完成官方文档、CLI help、静态参数和进程内构造实验。
- 结论：两个值都属于 Codex 启动配置，应作为可持久化的 Codex Session 设置；修改后通过 Worker respawn 重新启动 App Server，并保留原 `cli_session_id` 做 `thread/resume`。当前证据不支持运行中 App Server 热更新，也没有证据要求新建 Pan Session/Codex thread。
- 当前 Pan 尚未接入这两个字段；不能把当前代码行为误称为已支持。
- 官方参考：[configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)、[config basics](https://learn.chatgpt.com/docs/config-file/config-basic)、[App Server](https://learn.chatgpt.com/docs/app-server)。
- 已确认：`model_context_window`、`model_auto_compact_token_limit` 均为 `number` 配置键；后者未设置时使用模型默认值；`-c/--config key=value` 可传入。
- 未验证：真实 App Server 启动后是否实际采用新值；同一 thread resume 后上下文窗口/compact 行为是否完全按新值运行；`config/value/write` 是否影响已运行进程；有效范围和边界值。
- 产品建议：放入现有 Session Settings，不新增 new-session 专用输入；空值删除 override，不生成空字符串/0/default 的 `-c` 参数；修改后提示 Worker restart/respawn 后生效。

## 五、已完成的调查结论

### `model_context_window` 初步结论

- 正确调查使用了 Codex `gpt-5.6-luna` high。
- 官方配置入口为 `config.toml` 或通用 `-c model_context_window=<number>`。
- 当前 Pan 尚未支持该 Session 设置；直接传入会被忽略。
- 预期产品方向是创建后 Session 设置 + Worker respawn，而不是 new-session 输入框；最终结论等待当前隔离实验确认。

### 无 memory 时的 minimal requirements

- `minimal-requirements.txt` 缺少直接运行依赖 `httpx`。
- `pytest` 属于 dev/test，不是 Core/MCP runtime 硬依赖。
- Memory ML 依赖应保持 optional；关闭 memory 时不应进入 Core minimal。
- 该次为只读审计，尚未产生代码提交。

## 六、待处理清单

1. 根据 context window/compaction 调查结论，决定是否实现 Session Settings 支持；如实现，先做低成本 argv/respawn 验证，不触发真实 compact。
2. 对 MCP `model_list` 提交建立正确的主仓库 integration worktree，核对补丁后再合入。
3. 对 Codex quota 提交进行 integration review；必要时补真实隔离 E2E，但不得使用 practical/8768。
4. 清理已完成且不再需要的历史 feature/integration worktree，先确认没有未提交用户文件。
5. 合入前统一复核 `main`、`practical`、所有 feature worktree 的 clean 状态和未提交用户文件。

## 七、验收口径

- “TA 报告 done”不等于“已合入 main”。
- “单元测试通过”不等于“真实服务 E2E 通过”。
- 只有在主仓库正确拓扑中完成补丁审查、相关测试通过、工作树 clean，并明确执行 merge 后，才标记为“已合入 main”。
- worktree 路径、分支、HEAD、测试命令和未验证项必须在本文件同步更新。
