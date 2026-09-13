# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)。
>
> 维护者：Pan SMA；迁移日期：2026-09-12

## 当前项目事实

- 项目根：`D:\project\Pan-main`（`git rev-parse --show-toplevel` 已核对）。
- `main`：`f76bcd1`；最近业务整合为 `c1a5ace`，随后提交工作流状态记录；未 push。
- `practical`：`D:\project\Pan`，`1be6a5c`；本次工作流文档迁移未改动。
- `main` 既有 dirty/untracked：`docs/plans&overviews/overview.md`、`docs/references/cli-adapter-special-behaviors.md`、`.vite/`、`docs/developLog.md`；均属既有用户内容或本地文件，未纳入本次提交。
- 工作流迁移提交：`3311ee5`；规范文件已进入当前 `main`，旧文档仍保留在原位置。

## 工作流控制

- 整体状态：T-021 UI demo 正在进行最终发布前自检与必要修复，等待 TA 报告
- 当前焦点：T-021；先完成完整测试和边界审计，后端仍等待开发者确认
- 可执行：等待 TA 完成最终自检、修复和全量验证
- TA 执行中：`ses_f1bebe98cb738ec3`（`worker-2`，Codex `gpt-5.6-luna`，high）
- 决策阻塞：无
- 授权阻塞：无
- 外部阻塞：无
- 唤醒条件：TA 报告、用户消息、overview/约束变更或再次启动工作流

## 待决策与待授权

### AUTH-001：测试通过后直接合入本地 main

- 状态：已授权
- 作用域：Pan 隔离 worktree 中、测试通过且无冲突的功能修复和文档整合
- 允许：创建 worktree/分支、提交功能改动、合入本地 `main`、更新工作流文档
- 不包含：push、发布、生产/受保护服务操作、覆盖用户 dirty/untracked 文件
- 有效期：后续同类任务，直至用户撤销或修改
- 来源：用户会话，2026-09-12；已用于 `c1a5ace` 及后续本地整合

## 一、已合入 main 的改动

