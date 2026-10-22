# User Locker panel / Interaction Card 交付候选

- 分支：`feature/user-inbox-interaction-contract-20261023`
- 工作树：`D:/project/pan-worktrees/user-inbox-interaction-contract-20261023`
- 本轮起点：`cab1413d9335d3930501eeea30d64c8e39f9d763`（沿用基于 Inbox/Locker `7446efcb55545be35e76b4845941991202b8cd93` 的隔离后续任务）。
- **精确源码 SHA：`6158cd4fdee33586e23d03bfc0a8f6f28573dbe0`**。此后提交仅含本目录证据，未修改源码/测试/设计。
- TA 完成交付候选；等待 MA 独立验收。没有合入 main/practical、push/release、重启服务或派子任务。

## 实现与兼容

User Inbox 新增独立 `/react/locker` panel 和共享导航入口，左侧真实 Locker，Workspace 过滤及纯前端聚合视图，右侧按时间展示原始 Locker Message、来源名称和 Session ID。原消息进视口后写原 ID 的 read 状态；没有新增聚合容器或消费游标。支持已读/未读切换、分页展示、刷新/切换取消旧请求、错误显示、只读生命周期、answer/choice/submit/reject/delete。复用现有 Markdown、时间、附件安全链接；增加明确来源 Session 上下文，避免附件/本地文件借用当前 Chat。

真实用户 Locker 以 `(recipientId, ownerSessionId)` 隔离，由专用 Card 投递显式建立；普通 QQ/Chat/插件消息不自动建容器。用户是唯一消费者，只有 read/unread。末条 Card 删除后来源容器仍存在，来源 Session 消失时标 unavailable 并保留内容。目录使用既有有效 Workspace 归属；聚合只过滤已授权 recipient 的原始消息。

Card body/summaryBody 固定，answer 与 choice trigger 可选。通知只含 summaryBody 前 200 个 Unicode 字符。新 submit 支持无控件的显式确认，旧 respond 空回应拒绝语义保留。submit/reject 原子竞争一次性 response；相同意图幂等，冲突拒绝，终态定义不可改。用户 reject 保留 Card 并持久生成唯一内部通知意图，定向来源 Agent Session Queue；持久接纳不等 CLI，通知不携带完整 body/answer/附件。

delete 仅允许终态，只删除用户侧原记录，不发通知；最小 tombstone 防创建键复活。待确认的 Trigger/拒收通知保留内部投递记录，按稳定 ID/原 QueueID 核对，unknown 不盲目重投；接纳后剥离待投 Event/摘要。来源生产者的 response/Event 回执独立保留，可在用户删除后核对。逻辑删除不承诺 SQLite 自由页/备份物理擦除。

新增/扩展 HTTP schema 与 API：user-lockers、read、submit、reject、delete、producer receipt；MCP interaction_card 增加 receipt，仍仅限生产者 self/managed 权限，不暴露用户代答能力。设计/API 契约见 [设计文档](../../docs/design/User-Inbox-Interaction-Card-Contract.md) 和 [HTTP 手册](../../docs/skills/pan/references/http-api.md)。

没有改动 QQ / Agent Inbox 的共享消费、订阅、旧 QQ history 或 Job/scheduler/automation 业务。Agent Inbox 无新 GUI，继续由 msgbridge 管理订阅。原 Agent Inbox 工作树仍 clean，HEAD `6a4cd9e6f9b38d69d59e11dbf00bfec1cf003440`。

## 固定源码上的最终验证

| 层次 | 结果 | 证据 |
| --- | --- | --- |
| Python 后端/兼容回归 | **268 passed, 0 failed/errors/skipped**，退出 0，JUnit 34.904 秒 | [backend.xml](backend.xml)、[backend.log](backend.log)、[退出码](backend-result.json) |
| 前端 jsdom / 导航 / 快捷键 / Markdown | **64 passed, 0 failed/errors/skipped**，6 files，退出 0 | [frontend.xml](frontend.xml)、[frontend.log](frontend.log)、[退出码](frontend-result.json) |
| 生产构建 | tsc + Vite + 80 项压缩资源校验通过，退出 0 | [build.log](build.log)、[退出码](build-result.json) |
| 静态 | compileall、Draft202012 check_schema、diff --check、源码无未提交变化，4 项退出 0 | [static.json](static.json) |
| 真实 HTTP / stdio MCP / Chromium | 计入上方 Python 回归；完整 lifespan，桌面与 390×844 viewport，0 page errors | [browser.json](browser.json)、[实例与根目录](interaction-evidence.json)、[清理](cleanup.json) |

