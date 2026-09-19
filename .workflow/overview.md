# Pan 工作流总览

> 当前工作流真源。约束策略见 [constraints.md](constraints.md)，验收单见 [tasks/acceptance-batches.md](tasks/acceptance-batches.md)，历史归档见 [developLog.md](developLog.md)。本文件只维护当前控制状态；详细报告、原始测试输出和 Session 历史不复制到这里。
>
> 本次核对日期：2026-09-19。事实来源包括 `D:\project\Pan-main` 的用户 dirty 文档、Git worktree/祖先关系、Pan `session_list/session_get` 和 TA 报告。本次只在 `docs/overview-maintenance-20260919` worktree 修改文档；不写 `Pan-main`，不操作 `Pan`、8768、Session、worktree 或 branch。

## 工作流控制

### 当前概况

- 整体状态：MA 正在推进 T-053 Phase 1 和本次 T-054 文档整理；批次 A/B/C/D 已由用户确认验收；批次 E 和近期交付仍有开发者验收入口。
- Git 基线：canonical `main` 当前为 `bd4ec14ae8dde87b6d8158faed6e5ec6662aa7f3`（`docs: record T-050 usage cache repair`，未 push）；本次文档提交尚未合入 canonical main。
- 当前焦点：T-053 Luna high TA 的前端性能/状态同步 Phase 1；同时完成 T-054 overview 清理。
- 可执行：T-053、T-054。
- 仅待整合：T-049、T-050、T-051 的 TA 提交未进入 main；本次不代替 MA 做代码合入。

### 需要用户处理

- 待决策：`DEC-004` Job UI 形态与时间编辑入口；`DEC-005` Workspace 标签页 UI 形态与交互范围。
- 待开发者验收：`T-034`、`T-040`、`T-041`、`T-042`、`T-043`、`T-044`、`T-045`、`T-046`、`T-047`、`T-048`，以及 T-049/T-050/T-051 合入后的验收。
- 待授权：无新增授权请求；本次文档工作不包含 push、合入、服务操作或 8768 操作。

### MA 正在推进

- `T-053`：Luna high TA `ses_19f379fb82fb64b9` 仍为 `running`，`activeTaskId=T-053-frontend-performance-phase1-20260919`；下一动作是等待 Phase 1 提交与报告，再核验测试和隔离 Chromium 证据。
- `T-054`：在当前文档 worktree 重写本 overview，清理已验收批次和过时 Session/执行描述；下一动作是轻量检查、复核 diff、提交文档 commit。

### 等待与暂停

- 整合等待：`T-049/T-050/T-051` → MA 将各自提交带入 main → 在 main 复跑必要检查并安排开发者验收。
- 开发者验收等待：`T-034/T-040/T-041/T-042/T-045/T-046/T-047/T-048` → 开发者完成对应真实行为/运行证据验收；`T-043/T-044` 的后端验收可独立进行。
- 决策阻塞：`T-043` UI 阶段 → `DEC-004`；`T-044` UI 阶段 → `DEC-005`。后端、Agent/MCP 和持久化交付不因 UI 决策冻结。
- 用户暂停：`T-026`、`T-029`、`T-039` → 用户明确恢复并确认范围后再继续；当前不派 TA、不创建 worktree。
- 外部阻塞：无。真实 provider/CLI、HTTP/WS、Chromium、移动端和 OS 桌面边界按各任务卡标为未验证，不写成已通过。

### 资料入口

- 验收入口：[tasks/acceptance-batches.md](tasks/acceptance-batches.md)
- 约束与任务字段：[constraints.md](constraints.md)
- 归档主键与历史：[developLog.md](developLog.md)
- T-053：Session `ses_19f379fb82fb64b9`；worktree `D:\project\pan-worktrees\t053-frontend-performance-luna-20260919`
- T-054：Session `ses_96e2aaa955eef179`；当前 worktree `D:\project\pan-worktrees\overview-maintenance-20260919`
- 唤醒条件：T-053 报告、T-049/T-050/T-051 整合结果、开发者验收、用户决策或外部验证条件变化。

