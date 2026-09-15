# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)。旧 overview 已删除，历史内容已迁移到本文件，不再并行维护。
>
> 维护者：Pan SMA；迁移日期：2026-09-12

## 当前项目事实

- 项目根：`D:\project\Pan-main`（`git rev-parse --show-toplevel` 已核对）。
- `main`：`fc7fd44`（2026-09-15 核对，`git status` clean）；T-025 文件输入与结构化附件协议已合入并完成归档；T-027.1 / T-027.2 已合入、待开发者验收；**未 push**。
- `practical`：`D:\project\Pan`（practical 工作树）；本次工作流文档维护未改动它。
- TA worktree 根：`D:\project\pan-worktrees`；新 TA worktree 必须先用 `git worktree add` 注册后交付。
- 受保护服务：`8768`（未经用户授权不得重启/停止/修改）；隔离验证实例按任务分配端口（当前 T-031 = `8767`、T-033 = `8766`；`8765` 被其他 worktree 占用，不得停止或复用）。

## 工作流控制

- 整体状态：T-027 批次与 T-030、T-035 均已合入 main（T-030 合并提交 `7f2667b`；T-035 合并提交 `82823ad`）；T-031 已完成真实验证但发现服务端隔离缺陷，T-033 接续仍在执行，T-037 已派发修复，T-032 调查已闭环待 `DEC-003`；继续推进直到只剩用户决策/授权/外部条件/开发者验收阻塞
- 当前焦点：T-035 移动端 Detail 全屏；T-031 / T-033 两项独立真实实例验证；`DEC-003` 决策（T-032 第 2 档）
- 可执行：无（T-031、T-033、T-035 已派发在跑；T-034、T-036 待并发/额度窗口；其余任务待开发者验收或被用户暂停/取消）
- TA 执行中/待验收：T-031 `ses_826c588ce84122b5`（codex `gpt-5.6-luna`，high，bypass，隔离端口 8767，报告已到达）；T-033 接续 `ses_c8671119a2dd9bb4`（cbc `deepseek-v4.1-flash`，high，bypassPermissions，隔离端口 8766；原 `ses_1385abb15d4b3b3e` 已归档）；T-035 `ses_34efb6996f826a27`（cbc `deepseek-v4.1-flash`，high，bypassPermissions，已合入）；T-037 `ses_022781914942f1b2`（cbc `deepseek-v4.1-flash`，high，bypassPermissions，附件 Session 隔离修复）。
- 后置动作：已通过 QQ 私聊联系人“焕之”（用户本人）发送固定正文：`紧急修复已经合入main，待验收`；message_id `504271875`（不重复发送）
- TA 模型规则（2026-09-15 用户会话）：新 TA 优先 `MODEL-1`（codex `gpt-5.6-luna` high）；Codex 五小时额度触发限额后按 `MODEL-3` 级联（`cbc deepseek-v4.1-flash` → 限速 → `cbc glm-5.3-flash` → 仍限速 → 回 DeepSeek）。已派发中的 TA 不回溯切换模型。
- 用户追加口径（2026-09-15，Codex 五小时额度实测 96%）：**新任务直接派 `cbc deepseek-v4.1-flash`**（T-035 即按此派发）；**已在跑的 codex luna 任务（T-031 / T-032 / T-033）不重做、不再消耗 Codex 额度，待其停下后用 `session_handoff` 接续到 `cbc deepseek-v4.1-flash`**，接续时以原任务 brief + worktree 现场 + 源 session 历史为交接材料。
- 持续推进规则（2026-09-15）：紧急批次完成后不得自动停工；重新扫描 overview，持续处理可执行的设计、实现、验证、整合和归档动作，直到只剩用户决策、授权、外部条件或开发者验收阻塞。
- 已暂停：T-026 的原 Worker 与实现动作；其历史要求已并入 T-027 审查范围。T-029 仅完成挂起立项，未开始推进
- 决策阻塞：`DEC-003`（T-032 第 2/3 档实现范围；不阻塞其他任务）
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

### DEC-003：报告/队列身份贯通（T-032 第 2 档实现范围）

