# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)。旧 overview 已删除，历史内容已迁移到本文件，不再并行维护。
>
> 维护者：Pan SMA；迁移日期：2026-09-12

## 当前项目事实

- 项目根：`D:\project\Pan-main`（`git rev-parse --show-toplevel` 已核对）。
- `main`：`3b6b2d7`；T-025 文件输入与结构化附件协议已合入并完成归档；未 push。
- `practical`：`D:\project\Pan`，`1be6a5c`；本次工作流文档迁移未改动。
- `main` 既有 dirty/untracked：`docs/references/cli-adapter-special-behaviors.md`；本次将按用户要求提交。`.vite/` 与 `docs/developLog.md` 已加入 ignore。
- 工作流迁移提交：`3311ee5`；规范文件已进入当前 `main`，旧 overview 已删除。

## 工作流控制

- 整体状态：T-027 并行实施中；紧急批次合入并通知后，继续推进所有设计与任务直到彻底阻塞
- 当前焦点：输入框/发送事务与路径投影/editor 拖动；QQ 通道调查已取消
- 可执行：T-027.2 组合验证与整合
- TA 执行中：T-027.2 `ses_a59709c910ad4859`（Luna high，worker-3）；T-027.1 已完成并合入 main
- 后置动作：T-027.1/T-027.2 完成、发送体验 E2E 通过并合入 main 后，通过 QQ 私聊联系人“焕之”（用户本人）发送固定正文：`紧急修复已经合入main，待验收`
- 持续推进规则（2026-09-15）：紧急批次完成后不得自动停工；重新扫描 overview，持续处理可执行的设计、实现、验证、整合和归档动作，直到只剩用户决策、授权、外部条件或开发者验收阻塞。
- 已暂停：T-026 的原 Worker 与实现动作；其历史要求已并入 T-027 审查范围。T-029 仅完成挂起立项，未开始推进
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

### DEC-001：文件复制/拖入输入框的正式实现方案

- 状态：已决定（A + C，2026-09-13）
- 影响功能和阶段：T-023 方案调查及其后续文件输入、附件引用、发送和持久化实现；T-023 当前被本决策直接阻塞
- 已核实事实：系统文件复制/拖入在浏览器中通常提供 `File`/`FileList`，应上传文件内容而不是客户端绝对路径；目录依赖额外且兼容性不一的 handle/entry API；当前 Pan 已有服务端文件和客户端上传两条链路，但发送协议仍以 Markdown 文本为主
- 选项 A：只支持普通文件的复制/粘贴和拖入，目录明确拒绝；先复用现有上传接口和 Markdown 校验，复杂度最低、兼容性最好
- 选项 B：支持普通文件和目录递归上传；需要目录枚举、相对路径、批量进度、取消、部分失败和跨浏览器 fallback，复杂度较高
- 选项 C：直接采用结构化 `AttachmentRef`/`parts` 发送协议；客户端文件、服务端文件和消息附件统一使用 session-scoped opaque `attachmentId`，长期最稳健但需要改 queue/history/WebSocket 和旧协议兼容
- 推荐：A + C；第一阶段先实现普通文件输入并拒绝目录，同时采用结构化附件引用；目录能力以后单独建立任务
- 最终决定：A + C。第一阶段支持普通文件复制/粘贴和拖入，目录明确拒绝；同时采用结构化 AttachmentRef/parts 协议，兼容旧 text/Markdown 协议。
- 决定来源：开发者会话，2026-09-13；用户明确回复“使用A+C”。
- 决策要求：请明确选择 A、B、C 或 A + C；可同时补充目录、重复文件、上传时机和未发送附件生命周期规则
- 决定后动作：已解除 T-023 决策阻塞并建立 T-025 正式实现任务；本阶段不实现目录递归上传。T-026 为已合入实现的路径表示修复，不改变 A+C 决策。

### DEC-002：输入框、跨 Session 附件与发送事务语义