## 已决策/授权

### AUTH-001：测试通过后直接合入本地 main

- 状态：已授权
- 影响范围：已完成测试且无新增决策门的本地代码整合；不包含 push、发布、8768 或 `D:\project\Pan` 操作。
- 未决定时：只能保留提交并等待 MA/用户明确整合。
- 解除条件：对应任务的测试、提交和范围核对完成；本次 T-054 不执行代码整合。
- 来源：用户工作流授权，2026-09-12；当前任务边界另有更具体限制时以后者为准。

### DEC-001：普通文件与结构化附件方案

- 状态：已决定
- 影响范围：普通文件 paste/drop、目录第一阶段拒绝、session-scoped `AttachmentRef/MessagePart` 及兼容边界。
- 未决定时：附件实现不得进入受影响的不可逆扩展。
- 解除条件：已由 T-025/T-027 组实现并在批次 B 验收；不再阻塞当前任务。
- 来源：用户 2026-09-13 确认 A+C。

### DEC-002：跨 Session 附件与发送事务

- 状态：已决定
- 影响范围：待发送 chip/inline 附件隔离、正文 editor/server-file 引用复用、Send 清空与失败恢复。
- 未决定时：不得放宽跨 Session structured attachment 权限或改变失败恢复语义。
- 解除条件：T-027/T-027.1/T-027.2/T-031/T-037 已纳入批次 B 验收。
- 来源：用户会话，2026-09-15。

### DEC-003：报告与任务身份的最小继承规则

- 状态：已决定
- 影响范围：普通 `agent_send` 继承目标 TA 当前 `taskId`；不引入 `sourceQueueItemId`、`deliveryUnitId` 或完整事件模型。
- 未决定时：T-040 不得按历史消息猜测报告归属。
- 解除条件：T-040 已实现并合入；开发者验收仍由 T-040 任务卡跟踪。
- 来源：用户 2026-09-17 明确“按照这个原则推进”。

### DEC-006：时间 Job 的创建者、接收者与 Agent 入口

- 状态：已决定
- 影响范围：Job Registry、一次性/周期性 Session 消息、定时群发、Agent/MCP 创建与取消；不决定 Job UI 外观。
- 未决定时：后端可按既定语义推进，UI 仍不得假设具体布局。
- 解除条件：后端交付已完成；UI 外观继续受 DEC-004 约束。
- 来源：用户会话，2026-09-18。

## 待决策与待授权

### DEC-004：Job UI 形态与时间编辑入口

- 状态：待决策
- 影响范围：T-043 的 App Settings Job 标签页、Create Job 窗口、调度时间显示和调整交互。
- 未决定时：T-043 后端/Agent/MCP 可验收；Job UI 实现和 UI 验收暂停。
- 解除条件：用户明确标签页布局、创建/编辑控件和时间编辑交互。
- 来源：用户会话，2026-09-18。

### DEC-005：Workspace 标签页 UI 形态与交互范围

- 状态：待决策
- 影响范围：T-044 的 Workspace 标签页、Session 展示/切换、归属编辑 UI。
- 未决定时：T-044 后端可验收；UI 实现和 UI 验收暂停。
- 解除条件：用户明确标签页形态、布局、切换和归属编辑范围。
- 来源：用户会话，2026-09-18。

## 正在进行的任务/改动

### T-054：overview 与已验收内容清理

- 任务类型：无代码任务
- 阶段/状态：整理 / 执行中
- 合入 main：未合入（当前只提交本 docs worktree；不写 canonical main）
- 开发者验收：待本报告和文档 diff 核对
- 下一动作：完成 Markdown/link/重复标题轻量检查，提交当前分支文档 commit。
- 解除条件：overview 只保留当前控制状态，批次 A/B/C/D 已压缩为带日期和证据入口的索引，且本 worktree clean。
- 阻塞/未验证项：无产品测试；不验证或操作 8768。
- TA/提交/证据：`ses_96e2aaa955eef179`；worktree `D:\project\pan-worktrees\overview-maintenance-20260919`；提交待创建；本文件与 canonical dirty diff 为输入证据。