- 状态：待决策
- 影响功能和阶段：T-032 调查结论落地（报告粒度与任务身份贯通）；`agent_send` 的 MCP 参数面；T-029（挂起）将来恢复时的模型统一
- 已核实事实（MA 抽查行号属实）：报告 item **无** `sourceQueueItemId`/`clientMessageId`（`packages/core/worker.py:2668-2717`）；`agent_send` MCP 只有 `(session_id, text)`，无 `task_id`/`client_message_id`，也不返回 `queueItemId`（`packages/mcp/server.py:1364-1394`）；连续 report/QQ 项会**合并**成一条 delivery unit（`packages/core/worker.py:1324-1345`）；`GET /api/sessions/{id}/queue` 只显示 `queued` 子集（`packages/web/server.py:3447-3478`）；`sent_to_cli` ≠ provider 业务完成。
- MA 建议：采纳**第 2 档**——报告携带 `sourceQueueItemId`/`taskId`/`clientMessageId`/`deliveryUnitId`，完成/idle 时给结构化队列摘要，`agent_send` 增加可选 `client_message_id` 并返回 `queueItemId`；**不改变**现有 FIFO 与 at-most-once 交接语义。**第 3 档**（QueueItem → DeliveryAttempt → Receipt → CompletionEvent → ReportDeliveryUnit → MA ack 的完整事件模型）与挂起的 T-029 一并延后。
- 选项（逐条勾选或直接写结论）：
  - [ ] A1：是否规定"需要独立验收的追加任务必须用 `agent_assign(task_id=...)`，`agent_send` 只作补充信息"？（MA 建议：是）
  - [ ] A2：是否为 `agent_send` 增加可选 `client_message_id` 并返回 `queueItemId`？（MA 建议：是）
  - [ ] A3：报告模型——每个源队列项一条 report／允许批量但必须带结构化 `reports[]`／两者都支持？（MA 建议：两者都支持，批量必须带 `reports[]`）
  - [ ] A4：`queueItemId`/`taskId`/`clientMessageId` 是否出现在文本报告里，还是只放结构化字段？（MA 建议：结构化字段 + 文本显示 `queueItemId`/`sourceQueueItemId`）
  - [ ] A5：MA 需要观察到哪一级队列状态——仅 `queued`／`queued`+active／完整 ledger hand-off／provider 终态？（MA 建议：`queued`+active+最近终态，带 `queueRevision`）
  - [ ] A6：T-029 恢复时 queue 查询是否只读、是否允许查询 `reserved`/`writing`/`sent_to_cli` ledger？（MA 建议：只读，允许 ledger）
  - [ ] A7：是否接受当前 at-most-once 的窄重复窗口语义，还是要引入 provider acknowledgement 模型？（MA 建议：保留现状，文档写明边界）
- 未决定时：只执行第 1 档 MA 纪律（已立即采用），不启动第 2/3 档实现；T-032 调查结论保留在 overview。
- 阻塞：T-032 第 2 档实现任务（实现时需新分配 `T-nnn` 与独立 worktree）
- 最终决定：
- 决策来源：T-032 调查结论（用户 2026-09-15 会话委托的调查任务）
- 用户附件/备注：

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

### T-027：输入框、附件与发送链路完整审查及方案设计

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：审查与子任务已实现、验证并合入 main，待开发者验收（T-030 的后续状态问题已合入；服务端广播次序后续项见 T-036）
- 下一动作：等待开发者验收
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
- 当前阶段：已合入 main，待开发者验收；不得修改 T-027.2 的 composer/send 文件
- 历史阻塞：共享 worktree 元数据、依赖和测试环境曾阻塞验证，已通过正常重试解决；未删除锁、未修改 ACL、未使用替代 index。
- 目标：修复 Send 乐观清空与失败恢复、Session/revision 竞态、DOM/parts/draft 清理；统一附件 occurrence/resource 状态；修复普通输入、附件拖动重排、Ctrl+A/Backspace/Delete；处理文件 paste/drop、网页 HTML 转纯文本、图片/HTML 文件保持原文件上传、目录整批拒绝和客户端路径不可信。
- 工作树/分支：`D:\project\pan-worktrees\input-attachment-composer-send-20260915`；`feature/input-attachment-composer-send-20260915`
- TA/任务：`ses_7079ef10a62ddf5b`；`input-attachment-composer-send-20260915`；Luna xhigh；Worker `worker-2`；已完成
- 提交/整合：功能提交 `33aa52ada9cbe92037b5e44b1a90c3820776299d`；合并提交 `7a9c7e89cd8e313d6e506b39ba99842465603a8a`；契约补齐提交 `fadebefcdda7094ba59b321685817921996fe2c2` 已合入 main；未 push
- 测试：定向 Vitest 4 files / 73 tests、TypeScript、ESLint、Prettier 通过；隔离 Chromium E2E 6/6 通过；未做真实 8768 API/服务端集成
- 有序待办：
  - [x] 完成实现并添加回归测试
  - [x] 完成前端定向、类型、lint、浏览器验证
  - [x] 提交并检查 worktree clean
  - [x] 合入 main（测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

