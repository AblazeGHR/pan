# Pan 工作流总览

> 当前控制首页。默认规则见 [constraints.md](constraints.md)，复杂任务和验收动作见 [tasks/acceptance-batches.md](tasks/acceptance-batches.md) 及任务详情；历史交付见 [developLog.md](developLog.md)。

## 需要你处理

- `DEC-004`：决定 Job UI 的标签页、创建/编辑控件和时间交互；责任人：用户；状态：待决策；下一事件：用户给出形态后恢复 T-043 UI。
- `DEC-005`：决定 Workspace 标签页、Session 切换和归属编辑范围；责任人：用户；状态：待决策；下一事件：用户给出交互范围后恢复 T-044 UI。
- `T-FRONTEND-CONSOLIDATED-20260922`：选择性前端集成已由 TA 完成、经 MA 验收并以 merge commit `92b363c0ab9d022a8fd89e03dbe1ab1dda251cf1` 合入本地 `main`；尚未 push、尚未真人验收；责任人：用户/开发者；下一事件：完成真人验收并决定是否 push。
- `App Settings Appearance`：用户已明确验收；状态：已验收待清理。实现提交 `7b9f02e`、测试 follow-up `1704036`、最终 reset 断言位置修正 `ef95be3` 均已合入本地 `main` 与 `practical`（当前两者 HEAD 同为 `d3835a756f74b4f0faec20865c0fe61bfea5a470`），主线 `AppSettingsModal.tsx` 已包含 Appearance tab；`main` 比 `origin/main` ahead 26，push 未验证。TA worktree 因缺依赖未能运行定向 Vitest、TypeScript/build；最终断言修正后的 Vitest 也未在 TA worktree 运行。责任人：MA；下一事件：清理该功能的临时任务/分支记录后归档，不得将缺失的 TA 验证记作通过。
- `T-034`：补齐早期功能的真实浏览器、Windows 和 OS 证据；责任人：开发者；状态：待验收；下一事件：按[批次 E](tasks/acceptance-batches.md)逐项填写操作、预期和结果。
- `T-040`：确认普通消息的 taskId 继承与报告配对；责任人：开发者；状态：待验收；下一事件：按任务详情执行任务切换、重启恢复和 report 检查。
- `T-041`：确认终态持久化、result/idle 广播与 usage 最终一致性；责任人：开发者；状态：待验收；下一事件：按任务详情完成真实 provider/CLI 与 HTTP/WS 行为检查。
- `T-042`：确认本地文件链接仍可打开 Editor 且可拖入、发送和跨 Session 复用；责任人：开发者；状态：待验收；下一事件：按任务详情执行原始链接、首次拖入和跨 Session 操作。
- `T-043`：确认 Job 后端持久化、恢复和 Agent/MCP 入口；责任人：开发者；状态：后端待验收，UI 等待 DEC-004；下一事件：先验收后端，再按决策恢复 UI。
- `T-044`：确认 Workspace 持久化、查询和 Session 归属；责任人：开发者；状态：后端待验收，UI 等待 DEC-005；下一事件：先验收后端，再按决策恢复 UI。
- `T-045`：确认定时 Job 群发、恢复、逐目标结果和 MCP；责任人：开发者；状态：待验收；下一事件：执行隔离调度与逐目标结果检查，UI 边界按 DEC-004 处理。
- `T-046`：确认后台 Job 通知的稳定身份、恢复和去重；责任人：开发者；状态：待验收；下一事件：执行 Runner、重启、queue_pending 和前端终态检查。
- `T-047`：确认 Worker 退出或重启后的 queue 投递、清除和恢复；责任人：开发者；状态：待验收；下一事件：执行真实 hand-off、恢复和同类队列状态检查。
- `T-048`：确认移动端 Manage/Session Settings 关闭和桌面外部点击关闭；责任人：开发者；状态：待验收；下一事件：按任务详情执行移动端与桌面行为检查。
- `T-049`：确认完整前端 Vitest 基线及失败归因；责任人：开发者；状态：待验收；下一事件：核对 main 批次结果和后续失败是否仍属环境基线。
- `T-050`：确认 usage 缓存命中、重开、慢刷新和失败刷新显示；责任人：开发者；状态：待验收；下一事件：按任务详情执行 Session Details/TopBar 行为检查。
- `T-051`：确认 A→B→A 草稿与 inline 附件恢复、OS 粘贴和失败恢复；责任人：开发者；状态：待验收；下一事件：按任务详情执行真实浏览器/OS 边界检查。
- `T-053`：确认流式消息、Session 卡片、delta→result→idle 和旧状态回写；责任人：开发者；状态：待验收；下一事件：按任务详情执行真实 Session 切换和慢通知检查。

