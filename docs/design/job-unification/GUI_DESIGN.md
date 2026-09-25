# Job 统一 GUI 设计（GUI_DESIGN）

- 用途：JobsView（P3）GUI 需求讨论纪要，由 gui-TA 与用户逐轮收敛
- 状态：讨论中（2026-09-25 开始）
- 规范位置：docs/design/job-unification/GUI_DESIGN.md（2026-09-25 由 data/workdirs/ 转正进版本库）
- 前置：PLAN_JOB_UNIFICATION.md（后端契约源，已冻结方向）；现有面板 ScheduledTaskPanel.tsx

---

## 已定决策

**入口与信息架构（话题 1）**

- 入口：新增规范路由 `/jobs`（JobsView）；`/schedules` 保留为重定向（`<Navigate replace>`）到 `/jobs`。
- 侧边栏：入口标签用纯英文 `Jobs`（替换原 `Tasks`，保持 Chat | Editor | Jobs 三格纯英文风格）；图标弃用 `CalendarClock`（对进程类 job 语义过窄），改中性图标（候选 `ListChecks`，demo 里定稿）；rail / 展开态 / 移动端三处同步。
- 面板去向：退役 `ScheduledTaskPanel` + `ScheduledTasksView`，由 JobsView 取代；其可复用件（cron 简单/高级模式、cron 预设、时间格式化、runs 渲染、SwitchRow）抽为共享模块供 JobsView 复用。
- JobsView 页内结构：两个小标签 —— 「列表」+「创建新 Job」（最终标签文案待定）。
- 创建流模板模型：**模板 = 表单预设**。未选模板 → 展示全部可写字段；选中模板 → 表单按该模板裁剪/预填。首个模板 = 「创建定时任务」（由原 ScheduledTaskPanel 页面演化）。
- 交付方式：先做**伪后端 mock 的前端 demo**，供用户逐步调整（demo 承载需求验证）。

---

## 讨论记录

### 第 1 轮（2026-09-25）：入口与信息架构

**背景**：现只有一个 `/schedules` → `ScheduledTasksView` → `ScheduledTaskPanel`；侧边栏 3 处指向它（rail 图标 / 展开态三格 / 移动端）。路由见 `packages/web/src/router.tsx:30`，侧边栏见 `Sidebar.tsx:409,521,900`。

**提案与结论**：

- 1.1 路由：提案新增 `/jobs` + `/schedules` 重定向 → **采纳**。
- 1.2 侧边栏：提案保留三格结构、标签改 `Jobs`、图标换中性 → **采纳**（用户定：标签纯英文，与现有三格风格一致）。
- 1.3 面板去向：提案退役 Panel+View、抽可复用件 → **采纳**（用户原话"job 替换掉原来的 task panel"）。
- 追加（用户提出）：JobsView 内分「列表 / 创建新 Job」两个小标签；列表记录所有 job。
- 追加（用户提出）：创建流分「无模板 = 全字段 / 有模板 = 按模板裁剪」，首个模板为「创建定时任务」。
- 追加（用户提出）：先做伪后端 mock 的前端 demo，逐步调。

**待确认（下一轮）**：

- 列表标签的展示范围：全部 job（含 completed/failed/cancelled 历史）还是仅活跃？
- 两个小标签的最终文案（`Jobs` / `New Job`？或 `List` / `Create`？）。
- 模板清单首期包含哪些：仅「定时任务」+「自定义」，还是加「广播 / 单发消息 / 后台进程」？
- GUI 的"模板"（表单预设）与后端 schedule 命名预设（"工作日9点"，PLAN §4）是两个层级，需确认后者作为 schedule 字段内部的快捷项。