### T-027.2：服务端路径投影与 editor 文件链接跨 Session 拖动

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 当前阶段：已合入 main，待开发者验收；不得修改 T-027.1 的 composer/send 文件
- 目标：将服务端文件/附件在 Worker 文本中投影为实时绝对路径，在 UI 中继续使用安全 editor/download href；让对话正文中渲染出的文件路径/editor 链接可跨 Session 拖入并复用服务端文件，不重复上传；保留打开、下载、行号定位、Windows/UNC/file URI 和旧 Markdown 兼容及权限校验。
- 工作树/分支：`D:\project\pan-worktrees\attachment-path-editor-drag-20260915`；`feature/attachment-path-editor-drag-20260915`
- TA/任务：`ses_a59709c910ad4859`；`attachment-path-editor-drag-20260915`；Luna high；Worker `worker-3`；已完成
- 提交/整合：功能提交链 `dd01634`、`9f260e2`、`89ffa1d`、`a25091c`、`10b39cb898719461bfd9372da121dd387c344b8e`；已合入 main，合并提交 `99f1774ac4ec7a88366012fe2011e6bf5c36a3e0`；未 push
- 测试：定向 Vitest 9 files / 110 tests、pytest 13 passed、TypeScript、ESLint、build、附件专用 Chromium 6/6、隔离 API + Chromium 相关场景通过；完整 E2E 仅剩既有 stream 一像素滚动基线失败；未做真实第三方 provider/CLI 发送
- 有序待办：
  - [x] 完成服务端/renderer/drag payload 实现并添加回归测试
  - [x] 完成后端/API/结构化协议、前端定向和浏览器验证
  - [x] 提交并检查 worktree clean
  - [x] 合入 main（测试通过后按 `AUTH-001` 执行）
  - [ ] 开发者验收

### T-030：done 事件已传出但状态指示灯延迟更新

- 约束策略：`GIT-1`、`GIT-2`、`MODEL-3`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端（调查 → 修复 → 验证 → 报告）
- 当前阶段：已合入 main，待开发者验收
- 下一动作：等待开发者验收；服务端广播次序的后续项见 T-036
- 阻塞：无
- 目标：调查并修复系统已发送或收到 done 状态后，前端状态指示灯仍长时间保持旧状态的延迟问题。
- 调查结论（已核实，根因）：指示灯只读 `Session.workerStatus`；`worker.result` 到达时同步写 store，正常路径本就不延迟——缺陷是**事件一旦丢失就再也无法纠正**：`loadSessions()` 用**全局** touch 计数器与**单 session** 值做 `>=` 比较（修复前 `packages/web/src/stores/sessionStore.ts:171`），导致（1）“最后被触碰的那个 session”被永久豁免于权威快照，任何刷新（含 T-013 的重连/焦点恢复）都无法修正，指示灯卡在 `running`；（2）一个 session 的状态判定取决于无关 session 的流量。次要缺陷：`WorkerDot.tsx:6` 缺 `done`/`queued`/`restarting` 配色（渲染成 offline 灰）；`useWebSocket.ts` 的 generation 守卫丢弃终态事件后**无兜底**。
- 修复实现（commit `fb42e76`，11 files，+682/−30）：`sessionStore.ts` 守卫改为**严格按 session** 判定（仅当该 session 自身计数在本次请求进行中前进才保留本地值），并保留两个必要例外（本地显式 `null` 的销毁/崩溃；后端瞬态终态 `done`）；`useWebSocket.ts` 在 generation 守卫丢弃 `worker.result` 时退回防抖权威刷新；`WorkerDot.tsx` 补齐 `done`(success)/`queued`(accent)/`restarting`(warning) 配色；新增 `sessionStore.doneIndicator.test.ts`(7)、`WorkerDot.test.tsx`(10)、`e2e/done-indicator.e2e.mjs`，扩展 `useWebSocket.test.tsx`。
- TA/任务：`ses_2834ad61bb0d5b74`；`done-indicator-latency-20260915`；CBC `deepseek-v4.1-flash`；effort `auto`；权限 `bypassPermissions`；Worker `worker-2`；TA 报告 done（在新模型规则前派发，按规则未回溯切换模型）
- 工作树/分支：`D:\project\pan-worktrees\done-indicator-latency-20260915`；`audit/done-indicator-latency-20260915`；基于 `main@99f1774`；worktree clean
- 提交：`fb42e76`（功能提交）；合并提交 `7f2667b` 已合入 main；未 push
- 测试/未验证项：**MA 独立复跑**——定向 7 files / 102 passed；**MA 独立复跑全量**——501 passed / 10 failed (511)，且已在**修复前**的 commit 上单跑 `Toast.test.tsx` + `NewSessionModal.test.tsx` 复现同样 10 failed，确认既有基线、非本次引入；`tsc -b` 0 错；Chromium E2E `e2e/done-indicator.e2e.mjs` 4/4（隔离端口 8766；修复前焦点恢复后 5s 仍卡 running，修复后 121ms 收敛）；**未验证**——真实 Pan 服务/真实 provider 的耗时实测、真实半开连接/重连（`ws.ts` 无测试）、多 session 并发与 visibility/bfcache、Firefox/Safari/移动端；仓库自带真实服务 E2E（`e2e/run.ps1`）因需 8765 空闲而无法运行（8765 被 PID 7612 占用，未触碰）
- 有序待办：
  - [x] 调查并确认根因
  - [x] 实现与回归测试
  - [x] 定向验证（MA 复跑）
  - [x] 合入 main（`7f2667b`，按 `AUTH-001`）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：是（`main@7f2667b`）；push：否

