# T-053 Phase 1 前端性能与状态同步交付报告

## 范围与基线

- Worktree：`D:\project\pan-worktrees\t053-frontend-performance-luna-20260919`
- 分支：`feature/t053-frontend-performance-luna-20260919`
- 基线：T-051 `e7600b59166a7a83e7382657dbf0165b64455877`
- 端口：真实浏览器验证使用隔离 `8798`；未操作 `8768`、`D:\project\Pan` 或 `D:\project\Pan-main`。
- 604eeef 未 cherry-pick；只移植了经过审查的 memo/stable plugin/group memo 方向，并修正 unread Set 原地变异风险。

## 交付内容

### A/B：队列乐观反馈与渲染路径

- 服务端 enqueue 成功后立即将返回的 `queueItemId`、inline `parts` 写入当前 Session；`queue.item_delivered` 按 queue id 幂等收敛，跨 Session 不误渲染。
- 失败路径继续由 InputRow 恢复 T-051 草稿与附件；明确清空、发送成功和非文件目录/URL 纯文本语义保持不变。
- 忙 worker 的队列反馈单独显示“排队中”，不把 queued 状态伪装为 running。
- ChatMessages 分组、MarkdownRenderer plugin 数组、MessageBubble/ThinkingBlock/ToolGroup 做稳定化与 memo；`sessionUnread` 改为复制 Set 后更新。
- adapter config 增加已加载/飞行中守卫；queue 刷新由 WS 事件合并到下一 tick，保留 revision 防旧快照覆盖。

### C：全局 WebSocket 生命周期

- `useWebSocket` 从 ChatView 提升到 Layout，只挂载一次；ChatView 不再重复挂载。
- dashboard `/ws` 对 JSON ping 返回 pong；dashboard 慢客户端 eviction 主动 close `1013`，`/ws/agent` 路径保持独立。
- ws client 增加入站静默看门狗，重连复用现有退避并以 CONNECTING 状态防止 focus/visibility 风暴。

### D：Session 对账与 DONE 保留

- `session.renamed/updated` 的安全 payload 立即应用；一次性 event patch 门禁避免防抖旧快照回滚名称、预览和计数。
- `loadSessions` 对服务端字段不变的 Session 复用对象引用；本地终态更新保护 `lastMessage/historyTotal`，快照失败不造成长期欠计数。
- 本地 `[DONE]` system 消息与下一轮 server history 合并，避免被抹掉；worker.result 按 taskSeq 防重复。

### E：通知去阻塞

- `_persist_terminal_state` 仍先于终态广播。
- 桌面通知改为 `asyncio.to_thread` 后台 best-effort；worker.result/status 不等待 PowerShell、固定 Start-Sleep 或通知失败。

## 验证证据

### 定向测试

- Frontend：12 个相关 Vitest 文件，`173 passed / 0 failed`。
- Backend：`tests/test_backend_perf_opt.py tests/test_websocket_user_inject.py tests/test_notifications_reminders.py tests/test_terminal_broadcast.py`，`30 passed / 0 failed`。
- 新增/强化覆盖：重复 queue delivery、跨 Session、inline parts、乐观失败恢复、Session object reuse、旧 snapshot、DONE 合并、pong、1013 close、慢通知。

### 静态与构建

- `pnpm exec tsc -b`：通过。
- `pnpm run build`：通过；Vite 仅报告既有大 chunk warning。
- 改动前端文件 ESLint：`0 errors`；`MessageBubble.tsx` 保留 2 条既有 Fast Refresh warning。
- `python -m compileall -q packages/core packages/web/server.py`：通过。
- `git diff --check`：通过；Git 仅报告工作树 LF/CRLF 转换提示。

### 完整套件对照

- 当前完整 Vitest：`61 passed files / 2 failed files; 519 passed / 10 failed`。
- 失败集中在未改动的 `components/ui/Toast.test.tsx`（6，`document is not defined`）和 `components/session/NewSessionModal.test.tsx`（4，既有目录/名称时序断言）；改动相关文件的定向套件全绿。该环境未在改动前重新跑完整基线，故将其作为未改基线失败单独报告，不宣称全套件全绿。

### 隔离真实 Chromium E2E

命令：`PAN_E2E_BASE_URL=http://127.0.0.1:8798 pnpm exec node e2e/t053-phase1.e2e.mjs`，使用当前 production build、真实 FastAPI `/ws`、真实 REST；未使用 8768。

结果：`4/4 passed`。

1. busy worker 场景的乐观用户气泡立即出现，排队反馈链路注入成功。
2. editor 与 manage 路由各完成真实导航，并通过真实 dashboard WS 注入事件。
3. `[DONE] Task completed` 在下一轮 focus/history reconciliation 后仍保留，且本次运行只出现一次。
4. 静默 OPEN WebSocket 在加速测试时钟下自愈，观察到 `sockets=7`，证明看门狗触发重连。

## 未验证项与边界

- 未启用 CBC `--include-partial-messages`；Phase 2 仍按计划另行处理。
- 未新增 `session.patch/summaryRevision` 协议，未做 durable queue 多次持久化写合并。
- E2E 使用隔离 launcher 的 disposable session 与事件注入；未将外部真实 provider 调用作为本阶段通过条件。
- 完整 Vitest 的 10 个失败属于未改测试文件/环境基线问题，已与改动相关定向证据分开列出。