### T-053：前端性能与状态同步综合优化

- 任务类型：代码改动
- 阶段/状态：实现 / 执行中（Phase 1）
- 合入 main：未开始；当前 worktree HEAD `e7600b59166a7a83e7382657dbf0165b64455877`，不是 main 的祖先新增提交；有未提交产品代码改动
- 开发者验收：未开始；须在提交、定向/完整测试和隔离 Chromium E2E 后进行
- 下一动作：Luna high TA 完成 Phase 1，提交并报告；MA 审核后再决定 Phase 2
- 解除条件：Phase 1 提交、测试结果、隔离 Chromium E2E 和未验证项报告齐全
- 阻塞/未验证项：真实 8798/Chromium/provider E2E 尚未由本次核对确认；Phase 2 的 CBC 增量事件批处理、`session.patch/summaryRevision`、durable queue 重构不在当前 Phase 1
- TA/提交/证据：`ses_19f379fb82fb64b9`，active task `T-053-frontend-performance-phase1-20260919`；worktree `D:\project\pan-worktrees\t053-frontend-performance-luna-20260919`；分支 `feature/t053-frontend-performance-luna-20260919`；基线 `e7600b59166a7a83e7382657dbf0165b64455877`；当前 `workerStatus=running`，`lastResult=null`。
- 范围依据：T-052 两路报告已收齐并转为本任务输入；GLM 候选提交 `604eeef7230425602691a3eebe72a8966f9471f8` 仅作参考，未合入。

## 已完成调查与近期交付

### T-052：前端卡顿与消息发送延迟调查（已转 T-053）

- 阶段/状态：调查完成 / 已转实现；不再作为当前执行任务。
- 结论入口：GLM Session `ses_a78280bba0dfe1d7` 报告与候选提交 `604eeef7230425602691a3eebe72a8966f9471f8`；DeepSeek Session `ses_673105b23863e7b7` 主报告/follow-up；两路证据均由 T-053 复核使用。
- 主要转交结论：忙时无乐观渲染/FIFO 会放大用户气泡延迟；WS 消费层、慢客户端 close、ping/pong/静默连接和 Session 卡片 snapshot 收敛是 Phase 1 优先项；渲染 memo 与请求去重方案作为候选，不等于最终修复。
- 状态处理：DeepSeek Session live snapshot 为 `offline`；GLM Session live snapshot 曾显示 `running`，但其报告与调查任务已收口，因此不将其列入当前执行，不删除该 Session 或报告事实。

## 已完成但尚未合入 main 的改动

### T-051：输入框草稿与 inline 附件按 Session 保存

- 任务类型：代码改动
- 阶段/状态：实现完成 / 待整合
- 合入 main：未合入；提交 `e7600b59166a7a83e7382657dbf0165b64455877` 不在 canonical main 祖先链
- 开发者验收：待合入后；TA 自动化通过不替代真人/Chromium 验收
- 下一动作：MA 核对范围并整合到 main，随后安排草稿/附件隔离与真实 UI 验收
- 解除条件：提交进入 main，合入后关键检查通过，开发者完成验收
- 阻塞/未验证项：真实 Chromium/OS 桌面复制粘贴未运行；完整页面刷新不恢复 inline `File` 对象/编辑器结构
- TA/提交/证据：`ses_4a320a7614747b00` / `T-051-draft-attachments-20260918`；worktree `D:\project\pan-worktrees\t051-draft-attachments-20260918`，分支 `feature/t051-draft-attachments-20260918`，clean；定向 Vitest 75/75、tsc、build、ESLint、diff check、pre-commit frontend check 通过；完整 Vitest 513/523，10 项为既有基线失败。

### T-050：Usage 缓存命中后仍长时间加载