## 二、已完成但尚未合入 main 的改动

当前暂无。T-023 为纯调查任务，没有待合入的代码或文档改动；其结论和 A + C 决策保留在 DEC-001，并已用于已合入的 T-025。

## 三、正在进行的任务/改动

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

### T-031：已合入 main 的附件与发送链路真实隔离实例端到端验证

- 优先级/依赖：T-027.1、T-027.2 合入后的独立验证；与 T-030 改动文件不重叠，可并行
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端（纯验证任务；发现缺陷只报告，由 MA 决定是否另立返工项）
- 决策门：无；若发现需要改变产品行为/兼容性/数据语义的缺陷，停止并报告，由 MA 建立决策点
- 当前阶段：真实隔离验证已完成；报告发现服务端缺陷 T031-001，暂不能作为全部通过交付
- 下一动作：由 T-037 修复 structured attachment 跨 Session owner 绕过；修复后重跑负路径与相关回归，再回填 T-027.1 / T-027.2 测试证据
- 阻塞：无
- 目标：在隔离服务实例 `8767`（**禁止 8768**；`8765` 未使用）上用真实 HTTP/WS + 真实 Chromium 验证 `main@429cafd` 已合入的附件输入、发送事务与路径投影链路，补齐此前记录的“未做真实服务/API 集成”缺口，为 T-027.1、T-027.2 的开发者验收提供分层证据。
- 验证范围：客户端上传→发送→队列/history→Worker 收到 canonical 绝对路径；UI opaque editor/download href 与跨 Session 拖入复用服务端引用；chip/inline 禁止跨 Session 的负路径；Send 后清空与失败恢复；目录整批拒绝、HTML 富文本转纯文本、图片/HTML 文件保持原文件；旧 Markdown/`@"path"`/带行号/Windows/UNC 回归；`../` 路径穿越、越权引用、stale 路径的安全负用例。
- 边界：只读验证，不得修改产品代码；不 commit/不合入/不 push；不操作 8768、QQ 服务、用户 dirty 文件；真实浏览器行为必须用真实 Chromium，不得只用合成 DataTransfer。
- 工作树/分支：`D:\project\pan-worktrees\verify-attachment-chain-e2e-20260915`；`audit/verify-attachment-chain-e2e-20260915`；基线 `main@429cafd`（MA 已用 `git worktree add` 注册）
- TA/任务：`ses_826c588ce84122b5`；`verify-attachment-chain-e2e-20260915`；codex `gpt-5.6-luna`；effort `high`；权限 `bypass`；Worker `worker-4`；已 `report_subscribe`
- 提交：无（验证任务，不产生产品代码改动）
- 测试/未验证项：真实 HTTP/WS/Chromium（8767）、Python 14 passed、前端 6 files/79 tests、build 均有报告证据；正向链路与大多数负路径通过；structured parts 跨 Session 越权负路径失败（T031-001）；另有既有 browser one-pixel 失败、mobile、第三方 provider/QQ、真实 Finder/桌面剪贴板和真实 provider 崩溃恢复未验证；MA 已核对源码根因，修复后需独立复验；真人开发者验收阻塞
- 有序待办：
  - [x] 建立隔离服务实例并确认端口/身份对齐（非 8768）
  - [x] 完成真实 HTTP/WS + Chromium 端到端用例并记录原始证据
  - [x] 完成负路径与安全边界验证（发现 T031-001）
  - [x] 交付报告；MA 已核对源码根因，修复后再做完整证据复验
  - [ ] T031-001 修复后的独立回归与重新验收
  - [ ] 合入 main（本任务无产品代码改动，如发现缺陷则另立返工项）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：不适用（无产品代码改动）；push：否

### T-032：MA→TA 多任务排队时的报告粒度缺陷（仅调查）