Python 命令（工作树根）：

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_user_locker_lifecycle.py tests/test_user_locker_browser.py tests/test_interaction_cards.py tests/test_interaction_card_api.py tests/test_interaction_card_http.py tests/test_agent_inbox.py tests/test_agent_inbox_http.py packages/qq/test_qq_api.py packages/qq/test_channels.py tests/test_data_catalog.py tests/test_session_queue_api.py tests/test_illegal_queue_wake.py tests/test_jobs_api.py tests/test_independent_job_bridge.py -q --basetemp evidence/locker-panel-20261023/final-runtime --junitxml=evidence/locker-panel-20261023/backend.xml
```

前端命令（工作树 `packages/web`）：

```powershell
npm run build
node node_modules/vitest/vitest.mjs run src/views/LockerView.test.tsx src/components/chat/MarkdownRenderer.test.tsx src/components/layout/ViewEntryGroups.test.tsx src/components/layout/ViewEntryRail.test.tsx src/shortcuts/registry.test.ts src/shortcuts/useGlobalShortcuts.test.tsx --reporter=default --reporter=junit --outputFile.junit=D:/project/pan-worktrees/user-inbox-interaction-contract-20261023/evidence/locker-panel-20261023/frontend.xml
```

复用已有 Python 环境和前端 node_modules 目录联接，没有安装/修改共享依赖。更新快捷键夹具的精确内建入口列表，保留完整相等断言（补已有 Actions 和新增 Locker），未放宽断言/skip。

本轮新增 9 个后端用例、独立真实浏览器闭环、7 个 panel 定向用例、1 个 Markdown 来源隔离用例。覆盖来源/read 隔离、真实旧 v1 DB 幂等迁移、附件引用、submit/reject 并发、重复回应冲突、拒收后保留、终态删除/tombstone、来源回执、未确认 Trigger 删除后恢复、Queue 落盘后本地提交前中断/冷恢复、unknown 不重投、部分失败及并发恢复；另覆盖 Workspace 合并/排序、展示分页、只读刷新取服务端回应、网络不明重试复用原 payload、无用户验证器 fail closed。原多订阅/QQ/bot/容量/Queue gate/Job bridge 回归全部通过。

真实浏览器实例端口 **65348**，核验 nonce、PID/父链、命令、cwd、listener 和全部 data 根后使用；合成验证器/Trigger、fakeCLI、空 QQ gateway manifest。MCP 创建、用户提交、Trigger 持久回执、拒收、刷新保留、删除、来源 receipt、原消息标读均通过；拒收通知 outbox **恰 1 条 accepted**。测试后原 PID 退出、listener 关闭。未访问 8767/8768 或真实 QQ/provider/账号/用户 Worker。

截图：[桌面](locker-desktop.png)、[移动 viewport](locker-mobile.png)。仅证明浏览器 viewport，不等同真实手机/OS 交互验收。文件哈希与构建 index 哈希见 [artifact-manifest.json](artifact-manifest.json)。可变临时 profile/raw 运行数据保留在本地忽略目录，不作为候选源码提交。

## 迁移、恢复与尚未验证边界

旧 v1 cards 原地增量加 read 列与来源容器/内部回执表，只对隔离 profile 运行过测试；旧创建签名保持，缺 summaryBody 的旧请求确定性使用 notification.summary/body。没有修改真实用户文件或 JSON 上限截断政策。未宣称生产迁移已执行。

rejection outbox 与现有 Queue 不承诺跨文件事务；claim/原 ID/持久回执用于恢复。未知不重投，缺失回执需要调查；删除不会解除未知意图。待投摘要/Event 仅作为内部投递记录保留，接纳后清除；来源 response/Event 回执保留是生产者结果记录，不是用户消费进度。

**本基线没有生产 UserAuth。** GUI/API 需部署层注入可信 `app.state.user_inbox_principal`；缺失时明确 503，不能借用 Agent 身份代答。本批验证只使用合成身份适配器，没有扩展 OAuth/凭据/真实 provider。真实认证集成、真实 Trigger/Job 等待继续协议、真实附件权限/文件打开、OS 文件操作、海量消息性能和人工验收未验证。页面当前完整读取用户分页后做聚合，每 20 条展示；未宣称跨来源懒归并或无限历史性能。

最终回归保留既有 Pydantic/Starlette/httpx deprecation 警告、scheduler/api.py:259 未 await broadcast 警告（在 independent Job bridge 用例），前端测试存在既有 No routes matched 提示，构建保留路由 chunk 与 >500 kB chunk 提示；不把这些报告为已修复，不扩张到其他 TA 的 Job 业务。

中间执行曾有路径/命令配置错误及严格 TypeScript 夹具错误，已修正后重跑上述最终固定源码命令；没有把中间失败当作最终通过，也没有跳过失败用例。
