# P6 旧 UI advanced Action/Template 流程兼容（20261107）

基线：`b9d8dc3b3db26ab70b5d487db96762981f34f571`。独立 worktree：
`D:/project/Pan/data/workdirs/action-unification-ui-20261107`；分支：
`feature/action-unification-p6-ui-20261107`。未修改 `main` / `practical`、真实
data/profile、8767/8768；未部署、未 push。

本片只改 React 前端、UI API client、Vitest 和隔离浏览器 fixture。未改 P6 后台
runtime 接线；与正在运行的 P6 runtime TA 路径隔离。

## 1. 原红与逐项映射

先建立 8 个基线失败用例，命令与原始输出见
[old-ui-red.log](../../evidence/action-unification-p6-ui/old-ui-red.log)。失败原因
均是旧 UI 没有 advanced 入口：mapped Action 面板、canonical duplicate、canonical
lifecycle、Template duplicate/publish 控件不存在。

| 流程 | 旧入口 / 原状 | 本片界面 | API 接线 | 保留语义 | 证据 |
| --- | --- | --- | --- | --- | --- |
| advanced Action duplicate | 旧 `More actions → Duplicate` 只复制单命令 legacy 投影，advanced 多命令会丢失 | mapped Action 面板的 `Duplicate canonical Action`；输入副本名后创建 canonical 副本，并打开完整定义 | `POST /api/jobs/actions`，`expectedRevision: 0`，新 `requestKey`，完整 `definition` | 不再走 legacy POST；副本不绑定旧 alias，界面明确提示到统一目录管理；旧来源字节不变 | `ActionsMappedCompat.test.tsx` duplicate；browser duplicate 断言 2 条命令、timeout 19 |
| advanced Action new | 旧 `New Action` 只能编辑单命令/API/Session Operation legacy schema | 旧 UI header `New advanced Action`，复用 `CanonicalDefinitionEditor` | `POST /api/jobs/actions`（完整定义，创建 draft） | 保存不发布、不执行；旧 legacy 新建路径保持原样 | `ActionsMappedCompat.test.tsx` create |
| mapped Action delete | mapped 行没有删除入口；直接删 legacy 投影会破坏 alias/来源 | mapped 面板 `Delete canonical Action`，先检查 Group 引用 | canonical-backed 兼容适配器 `DELETE /api/actions/actions/{legacyId}?expectedRevision=<registry revision>` | 保留旧 registry CAS、alias tombstone、source preserve；Group 引用在发请求前阻断；Job/Template 引用由服务端阻断，不自动 `confirmReferences` | `ActionsMappedCompat.test.tsx` block/delete；browser 断言无 DELETE 请求 |
| mapped Action lifecycle | mapped 行只在 canonical 编辑器里改定义，没有 publish/enable/disable | mapped 面板按 state 显示 `Publish` / `Enable` / `Disable` | `POST /api/jobs/actions/{id}/operations`，携带 `expectedRevision` + `expectedStateRevision` + `requestKey` | 使用打开时观测到的 CAS；后台刷新不静默推进；403 直接拒绝；网络不确定只查询原操作，不重放 | `ActionsMappedCompat.test.tsx` publish/disable、CAS、unknown、403；browser disable/enable |
| legacy Action lifecycle | 旧行只能靠编辑保存 `enabled` | 行菜单 `Disable Action` / `Enable Action` | `POST /api/actions/actions/{id}/lifecycle`，`expectedRevision=<registry revision>` | 保留旧权限与 registry CAS，不改旧字段 | `ActionsView.test.tsx` 回归 |
| advanced Template duplicate | canonical JobTemplates 只有 new/edit/publish/instantiate/retire，没有 duplicate | JobTemplates 卡片 `duplicate` | `POST /api/job-templates`，完整 `jobDefinition` + `composition`，`expectedRevision: 0`，新 `requestKey` | 保留完整 advanced 定义；去除服务端字段与 `provenance`；新版本从 draft 开始 | `CanonicalJobsTemplatesCompat.test.tsx`；browser 断言完整 jobDefinition/actionRef/composition |
| Template new / publish / delete / instantiate | 已有 canonical 控件，但本片要确认完整链路 | JobTemplates 的 `创建 JobTemplates`、`Publish`、`retire`、`Instantiate` | `POST /api/job-templates`、`POST /api/job-templates/{id}/operations`、`DELETE /api/job-templates/{id}`、`POST /api/job-templates/{id}/instantiate` | publish/retire 使用 `revision` + `stateRevision`；retire 不物理删除；instantiate 仍走原模板冻结/Job composition | `CanonicalJobsView.test.tsx`、`CanonicalJobsTemplatesCompat.test.tsx`；browser new/publish/retire |
| complex / preserved records | 旧 UI 不展示 preserve 原因，且可能用 legacy 编辑器覆盖 | mapped 面板显示 `mapped.reason`；无 canonical mapping 的 preserved Action 直接阻断编辑并提示到统一目录 | 无写请求 | 复杂记录继续 preserve/blocked；不猜测、不压平、不覆盖原字节 | `ActionsMappedCompat.test.tsx` reason；代码路径 `preservedActions` |

