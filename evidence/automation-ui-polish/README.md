# Automation 前端美化（candidate evidence）

- 分支：`feat/automation-ui-polish-20261104`
- 基线：`c9dc621a01ccb568878c8a37297c034cbaec18de`
- 范围：React `packages/web/src/views/AutomationView.tsx`、`views/CanonicalJobsView.tsx`、`components/automation/{display,EventRegister,JobUserAuthorization,fields}` 及针对展示层的 Vitest、独立真实 HTTP 浏览器 e2e。
- 未改动：后端 API/store/领域契约、授权与 CRUD、请求身份、状态机、禁用条件、危险操作确认；未引入新依赖或第二套 UI。

## 设计方向

成熟克制的深色工作台：沿用 Pan 既有 `bg-primary/secondary/tertiary`、`border-default`、`text-*` 与 accent/success/warning/danger token；页面统一为“eyebrow + 标题 + 说明 + 关联入口”的页头、分段式页签、`max-w-6xl` 内容宽度和一致留白。列表卡片改为“标题 + 状态 badge + 等宽 ID + 版本元数据 + 主次操作分区”，表单增加明确的编辑/创建标题、保存版本说明和底部操作栏。错误、未授权、空列表、加载态均使用同一套带边框面板；原始定义、schema、Run 回执等仍通过可展开的 `<details>` 保留，不隐藏未知状态。

状态 badge 保留原始状态文本，未知状态回退为中性色；`enabled/published/completed`、`draft/paused/pending`、`running/accepted`、失败/未知族分别着色。危险入口（retire、Delete Scheduler、确认 retire）仍保留确认与红色语义。

## 真实页面截图

同一套 owned fixture（合成 profile、动态端口、production bundle、真实 canonical HTTP）在基线 `c9dc621a` 与候选树上分别截取；基线截图由冻结归档构建，未修改任何现有服务。

桌面 1440：

- 基线：[Jobs](before/before-1440-jobs.png) · [Triggers](before/before-1440-triggers.png) · [Event Register](before/before-1440-events.png) · [Canonical](before/before-1440-canonical.png) · [Job 编辑](before/before-1440-canonical-editor.png) · [Scheduler 编辑](before/before-1440-event-editor.png) · [Runs](before/before-1440-canonical-runs.png)
- 候选：[Jobs](after/after-1440-jobs.png) · [Triggers](after/after-1440-triggers.png) · [Event Register](after/after-1440-events.png) · [Canonical](after/after-1440-canonical.png) · [Job 编辑](after/after-1440-canonical-editor.png) · [Scheduler 编辑](after/after-1440-event-editor.png) · [Runs](after/after-1440-canonical-runs.png)

手机 390 / 320：

- 基线：[390 Jobs](before/before-390-jobs.png) · [390 Events](before/before-390-events.png) · [390 Canonical](before/before-390-canonical.png) · [320 Jobs](before/before-320-jobs.png) · [320 Events](before/before-320-events.png) · [320 Canonical](before/before-320-canonical.png)
- 候选：[390 Jobs](after/after-390-jobs.png) · [390 Events](after/after-390-events.png) · [390 Canonical](after/after-390-canonical.png) · [320 Jobs](after/after-320-jobs.png) · [320 Events](after/after-320-events.png) · [320 Canonical](after/after-320-canonical.png)

状态证据（候选，真实 HTTP；错误态由停掉自有 server 后刷新产生）：

- [未授权](states/unauthorized-canonical-1440.png)
- [空列表](states/canonical-empty-templates-1440.png)
- [传输错误](states/canonical-error-1440.png)

## 验证命令与结果

前端（`packages/web`，工作树内安装依赖）：

```text
npx tsc -b --noEmit                  # exit 0
pnpm build                           # exit 0, 88 compressed assets
npx vitest run src/views/AutomationView.test.tsx src/views/AutomationWorkspace.test.tsx \
  src/views/CanonicalJobsView.test.tsx src/views/CanonicalJobsView.retirement-r13.test.tsx \
  src/views/CanonicalJobsView.retirement-r14.test.tsx src/components/automation/CanonicalForms.test.tsx \
  src/components/automation/fields.test.tsx src/components/automation/RetirementRuntimeFields.test.tsx \
  src/components/automation/SchedulerInstantInput.test.tsx src/components/automation/display.test.tsx
# 10 files / 85 tests passed
```

`git commit` 触发 `scripts/pre-commit`（`tsc -b --noEmit`），通过后生成候选提交。

覆盖的 Vitest：`AutomationView.test.tsx`、`AutomationWorkspace.test.tsx`、`CanonicalJobsView.test.tsx`、`CanonicalJobsView.retirement-r13/14.test.tsx`、`CanonicalForms.test.tsx`、`fields.test.tsx`、`RetirementRuntimeFields.test.tsx`、`SchedulerInstantInput.test.tsx`、新增 `display.test.tsx`（状态 badge 原文/未知状态保留、页头导航、alert/空态）。运行日志中保留既有 React duplicate-key 提示（来自保留数据面板的测试替身），不影响通过。

独立真实 HTTP 浏览器 e2e（自有临时 profile、动态端口，拒绝 8767/8768）：

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_automation_ui_polish_browser.py -o addopts= -q
# 1 passed
```

该 e2e 在生产 Chromium（Playwright 151）中验证：真实 Bearer 登录、未授权 401 状态、1440/390/320 三档无横向溢出、可见按钮触控高度（桌面 ≥36px，手机 ≥44px）、键盘方向键切换页签、Job/Trigger/Event 列表与详情、启用的显式 review、危险操作确认、Canonical 类型切换与空态、Runs 与操作确认表单、停止 server 后真实网络错误。Provider 为 owned `RecordingProvider`，不调用真实模型/账号。

## 未验证与边界

- 未运行全量 `python -m pytest tests/ -q`；仅运行上述独立 e2e 与前端相关套件。
- 未在 main/practical 实例、真实 provider、真实用户 profile 上验证；未 push、未部署、未改 8767/8768、未改其他 TA 工作树。
- 未对手机实体触屏、屏幕阅读器/高对比度模式做人工验收；已做的键盘与尺寸检查为自动化断言。
- 截图使用合成数据（owned session/Job/Trigger/Scheduler），不含真实密钥、账号或用户数据。