## 正在执行

- `T-062`：前端性能与状态新鲜度长期优化；责任人：T-062.9 Luna max TA；状态：T-062.2~T-062.7 已合入 main，T-062.8 隔离 HTTP/WS 验收完成，T-062.9 main 原生修复已合入本地 main；隔离浏览器 E2E 由 TA 执行中；下一事件：收到 E2E 证据报告。
- `T-062.9`：practical 当前提交 delta/Steer 状态一致性调查与候选低开销修复；责任人：`ses_d63a00543806ef52` / `ses_5eba00b6ed150122` Luna max TA；状态：main 原生修复已合入本地 main，集成提交 `ad01565`；相关前端/后端定向回归已通过，隔离浏览器 E2E `T-062.9e` 执行中；下一事件：收到真实 HTTP/WS/Chromium 证据。
- `T-UI-queue-badge`：Queue 折叠按钮数量徽标定位修复；责任人：MA；状态：已合入本地 main；下一事件：待前端依赖可用后补跑 Vitest/TypeScript，或在开发者验收中确认视觉结果。
- `T-UI-send-running-optimistic`：发送消息时按前端 runtime worker 状态抑制 running 期间的乐观历史追加；责任人：`ses_68b9e2dfb7bd236c` Luna max TA；状态：T-062.9e 真实隔离 E2E 已确认当前 main 仍有 running queue 乐观行回归，已在 `ad01565` 上派发修复与复测；下一事件：收到前端修复提交及隔离 Chromium E2E 结果。

## 等待与暂停

- `T-029/T-039`：Session queue MCP 与 Worker 主动消费 queue 方案由用户暂停；责任人：用户；恢复条件：用户明确恢复并重新确认范围，恢复前不派 TA、不建 worktree。
- `T-043/T-044` UI：分别等待 `DEC-004/DEC-005`；责任人：用户；恢复条件：对应 UI 形态和交互范围明确，后端验收不受影响。
- 未验证边界：真实 provider/CLI、HTTP/WS、Chromium、移动端和 OS 桌面证据按任务详情保留；责任人：开发者；恢复条件：逐项验收、明确接受未验证项或建立后续任务。

## 最近交付