### T-001：Session prompt 拆分与非递归交接准备（旧编号 1）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，待开发者验收
- 目标/结论：将 `system_prompt` 拆为 `original_prompt` 与 `handoff_prompt`，计算兼容的 `systemPrompt`；持久化、旧 JSON、worker/HTTP/MCP/导入/分支路径均已接入，不猜测历史混合文本。
- 工作树/提交：历史 feature 与 practical 承接提交 `77a2e66`；main `bf73b5b`。
- 测试/未验证：真实 handoff 按安全边界未执行。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-002：Session Details / Rename / Usage / System prompt UI（旧编号 2）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 目标/实现：Session name 复制、rename 预填全选、Usage 默认折叠、System prompt 折叠并保留换行、Codex quota 窗口过滤、ChatMessages 底部跟随行为。
- 工作树/提交：`D:\project\pan-worktrees\session-detail-usage-rename-20260909`；最终 `e716f617`；main 整合 `f497d35196e7bbddde3aee564619871acc277edc`。
- 测试/未验证：46 files / 407 tests、lint、build 通过；未做真实 browser/mobile E2E。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-003：ChatMessages 底部跟随与 Scroll to bottom（旧编号 3）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，开发者已验收
- 结论：距底部 `<=48px` 时跟随并隐藏按钮，超过时显示按钮且不吸附历史浏览；保留分页、会话切换和几何快照处理。
- 工作树/提交：来源 practical `1f05127`；main `51a159c`。
- 测试/未验证：前端相关及全量测试曾通过；真实 browser/mobile 滚动物理行为未做 E2E。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-004：Windows 窄编码日志兼容与 CI（旧编号 4）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，开发者已验收
- 结论：stdout 编码失败使用 `backslashreplace`；回归测试和 CI Python 版本/配置已同步。
- 提交：`c734d6a`，后续 `31e1ee3`。
- 测试/未验证：本地复现修复；Python workflow、前端 Vitest、lint、build 曾通过。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-005：practical 启动脚本与 MCP 依赖检查（旧编号 5）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，待开发者验收
- 结论：启动脚本补齐 Pan Core 与 stdio MCP 依赖检查，保留 practical 用户脚本修改。
- 提交：`9138a79`。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-006：Codex 全局额度缓存（旧编号 7）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：额度属于 Codex provider profile/账号，不属于 Session；已实现 profile 缓存、app-server push 持久化、离线 API、窗口归一化、可选 WHAM 刷新和 Session Details projection。
- 工作树/提交：`D:\project\pan-worktrees\codex-quota-cache-luna-20260909`；`dd9ef954`、`466782c`；main `97930f7`、`6159644`、`b1770f9`。
- 测试/未验证：Python quota 17 passed、Session Details jsdom 13 passed；未做真实服务/browser E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-007：MCP `model_list` adapter 发现改进（旧编号 8）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，开发者已验收
- 结论：空 adapter 返回 `adapter_required` 与可用 adapter；指定 adapter 后查询其模型，不静默选择 CBC。
- 工作树/提交：来源 `e66613e6`；main `0e431e5`。
- 测试/未验证：`py_compile`、`git diff --check` 通过；历史环境曾阻塞定向 pytest，未做 live MCP E2E。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-008：带行号 Markdown 文件链接在 Editor 中打开（旧编号 9）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：支持 `#L42`、`#L42-L48`、`:42`、`:42-48`、URL 编码、Windows/UNC 和 `file://`，经 `pendingLocation` 与 Monaco 定位。
- 工作树/提交：`D:\project\pan-worktrees\markdown-file-link-line-20260911`；来源 `4d3dae1`；main `cea2611`。
- 测试/未验证：定向 jsdom 8 passed，`git diff --check` 通过；未做真实服务/browser/mobile E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-009：Vanilla 前端彻底移除与归档（旧编号 10）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，开发者已验收
- 结论：React 为唯一前端；旧 Vanilla 源码、入口和路由移除并归档；dist 缺失时返回可操作 503。
- 提交：来源 `5ffd395c`，dist 修复 `2947d0c`，随本地整合进入 main。
- 测试/未验证：`test_web_frontend.py` 4 passed、`test_config_reload.py` 19 passed；全量 pytest 曾有 quota docstring 基线失败；未做真实服务/browser E2E。
- 有序待办：
  - [x] 合入 main
  - [x] 开发者验收

