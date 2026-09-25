# Job 统一 GUI 设计（GUI_DESIGN）

- 用途：JobsView（P3）GUI 需求讨论纪要，由 gui-TA 与用户（后端 MA 代拍板）逐轮收敛
- 状态：**完成（对接闭环）**（2026-09-25；11 个增量全部落地并入 `integrate/pr1`，head `bdbed76`；已改走真实 `/api/jobs/*`，mock 数据层已删除）
- 规范位置：docs/design/job-unification/GUI_DESIGN.md（2026-09-25 由 data/workdirs/ 转正进版本库）
- 前置：PLAN_JOB_UNIFICATION.md（后端契约源）；现有面板 ScheduledTaskPanel.tsx（已被取代）
- demo 实现：`packages/web/src/views/JobsView.tsx` + `packages/web/src/components/jobs/{mockJobs,NewJobForm,ScheduleListEditor,JobDetailDrawer}.tsx`

---

## 已定决策

**话题 1 · 入口与信息架构**

- 入口：新增规范路由 `/jobs`（JobsView）；`/schedules` 保留为重定向（`<Navigate replace>`）到 `/jobs`。
- 侧边栏：标签用纯英文 `Jobs`（替换 `Tasks`，保持 Chat | Editor | Jobs 三格纯英文风格）；图标 `CalendarClock` → `ListChecks`（原图标对进程类 job 语义过窄）；rail / 展开态 / 移动端三处同步。
- 面板去向：退役 `ScheduledTaskPanel` + `ScheduledTasksView`，由 JobsView 取代；其可复用件（cron 简单/高级模式、cron 预设、时间格式化、runs 渲染、开关控件）抽为共享模块。
- JobsView 页内结构：两个小标签 —— `Jobs`（列表）+ `New Job`（创建）。
- 交付方式：先做伪后端 mock 的前端 demo，逐步迭代验证。

**话题 2 · 列表设计**

- **Q2.1 范围与排序（已拍板）**：**全部 job 一律入列**（含 `completed`/`failed`/`cancelled` 终态历史）；排序 = **活跃优先**（active-first：running → starting → scheduled → pending → 终态，同档内按 `updatedAt` 倒序）；保留 status chips（All / Active / Scheduled / Failed / Undeliverable）+ kind 下拉筛选，AND 叠加。
- **Q2.2 字段与可读标识（已拍板）**：job 增加 **`name`（必填）+ `description`（可选）**；空位默认命名（空槽位语义，demo 用 `job-N`）。列表**主列 = `name` + kind 标识 + status badge**；**次列 = `target` / 下次触发 / 最近结果**。
- **Q2.3 首期模板清单（已拍板）**：两个 —— ①「定时任务」模板（由原 ScheduledTaskPanel 演化：schedule 列表 + target + text）②「自定义」（全字段）。广播 / 单发消息 / 进程模板**本期不做**，真实需求出现再加。
- 模板层级：GUI「模板」作用于**整个 job**（决定整个表单的字段集与默认值）；后端字段级命名快捷模板（"工作日9点"）本期不做。

**话题 3 · 详情视图**

- 打开方式：**右侧抽屉**（点列表行打开；移动端全屏；Esc / 遮罩 / 关闭按钮三种关闭方式）。
- 内容 6 分区（只读展示为起点）：① 概览（name/description/kind/status/source/target/runCount/时间戳/paused）② schedule entries（逐条 kind/表达式/timezone/misfirePolicy/enabled/nextFireAt）③ 最近结果（`lastDelivery`，broadcast `partial` 可展开 / `lastError`）④ runs 历史（最近 N 条 + Load more）⑤ 积压（`mailbox` 条数 + 逐条摘要）⑥ 日志（仅进程类有 `logPath`，带复制）。

**话题 4 · 创建 / 编辑流**

