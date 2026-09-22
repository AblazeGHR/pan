# 暂停与后续任务详情

> 当前是否暂停、恢复条件和下一动作只以 `../overview.md` 为准。本文件记录范围、原因和已有调查，恢复后先重读本文件再重新计算可执行集合。

## T-026：附件 AI 绝对路径与 UI 下载 API 渲染分层（已由后续任务承接关闭）

- 任务概括：分离 Worker 的真实文件路径与 UI 的安全链接投影，让附件可下载、可打开并支持合法跨 Session 引用。
- 关闭结论：T-026 的实现范围已由 T-027、T-027.1、T-027.2 承接，T-031 提供隔离链路证据，T-037 修复跨 Session structured attachment 越权；批次 B 已由用户确认验收。
- 残余验证：Finder/桌面剪贴板、第三方 provider、provider 崩溃恢复和 UNC 的部分真实 OS/浏览器证据仍未全部覆盖；这些不重新打开 T-026，按 T-034 或独立验证任务记录。
- 归档位置：范围与原始暂停原因保留在本节，具体实现/验证见 `attachment-runtime.md` 的 T-027 系列和 `acceptance-batches.md` 的批次 B。

## T-029：Session queue 查询与修改 MCP

- 任务概括：设计受控查询和修改 Session queue 的 MCP 能力，明确取消、编辑、重排、删除、幂等和权限边界。

- 目标：调查 queue_pending、worker/发送队列等对象的查询与受控修改能力，明确权限隔离、幂等、审计、取消/编辑/重排/删除及报告队列边界。
- 状态：用户要求先加入待办并暂停；未创建新 worktree，未派 TA，未改 MCP 或服务端代码。
- 依赖：恢复后重新核对 T-027 已交付范围；queue 身份与报告模型需与已决定的 taskId 继承规则对齐。

## T-034：已合入批次的浏览器/UI 真实运行证据补全

- 任务概括：用真实隔离浏览器和 OS/Windows 可复核证据补齐早期功能的后台恢复、文件链接、通知、Steer、拖放和路径边界，并逐项处置未验证项。

- 证据：隔离 8792 的 T-013 visibility/pageshow/focus 权威刷新、真实重启后的 WS 重连、T-008/T-018 盘符/相对路径/`file://` 与行号定位通过；构建、编译和 24 项 Python 定向测试通过。
- 未验证：Windows sender 返回 `windows_powershell_unavailable`；桌面 toast、Notification/msgBridge、reminder 到期、Codex running Steer、UNC、OS 文件拖放仍未验证；exact-bottom 1px 基线失败未修复。
- 交付属性：无产品代码改动，合入 main 不适用；8792 已释放，8768 只读核对，未操作受保护服务。

## T-036：终态广播被 enrich/落盘推迟

- 任务概括：调查并重排终态持久化、完成广播和 usage/enrich 的时序，让 done 与 idle 不再被可延迟的后处理阻塞。

- 调查：当前顺序为 `status=done → enrich_after_result → history 落盘 → worker.result 广播 → idle`；cbc/kimi 的 enrich 含同步等待，会阻塞 asyncio loop，使清除指示灯的事件推迟。
- 约束：`enrich_after_result` 会写 Session 状态，不能简单移到线程；无保护地先广播再落盘会改变事件/账本语义。
- 决策：用户已明确采纳该推荐方案（2026-09-17），由 T-041 另立实现；基础 `last_result/history` 持久化后广播现有事件，再按 Session 串行执行可重试 enrich/usage。
- 未验证：真实服务/provider、多 Session 并发、慢 WS、崩溃/重启和默认配置延迟分布；T-041 必须重新验收这些边界，usage 可见性按最终一致处理。

## T-039：Worker 主动消费 queue 信息的 MCP 工具方案

- 任务概括：设计 Worker 主动观察或消费 queue 的 MCP 工具，明确 Session 隔离、FIFO、确认/重试和报告身份规则。

- 目标：先讨论消费对象、触发方式、Session 隔离、FIFO、at-most-once、报告身份和失败恢复，再决定是否实现。
- 范围：工具命名与参数、只读观察与 claim/consume、确认/重试/取消、与 `agent_assign`/`agent_send`/报告投递及已决定 taskId 继承规则的关系、审计和兼容性。
- 状态：用户 2026-09-15 明确要求先加入待商讨方案，不直接执行；确认前不派 TA、不创建 worktree、不修改 MCP/Worker/queue/服务端。
- 后续：方案讨论完成后另立实现任务；若涉及报告/队列身份关联，遵循已决定的最小 taskId 继承规则。