### T-010：Codex Session 上下文窗口与压缩阈值设置（旧编号 11）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，待开发者验收
- 结论：SettingsPopover、Web/MCP/import/spawn 和 idle/pending restart 已接入；未设置时不传并恢复默认。
- 提交：来源 `a9843c1`，随 React-only/附件整合进入 main。
- 测试/未验证：adapter、Session、worker、SettingsPopover 定向测试与 build 通过；全量 lint 历史上受旧 Hook 规则失败阻断；未做 provider E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-011：附件 Markdown 链接与附件下载改造（旧编号 12）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：统一安全 Markdown 附件链接与受限下载 URL，兼容历史 `@"path"`，避免误判 Editor 文件链接。
- 提交：来源 `240d858`；冲突整合 `cd09841`；门禁修复 `4444371`。
- 测试/未验证：相关 Vitest 41 passed、build 和 ESLint 通过；未做真实服务/browser/mobile E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-012：附件选择与 New Session 目录输入统一改造（旧编号 13）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：复用 `DirectoryInput`，服务端发送前重新枚举目录并阻止 stale path；New Session 可确认创建不存在目录。
- 提交：来源 `9c0852b`；整合 `cd09841`；门禁修复 `4444371`。
- 测试/未验证：目录 pytest 17 passed、InputRow 28 passed、目录工具 12 passed、build/ESLint 通过；未做真实服务/browser/mobile E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-013：浏览器后台恢复前端状态（旧编号 14）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：共享 WS 安全 reconnect，集中监听 visibility/pageshow/focus 并 debounce；刷新权威 sessions/workers/history/queue，保留草稿、滚动和本地状态。
- 工作树/提交：`D:\project\pan-worktrees\web-resume-on-focus-20260911`；来源 `a25a123`、`77dd322`；main `5d1f214`。
- 测试/未验证：jsdom 2 files / 52 tests、build、ESLint 通过；未做真实 browser visibility/bfcache、服务/WS/provider E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-014：Memory 关闭时的 minimal requirements 分层（旧编号 15）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，待开发者验收
- 结论：拆分 minimal/dev/memory requirements；Memory ML 保持 optional；setup/start 探测 Core/API/MCP 依赖。
- 工作树/提交：`D:\project\pan-worktrees\minimal-requirements-memory-off-20260911`；实现 `37199dc`；main `421c591`。
- 测试/未验证：指定 Python 60 passed；缺 `pytest-timeout` 有 warning；未启动服务或执行真实 API/MCP/browser E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-015：Pan 通知、系统提醒与 msgBridge（旧编号 16）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：通知前缀、Session 通知开关、持久提醒、msgBridge UI、Windows sender 和 MCP 工具已接入，`agent_notify` 语义保持不变。
- 提交：来源 `1033582`、Windows 修复 `5406953`；main `0b23a86`。
- 测试/未验证：后端 95 passed、compileall、前端通知 68 tests、Windows sender/API/MCP 部分 E2E 通过；Chromium 权限与桌面可见性、provider/mobile E2E 仍未完全验证。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-016：Pan MCP 工具清单与 skill 同步审计（旧编号 17）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 当前阶段：已合入，待开发者验收
- 结论：以 `packages/mcp/server.py` 为事实源同步 Pan skill，确认 49 个 Pan 工具、7 个 pan-qq 工具及 worker 兼容别名。
- 工作树/提交：`D:\project\pan-worktrees\pan-skill-tool-sync-20260912`；来源 `c5239c41`；main `683f012`、`cf7ff82`。
- 测试/未验证：静态工具检查、py_compile、pytest 28 passed / 1 skipped、diff check 通过；未启动服务或访问 8765/8767/8768。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-017：搜索结果双击文件夹后搜索目录不更新（旧编号 18）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：目录单击延迟 250ms，双击取消选择并更新 `path\\`，文件点击保持立即选择。
- 工作树/提交：`D:\project\pan-worktrees\pan-directory-input-doubleclick-20260912`；`d7930831`；main `683f012`。
- 测试/未验证：目录/InputRow/Vitest、tsc、ESLint、diff check 通过；NewSessionModal 仍有 4 个既有断言失败。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-018：Windows Markdown 带行号链接打不开（旧编号 19）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：识别单个前导斜杠加 Windows 盘符，保留 Unix rooted/UNC/file URL 与行号格式。
- 工作树/提交：`D:\project\pan-worktrees\pan-markdown-windows-line-link-20260912`；`051018fe`；main `683f012`。
- 测试/未验证：Windows href 与 MarkdownRenderer 回归 13 tests passed、tsc、ESLint、diff check 通过；未做真实服务/磁盘 E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-019：Codex Steer 按钮在 Worker running 时偶尔消失（旧编号 20）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入，待开发者验收
- 结论：InputRow 改用同一 Session 的 workerStore live running 状态和 session-level steer endpoint，避免 stale summary。
- 工作树/提交：`D:\project\pan-worktrees\pan-steer-visibility-20260912`；`8f69c96`；main `cf7ff82`。
- 测试/未验证：相关 Vitest 99 passed、tsc、Python worker branch 2 passed、diff check 通过；未做真实 browser/API E2E。
- 有序待办：
  - [x] 合入 main
  - [ ] 开发者验收