- 模板选择器两个：`创建定时任务`（默认）/ `无模板（全字段）`；有模板 → 表单按模板裁剪，无模板 → 全部可写字段。
- 全字段 = 基本信息(name/description) · kind(5 种) · source(agent/user/system/plugin) · target(含"无 target") · schedule(列表编辑器) · action(api 下拉 + args 随 api 切换) · 限制(maxRuns/paused)。
- schedule 编辑器抽为共享组件（`ScheduleListEditor`），两模板共用，复用 `cronPreview`（不复制 cron 解析逻辑），支持多条增删 + 逐条 enabled + 简单/高级模式 + misfirePolicy + next-fire 预览。
- `name` 留空 → 自动生成 `job-N`；`description` 可空。
- **编辑已有 job**：从详情 `Edit` 打开全字段表单（预填），**`kind` 只读**、**`name` 必填**；保存保持 `jobId`/`createdAt`，更新 `updatedAt`。
- **创建/编辑契约（见话题 7·A/B）**：本期创建仅 `scheduled-task`；创建时 `target` 必填且 session 须存在；编辑/改 target 可清空为「无 target」（→ `undeliverable` 积压）。

**话题 5 · 通知与提醒**

- toast：成功/信息类走 `info`；`undeliverable` / target 缺失**不弹 toast**（避免噪音），改为列表行常驻红条 + 详情顶部 banner。
- 列表行：`mailbox` 非空即显示 `N backlogged` 计数（不限于 undeliverable）。
- 详情顶部：`undeliverable` 时 banner「target missing — switch target to deliver N backlogged note(s)」。
- **`warning` toast 变体（已拍板：加）**：`ToastMessage.type` 增加 `"warning"` + `Toast` 组件琥珀色系样式；`job.partial_failed` 事件 → warning toast。定性："部分失败 = 值得知道但不需处理"，info 信号不足、error 过度。
- `undeliverable` **维持不弹 toast**（行红条 + 详情 banner 不变）——它是**持续状态不是事件**，弹了会刷屏。

**话题 6 · 管理动作**

- 动作集合：`run_now` / 启停（job 级 `paused` + 逐 schedule entry `enabled`）/ 改 `target`（归属切换，切换后积压便条重投）/ 编辑 / 删除（二次确认）。
- 位置：列表行 `⋯` 菜单放高频项（`Run now` / `Pause·Resume` / `Delete`）；完整动作组（含 `Change target` / `Edit`）放详情顶部。
- 删除二次确认弹窗；**批量操作本期不做**。
- **run_now 范围（见话题 7·C）**：仅 `scheduled-task` 显示 `Run now`；其余 kind 隐藏或禁用 + tooltip。

**话题 7 · mock → 真实 API 对接（已拍板：现在切）**

- 决策：GUI 结构已稳定（6 话题全落地、8 增量全并入），**立即把 mock 换成真实 `/api/jobs/*`**；后端前置已就绪（`cd428cc` 修好 `_emit` 异步广播 bug）。
- 计划：删 `mockJobs.ts`，改走 `packages/web/src/services/api.ts` → 真实 `/api/jobs/*`；契约源 = `packages/jobs/api.py` + `tests/test_jobs_api.py`。
- WS：`scheduler.task.fired` / `job.partial_failed` → toast（info/warning/error 对号）；`undeliverable` 靠列表/详情刷新呈现，不依赖事件。
- 已定事件名：`job.updated`、`job.deleted`、`job.fired`、`scheduler.task.fired`、`job.partial_failed`。
- **待后端 MA 裁定的契约歧义**（不猜，见下）：创建端点缺失 / target 不可清空 / run-now 仅 scheduled-task / 积压字段名（`undeliveredFires` vs `mailbox`）等。

**话题 7 · 契约裁定（2026-09-25，后端 MA 拍板；后端已实现 `1f6f94b`，`integrate/pr1` head `81f13fd`）**