- 状态：已决定（2026-09-15）
- 影响：T-027 及其输入框、附件、editor 文件链接和发送实现
- 最终决定：输入框上下的待发送 chip、输入框内附件节点不能跨 Session；对话正文中渲染出的文件路径/editor 链接可以跨 Session 拖入，并直接复用服务端文件引用，不重复上传；点击 Send 立即清空并乐观显示已发送，失败时恢复一份可编辑副本；服务端文件使用实时路径；客户端文件只在上传阶段作为浏览器 File，上传完成后统一按服务端本地文件处理；复制的图片文件和 HTML 文件保持原文件上传，只有网页富文本粘贴转换为安全纯文本；目录或目录混合拖入整批拒绝；同一资源允许在一条消息中出现多个 occurrence。
- 约束：浏览器不暴露或信任客户端绝对路径；editor/download href 只是 UI 投影；Worker 文本由服务端解析为实际路径；跨 Session 的正文文件链接必须通过服务端权限和引用校验。
- 决策来源：用户会话，2026-09-15。

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

### T-021/T-022：附件拖放 UI 与真实后端链路

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入本地 main，等待开发者验收
- 目标/结论：完成消息附件拖入、输入框内附件重排、附件节点保留图标、Ctrl+A/Delete 删除语义、客户端上传、附件引用安全校验、队列发送和 Session history 持久化；未嵌入的上方附件 chip 仍会正常发送并追加到消息尾部，嵌入附件按编辑器位置发送。
- TA/工作树：`ses_f1bebe98cb738ec3`；`D:\project\pan-worktrees\attachment-dnd-ui-demo-20260913`；`feature/attachment-dnd-ui-demo-20260913`；worktree clean
- 提交/合并：后端提交 `480e63e064a5bdc0be5662b9e6f28577484826bd`；合并提交 `784866b`；未 push
- 测试/未验证：真实 Chromium 4/4、真实 API 8767 回归、定向前端 73/73、相关后端队列/API 测试、TypeScript、ESLint、build、diff check 通过；全量 Vitest 有 10 个既有基线失败；完整 Python pytest 受 QQ 可选依赖缺失和 2 个既有失败影响；Firefox/Safari/移动端与真实生产服务链路未验证。
- 有序待办：
  - [x] UI demo 实现与开发者验收
  - [x] 真实后端上传、引用校验、发送和持久化接入
  - [x] 分层测试与真实隔离 API/浏览器验证
  - [x] 合入 main
  - [ ] 开发者验收

### T-024：Pan 解释器的 config.json 配置与环境变量优先级

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入本地 main，等待开发者验收
- 目标/结论：支持顶层 `config.json.python` 配置；优先级为 `config.json.python` > `PAN_PYTHON` > 当前 Pan 进程的 `sys.executable`。支持字符串、argv 数组和 `{command,args}` 结构，非法候选明确回退，不把坏值写入 MCP descriptor 或 worker argv。
- 范围：统一接入 manifest 展开、MCP descriptor、cbc/claude/kimi/opencode/codex wrapper、durable background runner、Windows start/restart/exit 脚本和 config reload 生效边界。
- TA/工作树：`ses_43962b89b225afde`；`D:\project\pan-worktrees\pan-interpreter-config-priority-20260913`；`feature/pan-interpreter-config-json-priority-20260913`；worktree clean
- 提交/合并：功能提交 `8b61ddba3ea880a3795e4ede1b68df5ef2838f51`；合并提交 `17632cc`；未 push
- 测试/未验证：相关 resolver/manifest/MCP/adapter/background/lifecycle/reload 测试、`tests/` 排除既有 quota docstring 失败的完整 runnable 集合、compileall、JSON/PowerShell 检查通过；完整 pytest 仍有 1 个既有失败，QQ 测试缺少可选 nonebot 依赖；前端未执行（本次无前端源码变更）。
- 有序待办：
  - [x] 审计解释器解析与所有消费路径
  - [x] 实现 config.json > PAN_PYTHON > 默认优先级
  - [x] 添加非法配置、Windows 路径、MCP command 和热加载边界回归
  - [x] 完成测试并检查 worktree clean
  - [x] 合入 main
  - [ ] 开发者验收

