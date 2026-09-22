# 附件与运行时任务详情

> 当前控制字段只在 `../overview.md` 的任务卡维护。本文件保存方案、实现、验证、测试边界和历史异常，供验收时按需阅读。

## T-027：输入框、附件与发送链路审查及方案

- 任务概括：审查输入框、附件和发送链路，确定结构化附件、目录边界与发送事务的实现方案。

- 结论：全链路审查确认高优先级问题包括 Send 后 DOM/draft/parts 未清空、发送与会话切换竞态、HTML paste 未强制纯文本、Worker 可能收到 API href、queue 编辑 text/parts 不一致，以及 editor 本地文件链接缺少统一附件拖动 payload。
- 方案：采用 A+C（普通文件 paste/drop、目录第一阶段拒绝、结构化 `AttachmentRef/MessagePart`），兼容旧 Markdown、`@"path"`、history、queue、WebSocket、重试和重启恢复。
- 交付边界：本条是审查与方案总任务；代码由 T-027.1/T-027.2 承接，服务端终态后处理由独立 T-036 处理。
- 验证边界：原审查为只读分析，未把合成 DataTransfer 当作 Windows 文件管理器验收；真实证据由 T-031/T-037 补充。

## T-027.1：输入框、发送事务与粘贴/拖入状态修复

- 任务概括：修复发送清空与失败恢复、Session 竞态、粘贴拖入和附件状态，使消息发送后状态可恢复且边界一致。

- 结论：修复 Send 乐观清空与失败恢复、Session/revision 竞态、DOM/parts/draft 清理、附件 occurrence/resource 状态、拖动重排和键盘删除；网页 HTML 转纯文本，图片/HTML 文件保持原文件上传，目录整批拒绝。
- 交付：功能提交 `33aa52ada9cbe92037b5e44b1a90c3820776299d`；合并 `7a9c7e89cd8e313d6e506b39ba99842465603a8a`；契约补齐 `fadebefcdda7094ba59b321685817921996fe2c2` 已合入 main；未 push。
- 验证：Vitest 4 files/73 tests、TypeScript、ESLint、Prettier、隔离 Chromium E2E 6/6 通过；未做真实 8768 API/服务端集成。
- 边界：与 T-027.2 分工，不应通过修改对方 composer/send 文件解决问题。

## T-027.2：服务端路径投影与 editor 文件链接跨 Session 拖动

- 任务概括：让服务端文件路径安全投影为 Editor/下载链接，并支持合法的跨 Session 文件引用复用而不重复上传。

- 结论：Worker 文本使用服务端实际绝对路径，UI 投影为安全 editor/download href；正文文件链接可跨 Session 拖入并复用服务端文件，不重复上传；保留打开、下载、行号、Windows/UNC/file URI 和旧 Markdown 兼容。
- 交付：功能提交链 `dd01634`、`9f260e2`、`89ffa1d`、`a25091d`、`10b39cb898719461bfd9372da121dd387c344b8e`；main 合并 `99f1774ac4ec7a88366012fe2011e6bf5c36a3e0`；未 push。
- 验证：Vitest 9 files/110 tests、pytest 13、TypeScript、ESLint、build、附件 Chromium 6/6、隔离 API + Chromium 通过；完整 E2E 仍有既有 stream 一像素失败，第三方 provider/CLI 未验证。

## T-030：done 事件与状态指示灯延迟

- 任务概括：修复完成事件和权威快照的门禁问题，让 Session 卡片与 Worker 指示灯在任务完成后收敛到正确状态。

- 根因：修复前 `loadSessions()` 的全局 touch 计数与单 Session 值比较，使最后被触碰的 Session 可能永久跳过权威快照；generation 守卫丢弃 `worker.result` 后没有兜底；`WorkerDot` 缺少 done/queued/restarting 配色。
- 修复：功能提交 `fb42e76`，11 files；main 合并 `7f2667b`；按 session 严格判断本地值、对丢弃终态做防抖权威刷新、补齐状态配色。
- 验证：定向 7 files/102 passed、全量 501 passed/10 既有失败、tsc 通过；Chromium 隔离 E2E 4/4，焦点恢复从 5s 卡住收敛到 121ms。
- 未验证：真实 Pan/provider 耗时、半开连接/重连、多 Session 并发、Firefox/Safari/移动端；默认完整 E2E 受 8765 被占用影响。

## T-031：附件与发送链路真实隔离实例 E2E

- 任务概括：用真实隔离服务和浏览器验证附件上传、发送、队列、历史及失败/目录等负路径，确认用户可观察行为与 Session 隔离。