- **A 创建端点**：`POST /api/jobs` 已补，本期**仅 `kind=scheduled-task`**；其余 kind 返回 `invalid_argument` 并指回专有端点（`/api/background-jobs`、`/api/session-message-jobs`）。schedule 支持 **spec 列表**（多 entry，每项可覆盖 `misfirePolicy`/`enabled`）或单 spec（兼容）。→ GUI 模板①「定时任务」与②「自定义」**都创建 `scheduled-task`**（②的真实含义 = 全字段的定时任务）；其余 kind 创建入口**本期隐藏**。
- **B target 清空**：**创建**时 `target` 必填且 session 必须存在（服务端 `session_not_found`）；**PATCH 允许显式清空**（`target: null` 或 `{sessionId: null}`）→ 进入 `undeliverable` 积压态（PLAN §10：后续触发直接积压不派发，切换/恢复后自动重投）。
- **C run-now**：**仅 `scheduled-task`**；其余 kind 服务端返回 `invalid_argument`。GUI 其余 kind 隐藏或禁用 + tooltip；message 类 run-now 本期不做。
- **D 积压字段**：GUI 改读真实视图 **`undeliveredFires`**（`{entryId, fireAt, dispatchKey, text, error}`，上限 20）+ **`lastStatus === 'undeliverable'`**。PLAN §1 的 `mailbox` 属**文档漂移**，后端已改文档对齐实现。
- **E WS 订阅（5 类）**：`scheduler.task.fired` → 触发 toast（统一内核原生事件名，兼容层不再另发）；`job.partial_failed` → warning toast；`job.updated` → 行刷新/upsert；`job.created` → 行插入；`job.deleted` → 行移除。`undeliverable` 无独立事件，靠 `job.updated` + 列表刷新呈现。（`job.fired` 已删除——run-now 曾双发，修后**一次触发至多一个 fire 事件**。）
- **F 列表筛选/排序**：**客户端做**（拉全量；服务端 `kind`/`status`/`includeCompleted` 参数不用）。
- **G kind 展示**：kind 徽标改用后端 `/api/jobs/kinds` 的中文 label（后台进程 / 定时消息 / 群发消息 / 定时任务 / 服务生命周期）。
- **落地补记（inc10 `8a26fff` / inc11 `bdbed76`；以裁定 + 实际代码为准）**：
  - `PATCH /api/jobs/{id}` **已支持 `text`**（`c0318c4`，`packages/jobs/api.py:303-307`）；Edit 表单含「派发正文」编辑框（创建/编辑双态 + 非空校验）。
  - **空 target 禁用 `Run now`**（行内 `⋯` 菜单 + 详情抽屉两处：`disabled` 样式 + tooltip + 守卫；`JobsView.tsx:238`、`JobDetailDrawer.tsx:244`）。
  - **逐 entry 启停 = schedule 整体替换**（`PATCH {schedule: specs}`，`JobsView.tsx:538`）——后端无 per-entry 端点，追认此实现。
  - **runs 记录补 `entryId` 缓办 → P4**（当前 `packages/jobs/api.py` 的 runs 无 `entryId`）。
  - `POST /api/jobs` 仅 `scheduled-task`（`api.py:138,146-148`，`text` 必填、`target.sessionId` 必填）；模板①「定时任务」与②「自定义」**皆创建 `scheduled-task`**，其余 kind 创建入口隐藏。
  - WS 五事件**已接线**（`JobsView.tsx:382-401`）：`job.created`/`job.updated`/`job.deleted` 驱动列表插入·更新·移除；`scheduler.task.fired` → info toast；`job.partial_failed` → warning toast；`job.fired` 不存在。
  - `packages/web/src/components/jobs/mockJobs.ts` **已删除**，全仓无残留引用。

**有意未做（记录在案）**

- 批量操作。
- ~~后端对接~~ → 已转为「话题 7」并拍板实施。

---

## 讨论记录

### 第 1 轮（2026-09-25）：入口与信息架构

**背景**：现只有一个 `/schedules` → `ScheduledTasksView` → `ScheduledTaskPanel`；侧边栏 3 处指向它（rail 图标 / 展开态三格 / 移动端）。路由见 `packages/web/src/router.tsx:30`，侧边栏见 `Sidebar.tsx:409,521,900`。

- 1.1 路由：提案新增 `/jobs` + `/schedules` 重定向 → **采纳**。
- 1.2 侧边栏：提案保留三格、标签改 `Jobs`、图标换中性 → **采纳**（用户定：标签纯英文）。
- 1.3 面板去向：提案退役 Panel+View、抽可复用件 → **采纳**（"job 替换掉原来的 task panel"）。
- 追加（用户提出）：JobsView 内分「列表 / 创建新 Job」两个小标签；创建流分「无模板=全字段 / 有模板=按模板裁剪」；先做 mock demo 逐步调。