- 优先级/依赖：独立调查；与 T-031、T-033 无文件冲突（纯只读）
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`
- 执行模式：仅调查（只交付解决方案，不实现）
- 决策门：无；若方案涉及改变队列/报告模型或兼容性，只报告并列出需用户决策点，不自行实施
- 当前阶段：调查完成，MA 已核验；解决方案待 `DEC-003` 决策后转入实现
- 下一动作：等待 `DEC-003` 决策；决策生效后派第 2 档实现任务（新分配 `T-nnn`，独立 worktree）
- 阻塞：决策阻塞 `DEC-003`（仅阻塞第 2/3 档实现，不阻塞其他任务）
- 目标：解释并解决「MA 派任务 A → A 执行中追发任务 B → TA 完成 A 后继续执行 B → MA 只收到 A 的报告，误判 B 未执行并重发 B」的报告粒度缺陷，给出分档解决方案与推荐。
- 调查结论（已核实）：存在**三层粒度**——`worker.result`（按 Worker 一次终态结果）、`queue_pending` report item（每个结果一项，自带 `queueItemId`）、MA 消费时的 delivery unit（**连续 report/QQ 项合并成一条消息**）。根因是**任务身份与报告身份没有贯通** + MA 按"消息条数"而非"报告段/任务 ID"计数，而不是 FIFO 必然丢 B。已核验的关键事实（行号均经 MA 抽查属实）：`_enqueue_report`（`packages/core/worker.py:2668-2717`）报告里**没有** `sourceQueueItemId` / `clientMessageId`；`agent_send` MCP 只有 `(session_id, text)`，无 `task_id` / `client_message_id`，也不返回 `queueItemId`（`packages/mcp/server.py:1364-1394`）；`_select_queue_unit` 只批处理**连续** report/QQ 项（`packages/core/worker.py:1324-1345`）；`GET /api/sessions/{id}/queue` 只返回 `queued` 项，隐藏 reserved/sent（`packages/web/server.py:3447-3478`）；`sent_to_cli` ≠ provider 业务完成。
- 推荐方案：第 1 档（MA 纪律，零代码，**已立即采用**）→ 第 2 档（报告与源队列项身份贯通 + 队列摘要，建议作为近期实现）→ 第 3 档（完整队列/报告事件模型，与 T-029 统一，成本与迁移风险高）。
- 文档落点建议：新建 `docs/design/ma-ta-report-granularity.md`（完整模型）；`docs/design/queue-at-most-once.md` 补 report↔source item 关系与"sent_to_cli ≠ 业务完成"；`docs/skills/pan/SKILL.md` 只放 MA 操作纪律与已知限制。
- 关联：与挂起的 T-029（Session queue 查询与修改 MCP）能力缺口相关，但方案不得假定 T-029 已实现；同场景运行时行为证据由 T-033 提供。
- TA/任务：`ses_704ba1fd334045b6`；`ma-ta-task-ordering-20260915`；codex `gpt-5.6-luna`；effort `high`；权限 `bypass`；Worker `worker-1`；已完成报告，MA 抽查核验通过
- 工作树/分支：`D:\project\pan-worktrees\ma-ta-task-ordering-20260915`；`audit/ma-ta-task-ordering-20260915`；基线 `main@52a434b`；worktree clean（无提交）
- 提交：无（仅调查，不产生产品代码改动）
- 测试/未验证项：无自动化验证；结论为机制分析与设计方案（MA 已抽查 4 条关键论断）；未复现历史事故（当时 B 未完成/报告晚到/被合并/只读第一段，四者未区分）；未启动服务、未访问 8768；未做真实 provider E2E；未验证不同 adapter 的报告时序差异
- 有序待办：
  - [x] 完成机制事实核对（报告粒度 / 队列项生命周期 / 幂等边界，含 `文件:行号`）
  - [x] 交付分档解决方案与推荐
  - [x] MA 核验报告证据（抽查行号与语义）
  - [ ] `DEC-003` 决策后派第 2 档实现任务
  - [ ] 合入 main（仅调查，无代码改动；方案若采纳则另立实现任务）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：不适用（本调查无代码改动）；push：否

### T-033：Pan 核心运行时链路（Worker 生命周期 + 队列/报告投递）真实隔离实例 E2E 验证

- 优先级/依赖：独立验证；端口与 T-031 分工（T-033 = 8766；**8765 不可用**，见 E2E-ENV-001）
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端（纯验证任务；发现缺陷只报告，由 MA 决定是否另立返工项）
- 决策门：无；若发现需要改变产品行为/兼容性/数据语义的缺陷，停止并报告
- 当前阶段：验证执行中（第 1 次接续**未交付报告**，见下；用户已明确选择 A，已向第 2 次接续发送覆盖性执行口径）
- 下一动作：等待 T-033（`ses_c8671119a2dd9bb4`）按方案 A 交付最终报告；其 A/B 排队行为证据回填 T-032
- 阻塞：无（E2E-ENV-002 已给出绕行方案并写入简报）
- 目标：在 8766 隔离实例上验证 Worker 生命周期、持久队列投递、完成报告链路与 zombie/watchdog 行为，补齐多个已合入批次共同记录的“未做真实服务/运行时 E2E”缺口。
- 验证范围：spawn → 执行 → done → idle 回收 → 自动重建与 `cliSessionId` resume；assign 幂等与 send 排队 / 无 worker 入队 / watchdog 拉起；`queue_pending` 跨重启恢复；`report_subscribe` 收到 done/error 报告字段；zombie 报告；A/B 排队可观察性行为取证；`send_force` restart、不存在/已删 session、服务重启恢复等边界。
- 边界：只读验证，不得修改产品代码（允许 worktree 内临时脚本/驱动与 launcher 副本，须在报告列出）；不 commit/合入/push；不碰 8768/8767/8765、QQ 服务、用户 dirty 文件；结束必须停止实例、删除临时 `config.json` 并释放 8766。
- 工作树/分支：`D:\project\pan-worktrees\runtime-queue-report-e2e-20260915`；`audit/runtime-queue-report-e2e-20260915`；基线 `main@52a434b`；MA 核验 clean
- TA/任务：原 TA `ses_1385abb15d4b3b3e`（codex `gpt-5.6-luna`，worker-5，已归档为 `(archive) runtime-queue-report-e2e-20260915`）→ 接续 TA `ses_c8671119a2dd9bb4`（cbc `deepseek-v4.1-flash`，effort `high`，`bypassPermissions`，最近一次补充指令已排队为 `worker-2`，隔离端口 8766）；已 `report_subscribe`；按用户 2026-09-15 口径不重做、用 `session_handoff` 接续
- 交付异常记录（E2E-DELIVERY-001）：第 1 次接续（task `T-033-cont-handoff-20260915`）返回 `done`，但 result 内容是**对话/上下文压缩摘要**（分析 + summary），六项验证一项未执行、无任何证据。MA 判定为交付失败（非产品缺陷），处置：**在同一 session 上原样重派**（不重做任务范围、不换 session），并在简报中明确要求“最后一条消息必须是报告本体”。
- 环境阻塞记录（E2E-ENV-001，已绕开）：原定端口 `8765` 被 PID 7612 占用——另一 worktree `input-attachment-composer-send-20260915` 的 `vite preview`（01:26 启动的残留进程）。MA 核验 `netstat`/`Win32_Process` 后裁定：**不停止他人进程**，改分配端口；该残留进程仍占用 8765，如需回收须用户确认。
- 环境约束记录（E2E-ENV-002，MA 已核验并写入简报）：① `tests/support/isolated_http_server.py:66-67` 的端口白名单只有 `{8767, 8765}`，**8766 会被 `SystemExit` 拒绝** → 绕行方案：复制 launcher 到 worktree 内的临时文件并扩展白名单，**不改动已提交文件**；② worktree 内无 `config.json` 时 worker 默认 `idle_sec=300`/`timeout_sec=300`/`task_timeout_sec=1800`；用户已选择**方案 A**，因此本次 E2E 允许用临时 `config.json` 将 `idle_sec` 缩短到约 20s 以验证回收链路，但报告必须标明这是**非默认配置证据**，结束删除 `config.json` 并确认 worktree 干净，不得把它表述为默认 300s 的时序证据；③ `tests/support/fake_stream_cli.py:27-32` 的 gate scope 硬绑 workdir basename（`real-fifo`/`real-recovery`/`real-manager`）；④ 只有 `running`/`queued` 状态的 worker 被杀才可能产生 zombie 报告（idle 被 kill 不报）。
- 测试口径决策（用户会话，2026-09-15）：选择 A——保留缩短 `idle_sec` 的 T-033 watchdog E2E 路径；进程内 watchdog 定时语义仍由既有回归覆盖，T-033 以非默认临时阈值加速验证真实服务中的回收后果，并单独标注证据边界。
- 提交：无（验证任务，不产生产品代码改动）
- 测试/未验证项：**已取得**——源码审查（入口行号见原报告）、`compileall` + `git diff --check` 通过、进程内 Python 回归 77 passed（`test_worker_watchdog` / `test_worker_global_watchdog` / `test_report_subscription` / `test_queue_restart_hardening`）；**未取得**——真实服务/API/WS、真实 CLI Worker 子进程、`queue_pending` 跨重启恢复、zombie 报告、A/B 排队观察（两轮均未启动实例）。真人开发者验收仍待用户
- 有序待办：
  - [ ] 8766 隔离实例启动与端口/数据根核对（用 launcher 副本）
  - [ ] Worker 生命周期用例
  - [ ] 队列投递与幂等用例
  - [ ] 报告链路与 zombie 用例
  - [ ] A/B 排队行为取证（供 T-032）
  - [ ] 交付报告并由 MA 核验证据
  - [ ] 合入 main（无产品代码改动；如发现缺陷则另立返工项）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：不适用；push：否

### T-035：移动端 Session Details 改为全屏

- 优先级/依赖：用户 2026-09-15 直接需求；与 T-030 同改 `packages/web/src` 但文件不重叠（T-030 在 stores/hooks/WorkerDot，本任务在 `SessionDetailsModal.tsx` 与 `ui/Modal.tsx`）
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-3`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端（需求明确，实现路线由 TA 在既有 `Modal` / `useMediaQuery` 约定内自主选择）
- 决策门：无；若必须改变其他弹窗的既有行为才能实现，在报告中说明并暂停该部分
- 当前阶段：MA 核验通过，已合入本地 `main`；等待开发者验收
- 下一动作：开发者在真机/设计环境验收安全区、触摸滚动和全屏交互；若发现问题再立返工项
- 阻塞：无
- 目标：移动端打开 Session Details 时占满视口全屏（`100dvh`、无圆角/外边距、内容可滚动、标题与关闭按钮不被安全区遮挡）；桌面端保持居中窗口与 `size="lg"` 不变。
- 起点（MA 侦察）：`packages/web/src/components/session/SessionDetailsModal.tsx:171` 使用共享 `<Modal size="lg">`；移动全屏先例见 `InputRow.tsx` 的 `isMobile` + `max-md:h-[100dvh]` / `max-md:rounded-none`，断言写法见 `InputRow.test.tsx:1081-1083`。
- 边界：只改 `packages/web/src/**` 及本任务验证脚本；不改 dist；不碰 8768；8767 / 8765 已分别归 T-031 / T-033；提交本分支后按 `AUTH-001` 合入本地 `main`，不 push。
- 工作树/分支：`D:\project\pan-worktrees\mobile-detail-fullscreen-20260915`；`feature/mobile-detail-fullscreen-20260915`；基线 `main@e98b871`
- TA/任务：`ses_34efb6996f826a27`；`mobile-detail-fullscreen-20260915`；cbc `deepseek-v4.1-flash`；effort `high`；权限 `bypassPermissions`；Worker `worker-6`；已 `report_subscribe`
- 提交：功能提交 `356961fc7ffe84c46322c43bb2706fcf012192bd`；本地合并提交 `82823ad`；worktree clean；未 push
- 测试/未验证项：MA 独立重跑定向 Vitest 2 files / 24 passed；TA 报告 `tsc -b`、ESLint（0 errors / 11 既有 warnings）、`pnpm build` 通过；全量 Vitest 486 passed / 10 baseline failures（已用 stash A/B 核对）；真实 Chromium 390×844、390×480、700×800、1440×900 三用例通过，产物位于 `%TEMP%\pan-e2e-session-details\artifacts\`；未验证真机 safe-area 实际遮挡、触摸惯性与软键盘/地址栏收缩；真人开发者验收仍待用户
- 有序待办：
  - [x] 实现移动端全屏并保持桌面端不变
  - [x] 定向 Vitest 与类型 / lint / build 通过
  - [x] 移动与桌面视口真实浏览器证据
  - [x] 提交并检查 worktree clean
  - [x] 合入 main（MA 核验后按 `AUTH-001`）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：`82823ad`；push：否

### T-037：修复 structured attachment 的跨 Session 隔离绕过

- 优先级/依赖：T-031 发现的 High 服务端边界缺陷；既定 DEC-002 已明确 chip/inline/待发送 structured attachment 不得跨 Session
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-3`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端实现与验证；不改变 DEC-002 语义，保留正文 editor/server-file 链接经过服务端校验后的跨 Session 复用
- 决策门：无；若无法安全区分 structured parts 与正文引用，或必须扩大权限/数据语义，停止并报告
- 当前阶段：实现/验证执行中（TA running）
- 下一动作：等待 `ses_022781914942f1b2` 报告 → MA 核验 diff 与定向回归 → 按 `AUTH-001` 合入本地 main
- 阻塞：无
- 目标：跨 Session 直接提交其他 Session 的 structured `attachmentId` 返回 `attachment_session_mismatch`，不得入队；同 Session structured attachment 继续通过；合法正文 editor/server-file 跨 Session 复用继续通过
- 起点：`packages/web/server.py` 的 `_attachment_id_error()` 已有 owner 校验，但 `_normalize_message_parts()` 传入 `allow_cross_session=True` 并调用 `_import_attachment_reference()`，由 T-031 真实 8767 E2E 复现
- 边界：只改服务端实现、直接相关回归测试和必要文档；不改 dist、不碰 8768、不改 T-033/T-035 worktree；不 push；只提交本分支，MA 验收后按 `AUTH-001` 合入本地 main
- 工作树/分支：`D:\project\pan-worktrees\attachment-session-isolation-20260915`；`fix/attachment-session-isolation-20260915`；基线 `main@e50fd32`
- TA/任务：`ses_022781914942f1b2`；`attachment-session-isolation-20260915`；cbc `deepseek-v4.1-flash`；effort `auto`；权限 `bypassPermissions`；已 `report_subscribe`；task `T-037-implement-attachment-session-isolation-20260915`
- 提交：待 TA 完成
- 测试/未验证项：待 TA 报告；必须分别记录同 Session structured、跨 Session mismatch/no queue、合法正文 editor/server-file 跨 Session 复用，以及相关附件回归；真人开发者验收仍待用户
- 有序待办：
  - [ ] 调查并实现最小服务端修复
  - [ ] 新增/调整跨 Session structured parts 回归
  - [ ] 验证合法正文 editor/server-file 跨 Session 复用不回归
  - [ ] 定向测试、diff check、worktree clean
  - [ ] 交付报告并由 MA 核验
  - [ ] 合入 main（测试通过后按 `AUTH-001`）
  - [ ] 开发者验收