## 二、已完成但尚未合入 main 的改动

当前暂无。T-023 为纯调查任务，没有待合入的代码或文档改动；其结论和 A + C 决策保留在 DEC-001，并已用于已合入的 T-025。

## 三、正在进行的任务/改动

### T-027：输入框、附件与发送链路完整审查及方案设计

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：TA 审查完成；用户已确认决策，子任务并行实施中
- 下一动作：等待 T-027.1/T-027.2 完成各自实现和验证，由 SMA 集成后再做最终验收
- 决策门：已解除；产品语义记录于 DEC-002
- 调查结论：TA 已完成全链路审查。高优先级问题包括 Send 成功后 DOM 未清空、发送/会话切换竞态、HTML paste 未强制纯文本、Worker 当前仍可能收到 API href、queue 编辑 text/parts 不一致，以及已渲染 editor 普通本地文件链接缺少统一附件拖动 payload。报告还指出当前审查 Session workdir 不是独立 Git worktree，未进行真实 Windows Explorer/桌面剪贴板验收。
- TA Session：`ses_cf22dbcbd289c6e9`；模型 `gpt-6-astra`；effort `low`；权限 `read-only`
- 目标：一次性检查 Pan 当前所有输入框、附件、粘贴/拖入、编辑器渲染、发送和状态清理代码链，给出完整修复方案；实际修复待本次审查完成并经 SMA 审查后，另派 Luna high/xhigh 执行
- 必查问题：附件插入后文字重复；输入框内附件继续拖动和重排；Ctrl+A/Backspace/Delete/删除按钮语义；未嵌入附件 chip 与嵌入节点发送语义；点击 Send 后输入框文本、draft、parts 和附件状态未清空；上传、取消、失败、重试和会话切换时序；复制/粘贴/拖入文件及目录拒绝策略；网页 HTML 粘贴导致整页进入输入框
- 路径与渲染重点：AI/Worker/adapter 文本使用服务端实际绝对路径；UI 仅将其渲染为 editor/下载 API 链接；审查服务端目录文件或历史文件已渲染为 `editor` 后是否仍可打开、识别并再次拖入输入框作为附件；设计打开/下载与附件拖动的元数据、MIME、拖放优先级和安全边界
- 兼容与协议：A + C（普通文件 paste/drop、目录第一阶段拒绝、结构化 AttachmentRef/MessagePart），同时覆盖旧 Markdown、旧 `@"path"`、`/api` history、queue、WebSocket、history、重试和重启恢复
- 审查边界：只读检查、源码数据流分析、必要的安全复现/测试观察；不得修改业务代码、不得提交或合并；不能把合成 DataTransfer 测试当作 Windows 文件管理器真实验收
- 交付报告必须包含：完整代码链路图、每个用户问题的事实/推断/未知区分、可复现条件、根因排序、editor 打开与再次拖动的可行方案对比、推荐架构、API/数据模型/渲染边界、测试矩阵、分阶段实施计划、迁移兼容和安全风险、待产品决策点
- 前置背景：T-026 `ses_2e3ce9ae8faa0645` 已暂停；其“绝对路径分层、对话本地文件链接拖入、Send 清空”要求全部并入本审查，不复用其 Session