### 第 2 轮（2026-09-25）：列表 / 创建模板 —— **已拍板**

- 模板层级澄清（用户）：GUI 模板作用于**整个 job**；字段级快捷模板本期不做。
- **Q2.1 已拍板**：全部 job 入列 + active-first 排序 + status/kind 筛选。demo 已实现（`908336d`），追认生效。
- **Q2.2 已拍板**：后端加 `name`（必填）+ `description`（可选），空位默认命名；demo 与后端（`a2ee30a`、`packages/jobs/api.py` 的 rename/description patch）均已实现，追认生效。列表主列 = `name` + kind 标识 + status badge，次列 = target/下次触发/最近结果。
  - 备注：demo 当前 kind 以**文字徽标**呈现，非图标（如需改为图标，属可选微调）。
- **Q2.3 已拍板**：首期两个模板「定时任务」+「自定义」；广播/单发/进程本期不做。
- 交付编排：由 gui-TA(MA) 派发实施 TA（会话 `jobsview-demo`，`ses_8731e1cfc25ef847`，cbc / deepseek-v4-pro）逐步实现，workdir = 工作树，与 MA 共用分支，逐增量 FF 合入 `integrate/pr1`。

### 第 3 轮（2026-09-25）：详情视图

- 打开方式：**右侧抽屉**（点行打开，移动端全屏）；仅读展示起步，动作留到话题 6。
- 内容 6 分区：概览 / schedule entries / 最近结果 / runs 历史（含 Load more）/ 积压 mailbox / 日志 logPath。

### 第 4 轮（2026-09-25）：通知与提醒

- `undeliverable` 不弹 toast，改列表红条 + 详情 banner 两级提示；列表行 `N backlogged` 计数；toast 仅 info/error（warning 变体待定）。

### 第 5 轮（2026-09-25）：管理动作

- 动作集合：`run_now` / 启停（job + 逐 entry）/ 改 target（重投积压）/ 编辑 / 删除（二次确认）；行内放高频、详情放全量；批量不做。

### 第 6 轮（2026-09-25）：开口项收口（后端 MA 代拍板）

- **开口项 1 · `warning` toast 变体**：**采纳，加**。`ToastMessage.type` 增 `"warning"` + 琥珀色样式；`job.partial_failed` → warning toast；`undeliverable` 维持不弹（持续状态非事件）。
- **开口项 2 · mock → 真实 API**：**现在切**，作为独立增量。前置 `cd428cc`（`_emit` 异步广播修复）已合入。
- 同时记录：`integrate/pr1` 已含 **P2 统一 API**（`e8930b3` / `ae527e8`），后端契约文件 `packages/jobs/api.py`（310 行）+ `tests/test_jobs_api.py`（351 行）。

---

## 实现台账（demo，均已合入 `integrate/pr1`）

| # | 增量 | commit |
|---|---|---|
| 0 | 设计文档转正进版本库 | `6d4dfa3` |
| 1 | JobsView 骨架 + `/jobs` 路由 + 侧边栏 | `f315155` |
| 2 | `name`/`description` + 默认命名语义 | `815c367` |
| 3 | 列表筛选 + active-first 排序 + 行动作占位 | `908336d` |
| 4 | 创建 tab：两模板 + 定时任务表单 | `f69f710` |
| 5 | 详情抽屉（6 分区） | `5e2ab6e` |
| 6 | 管理动作 + 通知联动 | `bd1dc86` |
| 7 | 共享 schedule 编辑器 + 全字段表单可用 | `e0465fc` |
| 8 | `Edit` 编辑已有 job | `81b49f4` |
| 9 | `warning` toast 变体（琥珀色） | `021a077` |
| 10 | JobsView 改走真实 `/api/jobs/*`（删 mock 数据层）+ 5 类 WS 事件接线 | `8a26fff` |
| 10b | 后端 `PATCH /api/jobs/{id}` 支持 `text` | `c0318c4` |
| 11 | 表单支持派发正文编辑（双态+非空校验）+ 空 target 禁用 `Run now` | `bdbed76` |