- 任务类型：代码改动
- 阶段/状态：实现完成 / 待整合
- 合入 main：未合入；提交 `3d0d9e0377a3c7a7f6bac3b1462214a1a3382b0b` 不在 canonical main 祖先链
- 开发者验收：待合入后
- 下一动作：MA 将提交整合到 main，复跑关键前后端检查，再验收 Session Details 重开和慢/失败 quota refresh
- 解除条件：提交进入 main，关键检查复跑通过，开发者完成验收
- 阻塞/未验证项：真实 HTTP/WS、WHAM/provider、CLI/provider、Chromium E2E 未运行；历史 quota docstring 断言失败不属于本任务
- TA/提交/证据：`ses_c28ff8194c88f3dc` / `T-050-usage-cache-fast-path-20260918`；worktree `D:\project\pan-worktrees\session-usage-cache-20260918`，分支 `feature/session-usage-cache-20260918`，clean；Session Details/TopBar 27、后端 usage/quota 10、扩展 quota/cache/store 29 项通过，build、ESLint、compileall、diff check 通过。

### T-049：完整前端 Vitest 失败检修与归因

- 任务类型：代码改动（测试维护）
- 阶段/状态：实现完成 / 待整合
- 合入 main：未合入；提交 `374039bee54d986a2d117b12fe1824e6f6ebc598` 不在 canonical main 祖先链
- 开发者验收：待合入后复跑
- 下一动作：MA 将测试维护提交整合到 main，复跑 Vitest/build 并确认基线归因
- 解除条件：提交进入 main 且 main 上完整 Vitest/build 复跑确认
- 阻塞/未验证项：未发现产品回归；本任务不新增产品行为验收
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-049-frontend-test-baseline-repair-20260918`；worktree `D:\project\pan-worktrees\t049-frontend-test-repair-20260918`，分支 `feature/t049-frontend-test-repair-20260918`，clean；完整 Vitest 61 files/519 tests 通过，SessionList 7/7、build、pre-commit、diff check 通过。

## 已合入 main、待开发者验收

> 下列任务的实现提交已通过 Git 祖先关系核对；“已合入”不等于开发者验收。批次 E 和真实运行边界见 [验收单](tasks/acceptance-batches.md)。

### T-048：移动端 Manage 关闭与 Session 设置点击外关闭

- 任务类型：代码改动；阶段/状态：验收 / 待开发者验收
- 合入 main：是，`fc2b00146038a0631eea1567aff370497efd98a0`
- 开发者验收：待确认；下一动作：验收移动端 Manage 关闭后 Sidebar 保持弹出、Settings 外部点击关闭
- 解除条件：开发者完成验收
- 阻塞/未验证项：真实 Chromium 移动手势/设备 viewport 未验证；定向 31 tests、tsc/build、Prettier、diff check 通过，完整套件的 11 项既有失败由 T-049 处理
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-048-ui-manage-settings-20260918`；实现提交 `7874ee32b55e584678c731fddf269690ab0cff4d`；worktree `D:\project\pan-worktrees\manage-settings-mobile-20260918` clean。

### T-034：已合入批次的浏览器/UI 真实运行证据补全

- 任务类型：无代码任务；阶段/状态：验证 / 待开发者验收
- 合入 main：不适用
- 开发者验收：待确认；下一动作：查看 8792 证据并逐项接受或另立未验证项
- 解除条件：开发者完成该验收
- 阻塞/未验证项：Windows sender、桌面 toast/Notification/msgBridge、reminder 到期、Codex running Steer、UNC、OS 文件拖放和 exact-bottom 仍需单独处理；8792 已释放，8768 未操作
- TA/提交/证据：worktree `D:\project\pan-worktrees\browser-evidence-followup-20260915`，分支 `audit/browser-evidence-followup-20260915`，HEAD `7f7bf742636c6dbc4ea445936bb0407d4240afee`；详见 [批次 E 验收单](tasks/acceptance-batches.md)。