### T-020：Session Detail React #310、System prompt 与 Codex quota

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端
- 决策门：无；实现路线不改变用户需求或数据语义
- 当前阶段：已合入，等待开发者验收
- 目标/调查结论：summary Session 不含 `systemPrompt`，Details 原先未请求完整 Session；五小时 Codex 窗口后端归一化为 `kind=five_hour`，旧前端过滤规则丢弃；TopBar 原先只读 Worker live quota，Worker 离线或重渲染时额度消失。
- 实现：Details 按 ID 请求完整 Session 并保留 summary fallback；Detail 与 TopBar 统一读取 `/api/sessions/{id}/usage` 的 provider-profile 持久化 projection；支持五小时/周/月窗口和未知/空数据过滤。
- 工作树/分支：`D:\project\pan-worktrees\session-detail-system-prompt-20260912`；`fix/session-detail-system-prompt-20260912`；基于 `main@1be6a5c`；修复提交后 clean。
- TA/任务：`ses_fe7e472cf96c96af`；`gpt-5.6-luna` / effort `high`；TA done；MA 独立复跑通过；TA Session 已清理。
- 提交/整合：功能提交 `5f545f5`；合并提交 `c1a5ace`；`main` 已确认包含该分支。
- 测试/未验证：前端 4 files / 24 tests、ESLint、`tsc -b` 通过；相关后端 64 passed（排除已有 quota MCP docstring 断言失败）；未做真实服务/API/browser E2E。
- 有序待办：
  - [x] 调查并确认根因
  - [x] 实现与回归测试
  - [x] 定向验证
  - [x] 合入 main
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（`main@c1a5ace`，祖先关系核对成功）；push：否。

## 二、已完成但尚未合入 main 的改动

当前暂无。

## 三、正在进行的任务/改动

### T-021：消息附件拖动到发送输入框的 UI demo

- 约束策略：`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：先做 UI demo；开发者确认后再拆分/启动后端实现
- 当前阶段：TA 进行最终发布前自检与必要修复，未合入 main
- 目标：附件从消息栏拖入发送输入框时显示文字中的插入光标；释放后在光标位置插入保留附件图标和文件名的可视化附件节点，而不是普通链接文字。节点作为整体可删除，前后可继续输入。
- TA/任务：`ses_f1bebe98cb738ec3`；`attachment-dnd-ui-demo-20260913`；Worker `worker-2`
- 工作树/分支：`D:\project\pan-worktrees\attachment-dnd-ui-demo-20260913`；`feature/attachment-dnd-ui-demo-20260913`；基于 `main@f76bcd1`
- 提交：初始 demo `29a3e786b52e7b0742e2c23d6d084ed422ef4988`；第二轮修复 `978a4d7f4d975f7d6d73295611be229c525be6bf`；未合入、未 push；TA worktree clean
- 测试/未验证：第二轮定向 Vitest 3 files / 48 passed、全量 Vitest 433 passed/10 个既有基线失败、`tsc -b`、ESLint、build、diff check 通过；真实 browser/mobile E2E 和真实服务/API 发送链路未验证
- 开发者反馈（2026-09-13）：附件旁输入文字会重复；已插入附件不能继续拖动调整位置；需要补齐一次性直接上传附件的 mock 流程。
- 跟进任务：`attachment-dnd-ui-demo-followup-20260913`，复用上述 TA/Worker 和分支；已完成。
- 最终自检任务：`attachment-dnd-ui-demo-final-audit-20260913`；要求覆盖输入/光标、外部拖入、内部重排、删除/焦点、mock 上传、真实模式兼容、会话/草稿/队列和边界场景，并先修复本次引入问题再报告。
- 有序待办：
  - [x] 实现拖动、插入光标和附件节点 UI
  - [x] 添加回归测试并完成定向验证
  - [x] 提供 demo 启动方法和已知限制
  - [x] 修复开发者反馈并补充回归测试
  - [ ] 完成最终发布前自检、必要修复和全量验证
  - [ ] 开发者确认 UI demo
  - [ ] 进入后端实现（需开发者确认后）
  - [ ] 合入 main（仅后端完成并测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

## 四、计划要做的任务

后端附件拖动/发送协议与持久化实现：等待 T-021 UI demo 经开发者确认后建立新的 `T-nnn`，不得复用 `T-021`。