## 2. 实现范围

- `packages/web/src/views/ActionsView.tsx`：mapped Action 面板入口、advanced new、
  legacy lifecycle、preserved 阻断。
- `packages/web/src/components/actions/MappedActionPanel.tsx`：duplicate/lifecycle/
  delete 的 CAS、requestKey、unknown 查询和 preserve 提示。
- `packages/web/src/views/CanonicalJobsView.tsx`：JobTemplates duplicate。
- `packages/web/src/services/canonicalJobs.ts`、`packages/web/src/types/actions.ts`：
  canonical Action client 与 `preservedActions` 类型。
- `packages/web/e2e/action-p6-server.py`、`action-p6-ui-compat.e2e.mjs`：隔离真实
  HTTP + production Chromium fixture。

## 3. 验证边界

已验证：

- 源码/TypeScript：`npx tsc -b --noEmit` 通过。
- 聚焦 Vitest：9 个相关文件 **86 passed**。
- 完整 Vitest：**2672 passed / 4 failed / 3 skipped**（2679）。4 个失败在基线
  `b9d8dc3` 的同一测试文件上同样失败，属于既有基线，不是本片改动：
  `mockBackend.test.ts` 1 个、`reaudit.orderedEvents.test.tsx` 1 个、
  `AutomationWorkspace.test.tsx` 2 个。基线复跑日志见
  [vitest-baseline-known-failures.log](../../evidence/action-unification-p6-ui/vitest-baseline-known-failures.log)。
- `pnpm build`：TypeScript + Vite + 压缩资源校验通过，**90 个 production assets**。
- 真实隔离 HTTP + production Chromium：随机未保护端口、实际 FastAPI、真实
  `/react/` 资产；桌面 1280 与移动 390 各 1 次；13 项 checks 全部通过；无页面异常、
  无横向溢出。证据见
  [browser-evidence.json](../../evidence/action-unification-p6-ui/browser-evidence.json)、
  [browser-requests.json](../../evidence/action-unification-p6-ui/browser-requests.json)
  和 `mapped-1280/390.png`、`template-1280/390.png`。

未验证 / 未声称：

- 远端 CI、Linux/macOS。
- 真实 data/profile 迁移、真实 owner/lease 切换、部署、人工验收。
- 真实模型 CLI/QQ/外部 provider。
- 旧完整 Template 编辑器 `JobTemplateEditor` 当前没有生产路由；本片的 Template
  完整界面是 `/automation/canonical` 的 JobTemplates tab。该旧组件及其旧
  `CompleteJobTemplate` client 端点未改，也未宣称可达。
- canonical-only 的 Action 副本/新建项不会出现在旧 alias 列表；界面明确提示到统一
  目录管理。这是旧来源 registry 的边界，不是失败后的静默丢失。

## 4. 与 protected refs 的关系

- `main`：`d08ac2678b2bf7a51af8b29aebc051c0a14354f1`（未改）
- `practical`：`4777a5e26e4c8c6ef1fd8f5c59a59e4934119429`（未改）
- 基线/父候选：`b9d8dc3b3db26ab70b5d487db96762981f34f571`
- 本候选 SHA 见交付终端报告（本文件随候选提交）。