### T-040：普通消息继承当前任务 taskId

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`22254eec284ae1418095e4bfb517622edd05a3d1`
- 开发者验收：待确认；下一动作：验收 taskId 继承、任务切换、重启恢复和 report 配对
- 解除条件：开发者完成验收
- 阻塞/未验证项：真实 Pan HTTP/WS、provider/CLI、Chromium、前端 TypeScript 未验证；定向/回归、compileall 和 skill 检查已通过
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-040-impl-01`；worktree `D:\project\pan-worktrees\task-id-inheritance-20260917`，分支 `feature/task-id-inheritance-20260917`。

### T-041：基础终态先持久化广播，enrich/usage 异步串行后处理

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`b466a76d714fcd68852d3b466de07f71a180aa65`
- 开发者验收：待确认；下一动作：验收真实 provider/CLI 的终态时序、usage 最终一致性和 HTTP/WS/Chromium 表现
- 解除条件：开发者完成验收
- 阻塞/未验证项：真实 HTTP/WS、provider/CLI、Chromium、cbc/kimi usage 延迟和 Worker 崩溃恢复未验证；相关回归 15 passed；QQ pytest 受可选 nonebot 缺失影响
- TA/提交/证据：`ses_c28ff8194c88f3dc` / `T-041`；worktree `D:\project\pan-worktrees\terminal-broadcast-impl-20260917`，分支 `feature/terminal-broadcast-20260917`。

### T-042：本地文件链接拖入附件且保留 Editor 打开

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`c5e0a19beac448f76b5415714446d794b68fec6a`
- 开发者验收：待确认；下一动作：验收原始链接点击、首次拖入、发送和跨 Session 复用
- 解除条件：开发者完成验收
- 阻塞/未验证项：不得削弱 opaque 引用和服务端校验；真实用户验收未完成
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-042-local-link-drag-20260918`；worktree `D:\project\pan-worktrees\local-link-drag-20260918`，分支 `feature/local-link-drag-20260918`。

### T-043：Job 后端与 Job UI 后续阶段

