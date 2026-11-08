# 统一 Action/Group 前端（20261108）

基线 `0ba70483edffdbd26b77c83c1a184724670f9967`（`main`）。独立 worktree
`D:/project/pan-worktrees/action-group-ui-unified-20261108`，分支
`feature/action-group-ui-unified-20261108`。未修改 `main` / `practical`、真实
data/profile、8767/8768；未 push、未部署、未启停既有服务。

## 0. 结论

- 原页面用 `UnifiedActionsView.tsx` 里的 `legacy` 布尔量做整页 `统一目录 ⇄
  旧定义与 Session 绑定` 切换，且 canonical 侧只有 Action，**没有任何 Group
  入口**：Group 必须切到旧系统才存在。
- 后端已具备 canonical-only 的
  `Action → 固定版本 → Group build → 插件目录发现 → Session 绑定 → 三种 MCP`
  全链路能力（逐项见 §1），因此本片是**前端缺口**，可以直接在 React 内完成。
- 审计同时发现 **2 处真实后端暴露缺口**（§2）。两处都不是“纯显示问题”：前端
  无法在不编造配对、不伪造历史的前提下满足验收，故相关子项按缺口暂停，其余
  独立前端工作继续完成。

## 1. 后端能力逐项核对（canonical-only 链路）

| 需求 | 端点 / 符号 | 结论 |
| --- | --- | --- |
| 单一 Action 目录 + 版本索引 + 固定引用 | `GET /api/jobs/actions`（`packages/jobs/final_api.py:443 action_catalog`）→ `FinalService.actions`（`packages/jobs/final_service.py:2116`）返回 `items/versions/references/draftReferences` | 有 |
| 单版本 / 固定 ref 读取 | `GET /api/jobs/actions/{id}/versions[{revision}]`（`final_api.py:509/515`） | 有 |
| 调用固定版本 | `POST /api/jobs/actions/{id}/invocations`（`final_api.py:498`，请求体必须带 `actionRef`） | 有 |
| 发布 / 启用 / 停用 | `POST /api/jobs/actions/{id}/operations`（`final_api.py:548`） | 有 |
| Group 目录（草稿固定版本 + release 指针） | `GET /api/action-groups`（`packages/actions/group_api.py:18`）→ `Groups.list`（`packages/actions/groups.py:90`） | 有 |
| Group 草稿写 / build / unbuild / delete（CAS + requestKey） | `group_api.py:24/60/82/40` → `Groups.put/publish/unbuild/delete`（`groups.py:95/117/290/304`） | 有 |
| 单个 release 读取 | `GET /api/action-groups/releases/{version}`（`group_api.py:54` → `Groups.release` `groups.py:285`） | 有（仅按版本号） |
| Group release → 插件目录投影 | `packages/actions/group_catalog.py:extend`，由 `Catalog.__init__` 自动 `extend_catalog`（`packages/plugins/catalog.py:191`）；plugin id `action-group-<id>`、`version=release.version`、`supported_modes` 默认三种 | 有 |
| Session 三种 MCP 绑定（CAS） | 读：`GET /api/sessions` 含 `pluginBindings` / `pluginSelectionRevision`（`packages/web/server.py:_session_plugin_fields`）；写：`PATCH /api/sessions/{id}` 要求 `pluginBindings` + `expectedPluginRevision`（`server.py:3709-3724`） | 有 |
| Group / Job 引用升级预览 + 应用 | `group_api.py:34/48`、`final_api.py:480/486` | 有 |
| Action 的 Job 引用 | `GET /api/jobs/actions/{id}/job-references`（`final_api.py:474`） | 有 |
| 旧记录投影（preserve / blocked / 使用情况 / 加载版本） | `GET /api/actions`（`packages/actions/api.py:107`）+ `group_migration.projection` | 有 |

结论：**除 §2 两处暴露缺口外，canonical-only 主链路后端完整**，本片不需要、
也未新增任何后端接口。

## 2. 真实后端缺口（具名）

### 缺口 A：Group 历史 release 不可枚举

- **现状符号**：`packages/actions/groups.py:152` 为每次发布写一条不可变
  `final_documents(kind='action_group_release', id=operation_id)`，随后
  `groups.py:153` 只把 `group["release"]` 指向最新一条；读取侧只有
  `groups.py:285 Groups.release(version)` / `group_api.py:54`。
  `final_api.py` 的 `KINDS`（`final_contracts.py:30`）不含
  `action_group_release`，通用列表路由无法列出它。