- 合入/push 状态：合入 main：未开始；push：否

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

### T-034：已合入批次的浏览器/UI 侧真实运行证据补全（计划，未派发）

- 优先级/依赖：排在 T-031 / T-033 之后；与二者均为验证类任务，需错开隔离端口
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-1`、`AUTONOMY-1`、`TEST-1`、`TEST-2`
- 执行模式：端到端（纯验证；发现缺陷只报告）
- 决策门：无；发现需要改变产品行为/兼容性的缺陷即停止并报告
- 当前阶段：计划（**未派发**）
- 下一动作：待 T-031 或 T-033 报告到达、或 Codex 五小时额度窗口重置后，派独立验证 TA（`MODEL-1`，独立 worktree，使用空闲的隔离端口）
- 阻塞：并行度与 Codex 五小时额度（当前已有 4 个 TA 在跑、其中 3 个 codex）；**非用户决策或授权阻塞**
- 目标：为已合入但仍记录“未做真实浏览器/移动端 E2E”的批次补运行证据——T-013 浏览器后台恢复（visibility/pageshow/focus + 共享 WS 重连与权威状态刷新）、T-019 Codex Steer 在 worker running 时的可见性与 session-level endpoint、T-015 通知/系统提醒/msgBridge、T-008 / T-018 带行号 Markdown 链接（Windows 盘符 / UNC / `file://`）在 Editor 中打开。
- 验证方式：真实 Chromium（仓库已有 Playwright 基础设施）+ 隔离服务实例；逐用例 通过/失败/未验证，证据含端口、命令、时间与截图/日志路径。
- 有序待办：
  - [ ] 派发独立验证 TA 并记录 worktree / 端口
  - [ ] 完成浏览器侧用例与证据采集
  - [ ] 交付报告并由 MA 核验，结论回填对应已合入批次
  - [ ] 合入 main（无产品代码改动；如发现缺陷另立返工项）
  - [ ] 开发者验收