- 任务类型：代码改动；阶段/状态：整合 / 后端待开发者验收，UI 决策阻塞
- 合入 main：是，`62ffffb63b4a6ae4c42e9d88f44f4f5a325ca473`
- 开发者验收：后端待确认；下一动作：验收 Job Registry/恢复/Agent-MCP 后，再由 `DEC-004` 决定 UI
- 解除条件：后端开发者验收 + DEC-004
- 阻塞/未验证项：UI 标签页、Create Job、时间编辑和 UI kill 等待 DEC-004；真实 provider 到期发送未验证
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-043-backend-jobs-20260918`；worktree `D:\project\pan-worktrees\jobs-backend-20260918`，分支 `feature/jobs-backend-20260918`。

### T-044：Workspace 后端与 Session 分组

- 任务类型：代码改动；阶段/状态：整合 / 后端待开发者验收，UI 决策阻塞
- 合入 main：是，`c97b95768c2d273cd7ef3db0af6ed4525ab7e681`
- 开发者验收：后端待确认；下一动作：验收 Workspace 持久化/查询/管理，再由 `DEC-005` 决定标签页 UI
- 解除条件：后端开发者验收 + DEC-005
- 阻塞/未验证项：标签页、布局、切换和归属编辑 UI 等待 DEC-005；后端模型和接口不阻塞
- TA/提交/证据：`ses_2c93476e30d3a676` / `T-044-backend-workspaces-20260918`；worktree `D:\project\pan-worktrees\workspaces-backend-20260918`，分支 `feature/workspaces-backend-20260918`。

### T-045：定时群发 Job 后端与 Agent/MCP

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`6250c8a63082b840a01cf54b78dc383b9f770eda`
- 开发者验收：待确认；下一动作：验收定时群发调度、恢复、逐目标结果和 MCP
- 解除条件：开发者完成验收
- 阻塞/未验证项：真实 Scheduler/provider/CLI 到期发送和 UI 未验证；UI 仍受 DEC-004 局部影响
- TA/提交/证据：`ses_0010355a6b0bf824` / `T-045-scheduled-broadcast-backend-20260918`；worktree `D:\project\pan-worktrees\scheduled-broadcast-backend-20260918`，分支 `feature/scheduled-broadcast-backend-20260918`。

### T-046：Pan 系统后台 Job 通知身份与结构化 Job ID

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`926022d`
- 开发者验收：待确认；下一动作：验收真实 Runner 完成、重启恢复、queue_pending 和前端终态展示
- 解除条件：开发者完成验收
- 阻塞/未验证项：真实 HTTP/WS、provider/CLI、Chromium、Scheduler/Runner 跨重启未验证；定向 145 passed 及合入后回归通过
- TA/提交/证据：`ses_c28ff8194c88f3dc` / `T-046-pan-system-notice-20260918`；worktree `D:\project\pan-worktrees\pan-system-notice-20260918`，分支 `feature/pan-system-notice-20260918`。

### T-047：持久队列投递/清除状态恢复

- 任务类型：代码改动；阶段/状态：整合 / 待开发者验收
- 合入 main：是，`fae290b`
- 开发者验收：待确认；下一动作：验收真实 Worker/provider hand-off、恢复和同类队列状态
- 解除条件：开发者完成验收
- 阻塞/未验证项：未对现场 Session 重发/清除，未运行隔离 HTTP/WS/provider E2E；相关回归、compileall、diff check 已通过
- TA/提交/证据：`ses_2c93476e30d3a676` / `T-047-queue-delivery-recovery-20260918`；worktree `D:\project\pan-worktrees\queue-delivery-recovery-20260918`，分支 `feature/queue-delivery-recovery-20260918`。

## 暂停任务

### T-026 / T-029 / T-039

- 阶段/状态：分别为实现/暂停、方案/暂停、方案/暂停；合入 main：未开始或不适用；开发者验收：恢复后按范围重新定义。
- 下一动作：用户明确恢复并确认范围；解除条件：用户恢复授权。阻塞：用户暂停。
- 证据：既有调查和暂停边界保留在原任务记录；当前不派 TA、不创建 worktree、不修改产品代码。

## 已验收归档索引（批次 A/B/C/D）

> 这里只保留定位信息；原始报告、提交、测试输出和未验证边界不删除。验收结论与日期见 [tasks/acceptance-batches.md](tasks/acceptance-batches.md)。

- 批次 A｜2026-09-18｜用户确认通过：`T-005/T-006/T-008/T-010–T-019/T-020/T-021/T-022/T-024`。实现摘要和历史提交位置见验收单及归档日志。
- 批次 B｜2026-09-18｜用户确认通过：`T-027/T-027.1/T-027.2/T-031/T-037`；关键 main 合并提交：`7a9c7e89`、`99f1774a`、`083a09d`。隔离 HTTP/Chromium 证据入口见验收单。
- 批次 C｜2026-09-18｜用户确认通过：`T-030/T-033/T-038`；关键 main 合并提交：`7f2667b`、`317e46e`。T-040/T-041 是后续独立待验收任务，不因批次 C 自动关闭。
- 批次 D｜2026-09-18｜用户确认通过：`T-035`；main 合并提交 `82823ad`。用户接受验收单中的真机 safe-area、触摸惯性、软键盘和地址栏收缩未单独验证，不再阻塞 T-035。

## 冲突与处理记录

- canonical dirty overview 顶部曾写代码基线 `fc2b001`；Git 实查 `main` HEAD 为 `bd4ec14ae8dde87b6d8158faed6e5ec6662aa7f3`，且 `fc2b001` 已是祖先，故本文件采用后者作为当前基线。
- canonical dirty overview 曾将 T-049/T-050/T-051 写成等待合入，将 T-052 写成调查收口；Git 与 Session 复核后保留为“提交未入 main”“调查已完成并转 T-053”，没有把 T-052 调查 Session 放入当前执行区。
- T-053 worktree 的 HEAD 与 T-051 提交相同但存在未提交产品代码；按 live Session `running` 和文件状态归类为实现中，未误写成已提交或已验收。
- canonical `Pan-main` 的用户 dirty workflow 文件未覆盖、未回滚、未直接修改；本分支只提交本次 overview 文档。