- **缺失的请求形状**：`GET /api/action-groups/{group_id}/releases?limit=…`
  → `{items:[{version, digest, revision, createdAt, tools}]}`；或让
  `Groups.list` 的组文档附带 `releases:[…]`（仍受 `LIMIT=100` 约束）。
- **原方案验收为何遗漏**：详细方案 §11 只要求
  “Group 一个条目含草稿与 release 版本视图”，P4/P6 验收只覆盖
  `GET releases/{version}` 单读；旧 UI `CanonicalGroupEditor.tsx:91-114`
  用“手工输入 release 版本号”绕过枚举，于是“看不到历史”被交互形式掩盖，
  从未被列为验收项。
- **影响**：统一目录只能显示当前 release，或按已知版本号查看某一个；
  “历史 release 易发现”这条不能由前端单独满足。
- **处置**：该子项暂停（未自造历史列表、未启发式猜测版本）。已保留原能力：
  查看当前 release、按版本号查看、把某个 release 的固定版本复制回草稿
  （都需要显式 Build 才生效）。

### 缺口 B：Group 采纳产生的 `legacyId → actionId` 映射无读取面

- **现状符号**：Group 采纳写入 `legacy_action_binding`
  （`packages/actions/group_migration.py:540`、`:638`，字段
  `{sourceRoot, legacyId, actionId, raw, deleted}`）。公开读取端点只有
  `GET /api/jobs/actions/aliases/{legacy_id}`（`final_api.py:468` →
  `FinalService.action_alias` `final_service.py:2227`），它读的是**另一张表**
  `final_action_aliases`，写入者是显式别名注册
  （`final_api.py:580` → `final_service.py:2215`）。因此 Group 采纳出来的别名
  在该端点上是 404（本片浏览器证据已记录该 404）。
- **缺失的请求形状**：`GET /api/jobs/action-aliases?limit=…`
  → `{items:[{legacyId, actionId, sourceRoot, deleted}]}`，或在
  `GET /api/jobs/actions` 的 `items[]` 上补 `legacyAliases:[legacyId]`。
- **原方案验收为何遗漏**：`canonicalActions`（`group_migration.py:855-860`）
  只在 **preserve 分支**填充，P6 UI 只验收 mapped/preserved，从未把
  “canonical + 旧投影”放进同一个列表。于是“一个逻辑 Action 一行”在跨来源
  场景没被验收过；上游 `Groups._preview`（`groups.py:211-221`）自己也承认这种
  引用只能报 `legacy_action_identity_unresolved`。
- **影响**：被采纳过的 Action 在统一列表可能同时出现 canonical 行与别名行；
  Action 级“已接管”徽标只能对 id 相同或已注册显式别名的情况显示；引用升级
  预览对这类目标仍是 unresolved。
- **处置**：不猜配对（不按名称/顺序推断）。已接官方别名端点：能解析的别名
  会合并为同一行；解析不到的保持“旧记录”呈现，并在报告与证据中记录 404
  观测。该子项待后端补读取面后再合并。

## 3. 前端实现

同一 `Actions & Groups` 主导航（`Segmented` Actions / Groups），不再整页切换：

- `packages/web/src/views/UnifiedActionsView.tsx`（重写）：Actions 与 Groups
  共用同一列表、搜索、筛选、刷新与详情区；Action 详情保留版本选择器、固定
  ref、定义编辑、参数调用、publish/enable/disable、引用升级与 unknown 查询；
  新增“新建 Action”（canonical 草稿）与“新建 Group”。
- `packages/web/src/components/actions/unifiedModel.ts`（新增）：把
  canonical 目录与旧 registry 投影合并成“一逻辑条目”，给出
  `canonical / adopted / legacy / preserved` 状态、组引用计数、build 阻断原因、
  Session 绑定与运行中阻断。
- `packages/web/src/components/actions/CanonicalGroupPanel.tsx`（新增）：固定
  工具（Action + 已发布版本）、保存草稿、Build / Unbuild、删除、当前与按版本号
  查看 release、复制到草稿、组级引用升级入口、Session 绑定入口、原 Group 操作
  查询。
- `packages/web/src/components/actions/LegacyRecordPanel.tsx`（新增）：旧记录
  的行内写入口，复用 `ActionEditorPage` / `GroupEditorPage` /
  `MappedActionPanel`；preserved 记录显示原因并阻断编辑；沿用 registry
  `expectedRevision`、`requestKey`、409/网络不明只查询不重放。
- `packages/web/src/components/actions/ReferenceUpdates.tsx`：新增可选
  `initialGroupId` / `manageGroups`（默认行为不变），使组详情能深链到本组升级
  范围而不重复渲染草稿编辑器。