### T-027.1：输入框、发送事务与粘贴/拖入状态修复

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入 main，待 T-027.2 组合验收及开发者验收；不得修改 T-027.2 的服务端/renderer 文件
- 历史阻塞：共享 worktree 元数据、依赖和测试环境曾阻塞验证，已通过正常重试解决；未删除锁、未修改 ACL、未使用替代 index。
- 目标：修复 Send 乐观清空与失败恢复、Session/revision 竞态、DOM/parts/draft 清理；统一附件 occurrence/resource 状态；修复普通输入、附件拖动重排、Ctrl+A/Backspace/Delete；处理文件 paste/drop、网页 HTML 转纯文本、图片/HTML 文件保持原文件上传、目录整批拒绝和客户端路径不可信。
- 工作树/分支：`D:\project\pan-worktrees\input-attachment-composer-send-20260915`；`feature/input-attachment-composer-send-20260915`
- TA/任务：`ses_7079ef10a62ddf5b`；`input-attachment-composer-send-20260915`；Luna xhigh；Worker `worker-2`；已完成
- 提交/整合：功能提交 `33aa52ada9cbe92037b5e44b1a90c3820776299d`；合并提交 `7a9c7e89cd8e313d6e506b39ba99842465603a8a`；契约补齐提交 `fadebefcdda7094ba59b321685817921996fe2c2` 已快进合入 main；当前 main 为 `fadebefcdda7094ba59b321685817921996fe2c2`；未 push
- 测试：定向 Vitest 4 files / 73 tests、TypeScript、ESLint、Prettier 通过；隔离 Chromium E2E 6/6 通过；完整 E2E 仍有既有 streaming 基线失败；未做真实 8768 API/服务端集成
- 有序待办：
  - [x] 完成实现并添加回归测试
  - [x] 完成前端定向、类型、lint、浏览器验证
  - [x] 提交并检查 worktree clean
  - [x] 合入 main（测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

### T-027.2：服务端路径投影与 editor 文件链接跨 Session 拖动

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：功能实现和专项验证完成；最终提交 a25091c 已生成，正在同步当前 main@08d88f1 并做最终组合验证；不得修改 T-027.1 的 composer/send 文件
- 目标：将服务端文件/附件在 Worker 文本中投影为实时绝对路径，在 UI 中继续使用安全 editor/download href；让对话正文中渲染出的文件路径/editor 链接可跨 Session 拖入并复用服务端文件，不重复上传；保留打开、下载、行号定位、Windows/UNC/file URI 和旧 Markdown 兼容及权限校验。
- 工作树/分支：`D:\project\pan-worktrees\attachment-path-editor-drag-20260915`；`feature/attachment-path-editor-drag-20260915`
- TA/任务：`ses_a59709c910ad4859`；`attachment-path-editor-drag-20260915`；Luna high；Worker `worker-3`；正在同步 main@08d88f1 并最终复验
- 有序待办：
  - [ ] 完成服务端/renderer/drag payload 实现并添加回归测试
  - [ ] 完成后端/API/结构化协议、前端定向和浏览器验证
  - [ ] 提交并检查 worktree clean
  - [ ] 合入 main（测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

### T-028：QQ 通道未连接归因调查（Pan vs llbot）

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已取消；用户已确认根因为端口占用，不再继续调查或实现
- 取消原因（2026-09-15）：用户明确取消 QQ 调查任务，并确认问题属于端口占用。
- 目标：调查 QQ 通道显示未连接的根因属于 Pan、llbot，还是两侧之间的配置/网络/协议连接边界；给出可复现证据、责任边界和下一步修复方案。
- 调查范围：Pan QQ adapter、bot/channel 生命周期、llbot WebSocket/HTTP 连接、鉴权与配置、端口/URL、心跳/重连、消息收发和状态映射、日志与错误吞噬；明确“Pan 未连接”“llbot 未连接”“连接存在但状态未同步”的区分。
- 约束：不得操作受保护的 8768；不得停止或修改现有 QQ 服务；优先静态审查、配置核对和隔离环境证据，真实外部连接未验证必须明确记录。
- 工作树/分支：`D:\project\pan-worktrees\qq-channel-connection-audit-20260915`；`audit/qq-channel-connection-20260915`
- TA/任务：`ses_883290f5f803a034`；`qq-channel-connection-audit-20260915`；已取消；Session 已不存在，专用 clean worktree 已移除
- 有序待办：
  - [ ] 核对 Pan 与 llbot 的实际连接链路和状态来源
  - [ ] 收集最小复现和日志/错误证据，区分事实、推断、未知
  - [ ] 给出责任归因、修复方案、测试矩阵和需要用户提供的外部信息
  - [ ] 调查报告交付并由 SMA 审查
  - [ ] 开发者验收（若后续进入实现，另建实现阶段）