- 2026-09-23 本地 `main` 一线新增的 UI/流式变更：Session menu dismissal/filter placement（`e10a911`）、相邻 tool/thinking blocks 分组及 Appearance 控制（`1f2656d`、`a899ef3`）、image preview 与下载身份修复（`e6af59f`、`18312e6`、测试 setup `eacc648`）、stream identity/transcript ordering fixes（merge `edada68`）、Windows launcher listener-child 判定（`d3835a7`）。上述代码提交均在当前 `main`；这些提交本身不构成 MA/用户验收证据。stream integrity 与 launcher 的验收状态仍需各自任务证据核实，不在此推断完成。
- Appearance TA 验证边界：TA 报告的功能及测试修改均已提交；其定向 Vitest 因 TA worktree 缺少依赖未运行，TypeScript/build 因类型依赖缺失未通过，最终 reset 断言位置修正后也没有在 TA worktree 重跑。用户本轮已明确验收，因此该功能状态为“已验收待清理”，未验证项仍如实保留。
- `T-060`：Pan skill 验收写法实验已结束；后续按“workflow 与 Pan 隔离”由 practical 的 scope 恢复提交取代，明确不合入 main、不再执行；最终边界见 [developLog.md](developLog.md)。
- `T-059`：`T-049/T-050/T-051/T-053` 已合入本地 main，批次回归已完成；状态：交付完成，开发者验收仍由上方任务入口跟踪。
- `T-062.1`：delta identity、流式渲染重建、InputRow 重渲染和后台 unread 归属的第一步止血修复已合入本地 main；后续长期优化仍在推进。
- `T-062.2`：history 增量游标和 usage enrichment 并发一致性修复已合入本地 main，集成提交为 `0f91fd6f5bf04d4ca3c51d8931669bd0b4f04c4d`；开发者验收和真实运行时验证仍待后续。
- `T-062.3`：Manage/Details metadata 视图、freshness 展示和异步请求竞态保护已合入本地 main，集成提交为 `06eb68ac800a3a6b63a9455d17d78528afbcca76`；开发者验收和真实运行时验证仍待后续。
- `T-062.4`：WebSocket 慢客户端背压隔离、delta 合并和显式 resync 边界已合入本地 main，集成提交为 `ca795ccb602780720f6e78263bc4f3810169be06`；真实服务、浏览器和长期压力仍未验证。
- `T-062.5`：Session summary bounded projection、revision freshness 和无副作用 metadata 加载已合入本地 main，集成提交为 `07d92496d1e1f88c3bd62bdfa9c747b1f73b554a`；开发者验收和真实运行时验证仍待后续。
- `T-062.6a`：Per-Session FIFO persistence writer、低频全局 store lock 和有界持久化诊断已合入本地 main，集成提交为 `e5e28b0`；真实多 agent 长压、provider/CLI 和生产恢复仍未验证。
- `T-062.6b`：队列 receipt/幂等索引有界治理、history 分页/浅加载和长期 Session 读取优化已合入本地 main，集成提交为 `f3a019a`；真实 HTTP/WS、provider/CLI、Chromium 和长压仍未验证。
- `T-062.7`：bounded result cursor、终态 replay/resync、断线 snapshot 和队列恢复边界已合入本地 main，集成提交为 `7416445`；真实 HTTP/WS、provider/CLI、Chromium 和长压仍未验证。
- `T-FRONTEND-COLDLOAD-FE-INTEGRATION`：FE-1/FE-2/FE-4 的原始 TA 提交为 `b324aa1b4c30ebd6a4345ecdc1750c170a9dff5b`；其选择性范围已由 `cba9df0` 的 `44b9ccd` 覆盖，原始分支仍未 merge/push；真实浏览器/服务层边界见整合报告。
- `T-FRONTEND-COLDLOAD-BE3`：history/list 冷读原始独立 commit `5110f28f7d4e34dd895646843ef3c28836cd1a99`，其中选择性变更由 `cba9df0` 的 `94b62e3` 覆盖；独立 BE-3 E2E 证据仍保留，原始提交未 merge/push，生产/8768/provider 仍未验证。
- `T-FRONTEND-CONSOLIDATED-20260922`：在 `feature/frontend-consolidated-20260922` 上完成选择性整合，提交 `cba9df0d143127b10500e237b0468a80f521b18d`，并以 `92b363c0ab9d022a8fd89e03dbe1ab1dda251cf1` 合入本地 `main`；TA 已完成，MA 已验收整合范围。Vitest 73 files/649 tests、TypeScript/build、消息探针 9/9、reconcile 3/3、event-loop 13/13、120k 冷历史 E2E、Chromium CBC strict 8/8 均通过；全库 pytest 仅有既有 Codex quota docstring 失败。未验证真实 provider、生产 8768；Steer receipt 服务端协议仍是后续缺口；未真人验收、未 push。
- `T-062.8`：在隔离端口 8794/8795 完成真实 FastAPI/WS、cursor replay/resync、queue 幂等、服务重启恢复和 4 Session/40 任务合成负载验收，未发现回归；真实 provider、Chromium、OS 和超长压测仍未验证，无代码提交。
- `T-UI-queue-badge`：Queue 数量徽标改为相对 Queue 按钮定位，合入提交为 `e72b097`；Vitest/TypeScript 因 worktree 缺少 `node_modules`、`vite/client` 和 `node` 类型未运行成功。
- 批次 A/B/C/D：用户已确认验收通过；状态：已关闭，不重新要求验收；未覆盖边界仅按原验收单记录。

## 资料入口

- [约束与默认策略](constraints.md) · [验收入口](tasks/acceptance-batches.md) · [决策详情](tasks/decisions.md) · [暂停与后续](tasks/paused-followups.md) · [附件与运行时详情](tasks/attachment-runtime.md) · [早期任务详情](tasks/legacy-merged.md) · [开发归档](developLog.md)
- [T-062 前端长期优化详情](tasks/frontend-optimization-longterm.md)
