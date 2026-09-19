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

### 追加 UI：Session 模型设置乐观更新

- `sessionStore.patchSessionSettings` 为每个 Session 维护 mutation sequence、乐观字段、真实回滚基线和服务端收敛值；PATCH 尚未返回时当前模型、effort、设置控件和 Session 卡片立即更新。
- model 变更按 `modelEfforts` 同步携带不兼容 effort 的隐式清空；失败或网络异常恢复完整字段并显示错误提示。
- A→B 快速选择、Session 切换、关闭 Popover 后的旧响应均受 per-Session sequence/request guard 保护；旧成功/失败响应不能覆盖较新的乐观值。
- `session.updated` 与 `loadSessions` 的旧快照在 pending 或请求期间完成时不会回退本地较新值；SettingsPopover 和 InputRow 的模型入口共用同一状态路径。

## 验证证据

### 定向测试

- Frontend：12 个相关 Vitest 文件，`173 passed / 0 failed`。
- Backend：`tests/test_backend_perf_opt.py tests/test_websocket_user_inject.py tests/test_notifications_reminders.py tests/test_terminal_broadcast.py`，`30 passed / 0 failed`。
- 新增/强化覆盖：重复 queue delivery、跨 Session、inline parts、乐观失败恢复、Session object reuse、旧 snapshot、DONE 合并、pong、1013 close、慢通知。
- 追加 UI 设置 Vitest：`SettingsPopover.optimistic.test.tsx`，`7 passed / 0 failed`，覆盖 deferred PATCH 立即可见、服务端成功收敛、失败回滚、model/effort 联动、A→B 乱序、Session 切换隔离、pending 快照保护。

### 静态与构建

- `pnpm exec tsc -b`：通过。
- `pnpm run build`：通过；Vite 仅报告既有大 chunk warning。
- 全部改动前端文件 ESLint：`0 errors`；保留 `MessageBubble.tsx` 的 2 条既有 Fast Refresh warning，以及 `SettingsPopover.tsx` 的 4 条既有 React Hook 依赖 warning。
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

## GLM 5.3 Flash 补充调查审核

这份补充报告作为外部调查输入审核，不将调查脚本或候选 commit 的结论自动视为本 worktree 的验证结果。调查使用的 8794 已清理，8768 未触碰；604eeef 仍未 cherry-pick。

### 已采纳的事实与当前 Phase 1 关系

- 8794 上真实 Chromium 观察到未选中 Session 在 `queue.item_delivered` 后约 147–152 ms 更新卡片，这是正常广播链路的外部证据，与本阶段现有 Sidebar/store/memo 修复相互印证；本次没有在 8798 重跑该精确延迟 probe，因此不把它记为本 worktree 新增的性能数字。
- 当前 Phase 1 已覆盖并应保持的交集是：idle/running 防旧快照回写、`[DONE]` 合并保留、全局 WS 静默看门狗、queue refresh 合并/去重，以及 generation/workerId 丢弃后的刷新兜底。正常路径的未选中 Session 更新已有 `appendDeliveredMessages`、`applyResultToSession`、`throttledLastMessageUpdate` 覆盖；卡片点击 history 拉取是补偿路径，不作为正常广播链路的替代品。
- 当前 worker 代码与定向测试已有 `queue_pending`、dead-process、pendingSpawn 和 watchdog recovery hook；因此补充报告中“退出后可能滞留 pending”的两个 probe 不能直接证明本提交引入回归，但说明真实运行时的 kill/idle/崩溃闭环仍需独立审计。Phase 1 不再重复发明第二套 recovery 协议。
- `/ws` 浏览器通道无 replay，而 `_replay_agent_results` 只属于 `/ws/agent`，与本阶段新增的静默重连看门狗是两个层次：看门狗能恢复连接，但不能证明断线窗口内丢失的 `worker.result` 会被补回。

### 后置或另立任务

- S1：`loadSessions` 失败后的受控重试/重连对账。目前失败分支仍是保留本地状态并记录 warning；Phase 1 的事件刷新、周期对账和点击补偿不等同于失败后的自动重试。后续应增加退避、单飞和最终对账测试，避免与现有 queue refresh 去重互相触发风暴。
- S2：worker 退出后的 recovery runtime audit。需要用真实 kill/idle/crash 场景确认仍可投递的 `queue_pending` 在约定窗口内由唯一 recovery 消费者接管，并验证不重复投递；现有单测/代码 hook 作为基础，不把 provider hang 或调查 probe 当作闭环通过证据。
- S3：`_session_summary` 的 summary projection。当前实现取 `history[-1]`，尚未在本阶段改变其 thinking/tool 过滤语义；应单独定义 user/assistant preview 规则并补 backend、snapshot 回退测试，防止 300 ms 对账把 assistant preview 改回 thinking/tool 文本。
- S4：浏览器 `/ws` replay 或等价重连补偿。需要先定义 seq、幂等键、重连边界和与现有 `queueItemId`/taskSeq 的关系，再实现协议及断线错过 result 的真实闭环；本阶段不新增 `session.patch` 或 `summaryRevision`，也不声称 Case B 已通过。

补充报告中的 cbc/hy3 与 glm-5.3-flash provider hand-off 超过 120 秒无 stdout/result 属于本轮实验干扰，不能归因于 Pan UI，也不能用来宣称“恢复后 ≤2 秒收敛”。同理，本阶段真实 Chromium 已验证的是隔离事件注入下的队列、editor/manage、DONE 和静默 WS 自愈 4/4；“result 后 800 ms 不回退”与“provider 恢复后 ≤2 s 收敛”尚未形成独立的真实 provider E2E 证据。

## 未验证项与边界

- 未启用 CBC `--include-partial-messages`；Phase 2 仍按计划另行处理。
- 未新增 `session.patch/summaryRevision` 协议，未做 durable queue 多次持久化写合并。
- E2E 使用隔离 launcher 的 disposable session 与事件注入；未将外部真实 provider 调用作为本阶段通过条件。
- 模型设置的 deferred PATCH、乱序响应和失败回滚由 Vitest 直接证明；本次真实 Chromium 4/4 保持 Phase 1 的队列、editor/manage、DONE 与静默 WS 覆盖，未将浏览器级 deferred PATCH 作为已验证项。
- 追加 UI 定向联测最新组合为 6 个文件、116 passed / 0 failed；其中包含 SettingsPopover 乐观设置、Session snapshot/queue delivery 和 WS 生命周期回归。GLM 补充报告建议的 S1–S4 尚未被计入 Phase 1 的“已完成”项。
- 完整 Vitest 的 10 个失败属于未改测试文件/环境基线问题，已与改动相关定向证据分开列出。