- 结论：在隔离端口 8767 完成真实 HTTP/WS + Chromium 验证；上传→发送→队列/history→Worker canonical 路径、UI href、跨 Session 正文引用、发送清空/失败恢复、目录拒绝和多数安全负路径通过。
- 缺陷：发现 structured parts 跨 Session 越权（T031-001），已由 T-037 修复并完成独立回归；既有 browser stream one-pixel 失败与本任务无关。
- 验证边界：真实 mobile、第三方 provider/QQ、Finder/桌面剪贴板、provider 崩溃恢复未验证；无产品代码提交，合入 main 不适用。
- 验收：T-027/T-027.1/T-027.2 需要阅读本证据；当前开发者验收统一由 overview 跟踪。

## T-032：MA→TA 多任务排队时报告粒度调查

- 任务概括：厘清多任务排队、Worker 执行和报告回传的身份粒度，避免报告归属错误或把入队当成完成。

- 结论：存在 worker.result、queue_pending report item、delivery unit 三层粒度；连续 report/QQ 项可合并为一条消息。根因是任务身份与报告身份未贯通，以及 MA 按消息条数计数，不是 FIFO 必然丢失 B。
- 已核实事实：报告缺少 `sourceQueueItemId/clientMessageId`；`agent_send` 缺少 `task_id/client_message_id` 且不返回 `queueItemId`；queue API 只显示 queued；`sent_to_cli` 不等于 provider 完成。
- 推荐：原调查曾提出第 2 档结构化身份与队列摘要；用户已决定采用最小 taskId 继承规则，由 T-040 实现。sourceQueueItemId、deliveryUnitId 与完整事件模型不实施；实现任务不复用调查 ID。
- 验证边界：完成机制分析和 MA 抽查，未复现历史事故，未做真实 provider E2E。

## T-033：Worker 生命周期、队列与报告投递真实 E2E

- 任务概括：用真实服务验证 Worker 生命周期、队列恢复、watchdog、kill 和报告投递，找出并交接异常完成路径。

- 结论：隔离端口 8766 真实服务/API/WebSocket/CLI worker E2E 完成；C1/C2/C3/C5/C6 和 done report 通过，队列恢复、幂等、watchdog、zombie 与 A/B 排队行为有证据。
- 发现：C4 running-worker kill 不产生 completion report，形成 D-1，已由 T-038 承接；D-2 为 send 来源报告 `taskId=null`，已由 T-040 通过 active taskId 继承规则处理。
- 环境边界：8765 被外部 PID 占用未触碰；使用临时 `idle_sec=20` 加速 watchdog，不能作为默认 300s 时序证据；结束清理已核对。
- 验证边界：未验证真实 provider 崩溃恢复和默认阈值时序；无产品代码提交，合入 main 不适用。

## T-035：移动端 Session Details 全屏

- 任务概括：让移动端 Session Details 使用完整可视高度并可滚动，同时保持桌面端窗口布局不变。

- 结论：实现移动端 `100dvh` 全屏、无圆角/外边距、内容滚动和安全区布局；桌面端保持居中窗口与 `size="lg"`。
- 交付：功能提交 `356961fc7ffe84c46322c43bb2706fcf012192bd`；main 合并 `82823ad`；未 push。
- 验证：定向 Vitest 2 files/24 passed；TA 的 tsc、ESLint、build、全量 Vitest 和 Chromium 四视口通过；全量有 10 个既有失败。
- 未验证：真机 safe-area 实际遮挡、触摸惯性、软键盘/地址栏收缩。

## T-037：structured attachment 跨 Session 隔离

- 任务概括：阻止跨 Session 直接提交不属于当前 Session 的结构化附件，同时保留合法正文文件引用和同 Session 复用。

- 结论：跨 Session 直接提交其他 Session 的 structured `attachmentId` 返回 `attachment_session_mismatch` 且不入队；同 Session structured attachment 和合法正文 editor/server-file 跨 Session 复用保持通过。
- 交付：功能提交 `4ebe0dfca7ecb0f4857a079fbba0805529a0cc3d`；main 合并 `083a09d`；未 push。
- 验证：Python 10 files/104 passed、1 skipped；真实 8767 HTTP 核心隔离、同 Session upload、server_file/editor 复用、Chromium 拖动与 build 通过。
- 未验证：既有 stream bottom 一像素场景未重做；发送失败、provider 崩溃恢复和开发者验收待处理。

## T-038：主动 kill running worker 的完成报告

- 任务概括：让用户主动终止运行中或排队中的 Worker 产生一次可追踪完成报告，并避免 idle/watchdog 路径重复报告。

- 结论：显式 kill running/queued worker 产生单次 zombie/error completion report；idle kill 不报告；watchdog 和已有 report 不重复。
- 交付：功能提交 `2c0b674152d509f6b0cf04945fc05cd9d05aa5ce`；main 合并 `317e46e`；未 push。
- 验证：两组相关回归共 214 passed、compileall 和 diff check 通过；未运行真实服务/876x、全量 Python、前端/browser E2E。
- 关系：只承接 T-033 D-1；T-033 D-2 已由 T-040 处理。
