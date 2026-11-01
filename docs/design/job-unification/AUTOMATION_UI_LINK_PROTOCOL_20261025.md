# Phase 3 Automation UI 与 Scheduler Link 契约

本文件记录 Phase 3（设计文档 §5、§6、§6.1）的实现边界和冻结选择。它描述的是新 Automation UI 对 Phase 1/2 后端能力的调用方式；旧 Jobs 入口、旧 MCP/HTTP/GUI 路径仍由原实现负责。

## 页面和权限

- `/react/automation` 保留一个页面入口，提供 `Job`、`Trigger`、`Event Register` 三个 ARIA tabs。tabs 支持 query deep link、左右/Home/End 键盘移动、焦点可见；三个面板保持挂载，因此切换 tab 不丢草稿。
- Automation 的写操作都要求用户 Bearer。`/api/automation/job-edits*` 和 `/api/scheduler/event-sources` 不加入旧 MCP allowlist。浏览器登录 token 只保存在内存；过期后保留最后快照和草稿，并停止写操作。
- Job 编辑器显示有序 `steps[]`、稳定 `stepId`、Action、literal/input/event/prior-step 映射、链校验、并发/恢复/通知设置和时间来源摘要。旧的 `actionId + arguments` 在读取时规范化为单步骤，保存时仍可由后端接受。
- Trigger 面板显示每个订阅的 owner Job；managed Trigger 只能从 Job 编辑器维护。Event Register 显示来源 revision、下次触发、时区、schema、订阅引用和影响确认。只允许用户创建 Scheduler 来源；系统/插件事件定义只读。

## 两条 Scheduler flow

1. **已有来源**：Job 编辑器按名称、规则、时区、enabled 和 revision 搜索 Scheduler Event；提交 `mode=existing` 的 link，带 `sourceRevision`。revision 不一致返回 409，草稿保留。
2. **新来源**：先提交同一用户 operation key 的 Job composition，后端用 `creationKey=<operationKey>:<linkId>` 创建来源，再创建 managed Trigger。来源和 Trigger 在全部资源准备好前保持 disabled；只有最后 checkpoint 完成才启用 Job、Trigger 和用户选择启用的全新来源。

两条 flow 都使用 durable `linkOperations`、Job root-scoped deterministic id、source creation receipt 和 `trg_job_<sha256(jobId,linkId)>`。同 key 同请求重试复用对象；同 key 不同请求返回 409；相同 schedule 不自动合并。失败会隔离并 quarantine 该 operation 的新草稿，重试不会留下 enabled half-created object。现有共享来源不会因另一个 Job 失败而被禁用。

## 生命周期与并发

- 禁用 Job 只禁用其 managed Trigger，不禁用 Scheduler 来源。来源禁用只阻止未来 Event；已经 admitted 的 Run 继续完成。
- 删除 Job 删除其自动管理的 Trigger 和 source link，保留 Scheduler 来源。来源只有在没有引用、并通过 revision/impact 检查时才可删除；不会强制级联。
- 编辑来源返回受影响 subscribers 和 `referencesVersion`。新 UI 更新/删除始终带该版本；版本变化返回 409，页面要求用户在 live latest、load server、keep draft 三种选择中显式处理并重新确认影响。
- 同一来源可被多个 Job 订阅，linkId 和 Trigger id 稳定；同一 Job 不可重复订阅同一来源。现有带时间触发的非 manual Job 保持原 trigger/schedule，UI 不静默迁移 owner。
- Job composition 有 8 个 Scheduler links 和 10,000 个 durable operation 记录上限。未发布的新来源不会自动清理；用户可 Resume 或 Keep draft/cancel。恢复在服务启动时 quarantine 未完成 operation，不自动发布。

## 与 Phase 2 Event 契约的连接

Scheduler 仍负责时间计算、missed-trigger、cursor 和恢复。时间到期时先 durable admit 一个 Event，再推进 cursor；Event identity 为 `scheduleId + scheduleRevision + scheduledAt` 的稳定 occurrence key，并生成独立 `eventId`/`emittedAt`。crash 边界是“Event admission commit 成功、cursor commit 尚未成功”：重启用 occurrence key 去重后只保留一条 Event，不重复广播；admission 失败不推进 cursor。所有订阅各自记录 admission/result，失败、disabled、unavailable 互相隔离。

Managed Trigger 的 Run 只表示该订阅已授权并 admitted 到 target Job；后续 Job chain 的完成状态由 Job Run/Step audit 表示。Job 输入和 Event 字段在 admission 时解析并冻结，不能从不可信 payload 伪造 caller authorization。

## 明确未实现

本阶段不切换或删除旧 Jobs 入口，不实现 Automation UI 之外的 Event Register/Automation 后续迁移、旧 MCP wrapper、真实数据迁移、Scheduler 到 Event 的新 UI 注册流程，以及服务端真实供应商/移动 OS 验证。截图使用生产 bundle 加 Playwright virtual origin fixture；它验证真实组件布局、键盘和路由，但不等价于现场浏览器、真实 API、触摸输入或真实第三方事件源。