### T-026：附件 AI 绝对路径与 UI 下载 API 渲染分层

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已暂停，未合入 main
- 暂停原因（2026-09-14）：用户要求先记录当前任务，然后暂停其他所有动作，等待一件最高优先级事项。
- 目标：给 AI/Worker/adapter 的文本使用服务端真实绝对路径；UI 消息渲染时再将已验证的路径或 attachmentId 转换为 editor/下载 API 链接。
- 新增需求（2026-09-14）：对话中出现的、非附件 chip 的本地文件 Markdown 链接也可直接拖入输入框；支持 Windows/UNC/file URI 及带行号目标，行号只用于定位，不进入附件实际路径；普通 HTTP 网页链接不误判为附件。
- 产品语义：客户端文件上传后使用服务端实际存储路径；服务端文件使用已验证实际路径；浏览器不提交或信任客户端绝对路径；上方 chip 和编辑器内嵌附件的发送语义保持不变。
- 开发者反馈（2026-09-14）：点击 Send 后输入框内文本没有清空；需要检查结构化 parts 入队成功后的 composer、draft 和附件状态清理时序。
- 兼容范围：结构化 AttachmentRef/parts、旧 text/Markdown、旧 `@"path"`、旧 `/api` history、queue、WebSocket、history、重试和重启恢复。
- TA/任务：`ses_2e3ce9ae8faa0645`；`attachment-path-rendering-separation-20260914`；Worker `worker-3`
- 工作树/分支：`D:\project\pan-worktrees\attachment-path-rendering-separation-20260914`；`feature/attachment-path-rendering-separation-20260914`；基于 `main@e1d11a9`
- 有序待办：
  - [ ] 审计当前 AI 文本、结构化 parts 和 UI renderer 的所有表示边界
  - [ ] 实现服务端绝对路径到 AI 文本、UI 安全 API href 的分层转换
  - [ ] 修复 Send 成功后输入框文本、draft 和已发送附件未清空的回归
  - [ ] 支持对话中的本地文件 Markdown 链接拖入并转为服务端附件引用
  - [ ] 添加 client upload/server file、旧历史、跨 session/stale/path traversal 回归
  - [ ] 完成真实 API/浏览器和全量门禁验证
  - [ ] 提交并检查 worktree clean
  - [ ] 合入 main（测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

## 四、计划要做的任务

### T-029：Session queue 查询与修改 MCP

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-2`、`TEST-1`、`TEST-2`
- 当前阶段：已挂起立项，不推进
- 目标：为 MCP 增加查询 Session queue 及受控修改 queue 的能力；具体覆盖 queue_pending、worker/发送队列或其他 queue 类型的范围，待恢复后调查并明确。
- 初始范围：查询队列内容、状态和来源；提供受权限隔离、幂等和审计约束的修改操作；明确可修改字段、取消/编辑/重排/删除语义，以及与报告队列、任务队列、消息队列的边界。
- 挂起原因（2026-09-15）：用户要求先加入待办，挂起立项，不推进。
- 下一动作：等待用户明确恢复后，再进行方案调查和接口设计；在此之前不派 TA、不创建 worktree、不修改 MCP 或服务端代码。
- 合入 main：未开始
- 开发者验收：未开始

### T-030：done 事件已传出但状态指示灯延迟更新

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-2`、`TEST-1`、`TEST-2`
- 当前阶段：计划待办，未开始
- 目标：调查并修复系统已发送或收到 done 状态后，前端状态指示灯仍长时间保持旧状态的延迟问题。
- 初始范围：核对 done 事件来源、WebSocket/队列/轮询传输、session/worker 状态存储、前端订阅与渲染、去抖/批处理/重连/缓存/竞态；区分事件未到达、到达未落库、落库未广播、广播未消费和 UI 未重绘。
- 下一动作：等待前置任务完成或用户明确推进后，派 TA 做证据化调查；当前不派发、不修改代码。
- 合入 main：未开始
- 开发者验收：未开始

新需求必须分配新的 `T-nnn`，不得复用已完成任务 ID。
