# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)。旧 overview 已删除，历史内容已迁移到本文件，不再并行维护。
>
> 维护者：Pan SMA；迁移日期：2026-09-12

## 当前项目事实

- 项目根：`D:\project\Pan-main`（`git rev-parse --show-toplevel` 已核对）。
- `main`：`c48c3a9`；T-024 解释器配置实现及工作流记录已合入本地 main；未 push。
- `practical`：`D:\project\Pan`，`1be6a5c`；本次工作流文档迁移未改动。
- `main` 既有 dirty/untracked：`docs/references/cli-adapter-special-behaviors.md`；本次将按用户要求提交。`.vite/` 与 `docs/developLog.md` 已加入 ignore。
- 工作流迁移提交：`3311ee5`；规范文件已进入当前 `main`，旧 overview 已删除。

## 工作流控制

- 整体状态：T-024 解释器配置已合入本地 main；T-025 已在独立 worktree 完成实现、分层回归并提交，等待 MA 按 AUTH-001 合入本地 main
- 当前焦点：T-025 交付 MA；解释器配置和本功能均等待开发者验收
- 可执行：仅允许 MA 在测试证据核对后合入本地 main；本 TA 不 merge、不 push
- TA 执行中：本 worktree 已完成；T-023 与 T-024 TA 均已 done
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
- 最终决定：A + C；第一阶段实现普通文件输入并拒绝目录，同时采用结构化附件引用；目录能力以后单独建立任务
- 决策要求：请明确选择 A、B、C 或 A + C；可同时补充目录、重复文件、上传时机和未发送附件生命周期规则
- 决定后动作：已解除 T-023 的决策阻塞并建立 T-025；本阶段不实现目录递归上传

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

### T-025：普通文件 paste/drop 与结构化 AttachmentRef/parts

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：TA 已完成并提交独立 worktree；不 merge、不 push；由 MA 按 AUTH-001 合入本地 main
- 决策：采用 A + C。浏览器只上传 File/FileList 内容；Pan 自定义附件 MIME 优先；目录、directory entry/handle 与文件 URI 第一阶段拒绝；queue/WS/history 保留结构化 parts 与 text fallback。
- 工作树/分支：`D:\project\pan-worktrees\attachment-file-paste-structured-parts-20260913`；`feature/attachment-file-paste-structured-parts-20260913`；基于 `main@c0bf345`
- 协议文档：[attachment-parts-protocol.md](../docs/design/attachment-parts-protocol.md)
- 已实现：`AttachmentRef`/`MessagePart` 类型、session sidecar registry、客户端上传与服务端文件注册统一 opaque id、queue/WS/history/retry/restart parts 保留、text/Markdown/`@"path"` 兼容、adapter 边界 Markdown fallback、mock=1 内存语义、目录/entry/handle/URI 拒绝、native MIME 优先和取消上传。
- 已验证：相关前端 Vitest 59/59；相关后端/API/WS/queue/history 63/63；8765 mock Chromium 6/6；tsc、build、eslint（0 error）、diff check 通过。全量 Vitest 456/466，10 项为既有 Toast jsdom/NewSessionModal 基线；全量 pytest 因 QQ 可选依赖 `nonebot` 缺失在 collection 阶段阻塞。真实后端 Chromium 8765 套件另有既有 streaming scroll 基线失败，其余用例通过。
- 未验证/边界：Firefox/Safari/移动端、生产链路、目录递归上传；目录能力以后单独建立任务。8768 未启动、未请求、未操作。
- 有序待办：
  - [x] 普通文件 paste/drop、上传进度/取消/重试/去重/会话切换
  - [x] 结构化 parts 贯通 queue、WebSocket、history、重试与恢复
  - [x] 真实 Chromium/Vite 与隔离 API 回归
  - [x] 提交当前分支并保持 worktree clean
  - [ ] MA 按 AUTH-001 合入本地 main
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

当前暂无。

## 三、正在进行的任务/改动

### T-023：文件复制/拖入输入框的客户端与服务端方案调查

- 约束策略：`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：TA 已完成调查，等待开发者决策；未修改正式功能代码、不提交、不合入 main
- 目标：明确系统文件复制粘贴、文件拖入、目录拖入、网页附件拖动的真实浏览器数据形态，并提出能同时支持客户端文件与服务端文件的实现方案。
- 范围：浏览器 paste/drop DataTransfer、File/FileList、目录 handle/entry、绝对路径安全边界、当前 Pan 附件链路、AttachmentRef/parts 协议、上传/取消/进度/持久化和兼容性。
- TA/任务：`ses_f1bebe98cb738ec3`；`attachment-file-paste-drop-design-investigation-20260913`；Worker `worker-3`
- 有序待办：
  - [x] 核对浏览器复制文件、拖入文件和目录的事实行为
  - [x] 审计当前客户端/服务端附件链路与缺口
  - [x] 给出至少三套可选方案、推荐分阶段方案和产品决策项
  - [ ] 开发者决策方案
  - [ ] 决策后建立实现任务，不在本调查任务中实现

## 四、计划要做的任务

文件复制/拖入输入框的正式实现：等待 T-023 方案调查和开发者决策后建立新的实现任务。