- `packages/web/src/views/ActionsView.tsx` 未改：`LegacyActionsView` 仍导出且
  被 3 个既有测试覆盖；它不再由 Actions 路由挂载，但每条旧记录的操作都通过
  `LegacyRecordPanel` 行内可达（编辑/复制/启停/删除/构建/绑定）。

保留的语义（未因美化删减）：权限与 capability 判定仍全部由既有路由承担；
CAS（`expectedRevision` / `expectedStateRevision` / `expectedPluginRevision`）、
`requestKey`、unknown 只查询不重放、Worker pin 不被改写、release 不可变、
旧来源字节不变。

## 4. 验证

- `npx tsc -b --noEmit`：通过（0 错）。
- 聚焦 Vitest：`unifiedModel.test.ts`(9) + `UnifiedActionsView.test.tsx`(10) +
  `UnifiedDirectoryGroups.test.tsx`(6) = **25 passed**。
- 完整 Vitest：**238 files / 2742 passed / 3 skipped / 0 failed**。
- `pnpm build`：TypeScript + Vite + 压缩资源校验通过，
  **90 个 production assets**。
- 正常 hook：`core.hooksPath=scripts`，提交时 pre-commit 对
  `packages/web/src/` 变更执行 `tsc -b --noEmit` 并通过。
- 隔离真实 HTTP + production Chromium（`packages/web/e2e/action-group-unified-server.py`
  + `action-group-unified.e2e.mjs`，随机未保护端口、自有 runtime、真实
  FastAPI、真实 `/react/` 资产、无前端 mock）：
  - `--phase=after`（本分支）：13 checks 全过，见
    [after-evidence.json](../../evidence/action-group-ui-unified-20261108/after-evidence.json)
    与 `after-*.png`。要点：单一导航无整页切换；多版本单条目（v1/v2 切换零写
    请求）；Groups 同列并显示 `action-group-*` 与 release；固定版本 select 值
    `1`；三种 MCP 模式可见且在对话框内（`modeCards`）；绑定 PATCH 体
    `pluginBindings:[{plugin_id:'action-group-uni-tools',enabled:true,mode:'gateway'}]`
    + `expectedPluginRevision:0`；旧来源 `registry.json` 字节不变。
  - 失败负控：浏览器打开组详情（revision 2）后，从外部用真实 API 把组改到
    revision 3，再点“保存草稿”——只发出 **1 次** PUT，返回
    `版本冲突… [revision_conflict]`，没有 `回执 completed`，也没有重试/重放。
  - `--phase=before`（临时基线 worktree，验证后已删除；复现：
    `git worktree add --detach <path> 0ba70483` → 在 `<path>/packages/web` 执行
    `pnpm install --offline --frozen-lockfile && pnpm build`，再以
    `PAN_E2E_SOURCE_ROOT=<path>` 跑本脚本）：同一 fixture 下确认基线只有整页
    `统一目录 ⇄ 旧定义与 Session 绑定` 切换、canonical 侧没有 Groups 入口，
    Group 需切到旧系统才可见，见
    [before-evidence.json](../../evidence/action-group-ui-unified-20261108/before-evidence.json)
    与 `before-*.png`。
  - 视口 1280 / 390 / 320：三条流程（Action 列表、Group 详情、绑定对话框）
    均 `scrollWidth <= innerWidth`，无横向溢出；截图见
    `after-actions-{1280,390,320}.png`、`after-group-{1280,390,320}.png`、
    `after-session-dialog-{1280,390,320}.png`。

## 5. 未验证 / 未声称

- 缺口 A（历史 release 列表）与缺口 B（`legacy_action_binding` 读取面）未实现，
  因此“历史 release 易发现”与“采纳型别名自动合并为一行”**未达成**；
  也不声称已用前端绕过后端限制。
- 浏览器证据里没有“旧记录（legacy-only）”行：owner epoch fence 在 cutover 后
  拒绝新的 legacy 写入（`packages/jobs/runtime_owner.py:170 legacy_pass` →
  `_check` 抛 `revision_conflict`），这是产品既有行为，不是本片缺陷。该状态由
  单测覆盖（`unifiedModel.test.ts`、`UnifiedDirectoryGroups.test.tsx`）。
- 未验证：远端 CI、Linux/macOS、真实 data/profile 迁移、真实模型 CLI/外部
  provider、部署与人工验收。
- `LegacyActionsView` 仍保留但不再由该路由挂载；是否删除该组件留待 MA/用户决定。