### T-036：终态广播被阻塞的 enrich/落盘推迟（服务端次序，先调查再决定）

- 优先级/依赖：T-030 的 TA 明确上报的后续项（本次刻意未实现）；排在 T-031 / T-033 / T-035 之后
- 约束策略：`GIT-1`、`GIT-2`、`MODEL-3`、`AUTONOMY-2`、`TEST-1`、`TEST-2`
- 执行模式：分阶段决策门（先调查事件/账本持久化次序与 `enrich_after_result` 的写入面，再决定是否实现）
- 决策门：若调查证明需要改变事件/账本持久化次序或 Session 状态写入时机，先给方案、影响与风险，不直接实现
- 当前阶段：计划（**未派发**）
- 下一动作：待 T-031 / T-033 / T-035 任一收束后派调查 TA（`MODEL-3`）
- 阻塞：并行度上限（当前 4 个 TA 在跑）；**非用户决策阻塞**
- 目标：`packages/core/worker.py` 终态路径当前次序为 `w.status="done"`(≈963) → `adapter.enrich_after_result(s)`（cbc `adapter.py:475` 含 `time.sleep(0.2)`、kimi `adapter.py:455` 为 `0.3`，均在 asyncio 循环上）→ `_flush_history_now`（`session.py:43` 进程级 `_SAVE_LOCK` 落盘）→ **才** `_bcast worker.result`(≈1027) → `w.status="idle"` + 广播(≈1052)。即"清除指示灯的那个事件"被阻塞工作推迟。需评估：能否在不破坏事件/账本持久化次序语义的前提下降低该延迟（先广播后落盘？把 enrich 移出事件循环？分帧？），给出最小改动、风险与验收方式。
- 已知约束：`enrich_after_result` 会写 `Session` 状态，不能简单挪到线程；改次序属语义变更，须先报告。
- 有序待办：
  - [ ] 派调查 TA：事件/账本持久化次序 + `enrich_after_result` 写入面 + 可行的最小改动与风险
  - [ ] MA 核验方案后决定是否实现（必要时建 `DEC-nnn`）
  - [ ] 若实现：定向测试 + 真实隔离实例验证
  - [ ] 合入 main
  - [ ] 开发者验收

新需求必须分配新的 `T-nnn`，不得复用已完成任务 ID。
