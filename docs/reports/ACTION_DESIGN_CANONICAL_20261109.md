# 设计页接入 canonical（20261109）

基线 `main 99890a0c32890977e31ca0523ea3262dc3828542`；独立 worktree
`D:/project/pan-worktrees/action-group-design-canonical-20261109`，分支
`feature/action-group-design-canonical-20261109`。未改 `main` / `practical`、真实
data/profile、8767/8768；未 push。

## 1. 页面基线

- `/react/actions` 恢复为 practical 的完整设计页（`ActionsView` → `LegacyActionsView`）：
  同样的标题、两个 tab、表格列、行菜单、全页编辑器与右侧栏。
- canonical 只作为**页面内的增量**出现：Actions/Groups 各新增一个「统一目录条目」
  区块，点击进入版本 / 发布 / 固定引用 / release 明细；没有第二个页面、没有整页切换。
- 系统产品 Action 不出现在该区块（产品维护，不提供用户编辑入口）。

## 2. 旧 → 新逐操作映射

| 设计页操作 | 原实现 | 现在 | 说明 |
| --- | --- | --- | --- |
| Actions 列表 | `GET /api/actions`（旧投影） | 旧投影 + `GET /api/jobs/actions` 增量 | 旧记录仍在原表格；canonical-only 记录在新区块 |
| Groups 列表 | `GET /api/actions` 的 groups | 同上 + `GET /api/action-groups` | 固定版本/release 在新区块 |
| 新建 Action（表单） | `POST /api/actions/actions` | `POST /api/jobs/actions/definition-preview` → `POST /api/jobs/actions`（+ `publish`） | 表单字段/校验完全复用；不写 JSON；草稿/发布由 `enabled` 决定 |
| 编辑旧 Action | `PUT /api/actions/actions/{id}` | 不变（兼容适配器在已采纳时落 canonical） | 未采纳的旧记录不接管写入权威 |
| 编辑 canonical 记录 | 无 | `PUT /api/jobs/actions/{id}`（区块内定义编辑器） | 带 `expectedRevision` CAS |
| 版本列表/切换 | 无 | `GET /api/jobs/actions/{id}/versions` | 切换只读，不发写请求 |
| 发布/启用/停用 | 旧 registry lifecycle | `POST /api/jobs/actions/{id}/operations` | `expectedRevision` + `expectedStateRevision` |
| 调用固定版本 | 无 | `POST /api/jobs/actions/{id}/invocations` | `actionRef` + `requestKey`；unknown 只查询不重放 |
| Group 固定版本 | 无 | `PUT /api/action-groups/{id}` | 工具固定 `{id,revision,digest}`，仅可选已发布启用版本 |
| Group build/unbuild/删除 | 旧 `actions/groups/{id}/build` | canonical `POST /{id}/build`、`/{id}/unbuild`、`DELETE` | CAS + requestKey；运行中 Session 阻断 unbuild |
| release 查看/历史 | 无 | `GET /releases/{version}`、`GET /{id}/releases` | 不可变；历史新到旧、可打开旧版本 |
| 引用升级 | 旧 `preview-reference-update` | 不变（canonical Group 作用域） | 预览摘要 + 显式 targets |
| Session 绑定 | `UseInSessionDialog`（旧 Group） | 同一对话框 + `PATCH /api/sessions/{id}` | 三种 MCP 模式 + `expectedPluginRevision` |
| 采纳状态 | `canonicalActions` / `preservedActions` | 同上 + `legacy-action-bindings` | 仅按显式 `legacyId/actionId` 合并，禁止名称/顺序推断 |

## 3. 后端

- 新增**只读** `POST /api/jobs/actions/definition-preview`：把设计表单的旧形状草稿
  交给既有采纳映射 `group_migration.command_definition` 得到 canonical 定义；不落库、
  不发布、不执行。权限 `action:read`；`invalid_schema` 400、模板引用 409/404。
- 复用既有 `GET /api/action-groups/{id}/releases` 与
  `GET /api/action-groups/legacy-action-bindings`（`634b0428` 已合入 main）。
- 单一 `FinalService` / owner fence / receipt 未变；没有第二执行引擎。

## 4. 验证

- 后端聚焦：`tests/test_action_definition_preview.py`(5) +
  `tests/test_action_group_directory_reads.py`(7) = **12 passed**。
- 前端：`tsc -b --noEmit` 通过；聚焦 **18 passed**（设计页 16 + canonical 区块 2）；
  完整 **239 files / 2748 passed / 3 skipped / 0 failed**。
- `pnpm build`：`Verified 90 compressed production assets`。
- 隔离真实 HTTP + production Chromium（`e2e/action-design-canonical.e2e.mjs`，
  fixture `e2e/action-group-unified-server.py`）：7 checks 通过——设计页即页面、
  canonical 增量可见、单条目两版本只读切换、release 历史可打开旧版本、三种 MCP
  模式与绑定 CAS、外部改版后保存草稿得到 `revision_conflict`、1280/390/320 无横向溢出。
  证据：`evidence/action-design-canonical-20261109/`。

## 5. 未完成 / 需集中报告（不阻塞以上范围）

1. **Jobs 设计页未移植**：practical `JobsView.tsx`（1512 行）依赖
   `POST/PATCH /api/jobs`、`/run-now`、`/pin` 等旧调度写入口；main 已把这些写入
   退役（410）并改为 canonical job/operations。要复用该设计页需要一层 Job 兼容
   门面（仍落同一 FinalService/owner/receipt），属于后端能力保全缺口，未在本片
   自行决定。
2. **旧 `job_template` 快创只能指向旧模板**：canonical 新建的模板（`template_*`）
   无法作为旧 `job_template` 目标（采纳映射要求 `jtpl_*`）。
3. **canonical-only 记录的结构化编辑**：只能走区块内的定义编辑器（JSON）；旧表单
   只覆盖旧形状草稿，未做 canonical→表单的反向投影。
4. 未验证：远端 CI、Linux/macOS、真实 data/profile 迁移、真实模型 CLI、部署。
