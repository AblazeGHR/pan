# 设计页接入 canonical（20261109）

基线 `main 99890a0c32890977e31ca0523ea3262dc3828542`；独立 worktree
`D:/project/pan-worktrees/action-group-design-canonical-20261109`，分支
`feature/action-group-design-canonical-20261109`。未改 `main` / `practical`、真实
data/profile、8767/8768；未 push。

## 1. 页面基线

- `/react/actions` 是 practical 的完整设计页（`ActionsView` → `LegacyActionsView`）：
  同样的标题、两个 tab、表格列、行菜单、全页编辑器与右侧栏。
- canonical 记录**并入同一张表**（不再有并行第二列表）：按显式 identity 合并，
  只有没有旧身份的 canonical 记录才追加为同网格行；系统产品 Action 同样在表中
  可发现、可查看、可按原权限调用，只是不提供编辑。
- 版本 / 发布 / 固定引用 / release / 绑定作为明细页出现，仍是同一页面内的增量。

## 2. 旧 → 新逐操作映射

| 设计页操作 | 原实现 | 现在 | 说明 |
| --- | --- | --- | --- |
| Actions 列表 | `GET /api/actions`（旧投影） | 旧投影 + `GET /api/jobs/actions` 增量 | 旧记录仍在原表格；canonical-only 记录在新区块 |
| Groups 列表 | `GET /api/actions` 的 groups | 同上 + `GET /api/action-groups` | 固定版本/release 在新区块 |
| 新建 Action（表单） | `POST /api/actions/actions` | `POST /api/jobs/actions/definition-preview` → `POST /api/jobs/actions`（+ `publish`） | 表单字段/校验完全复用；不写 JSON；草稿/发布由 `enabled` 决定 |
| 结构化编辑 canonical | 无 | 同一表单反向投影 → `definition-preview` → `PUT /api/jobs/actions/{id}` | 仅对旧形状可表达的类型；其余保留完整定义在显式高级区 |
| 系统 Action 使用 | 旧目录可见 | 同表可见 + 版本/调用；编辑受限 | 产品维护只限制编辑，不隐藏 |
| 编辑旧 Action | `PUT /api/actions/actions/{id}` | 不变（兼容适配器在已采纳时落 canonical） | 未采纳的旧记录不接管写入权威 |
| 编辑 canonical 记录 | 无 | `PUT /api/jobs/actions/{id}`（区块内定义编辑器） | 带 `expectedRevision` CAS |
| 版本列表/切换 | 无 | `GET /api/jobs/actions/{id}/versions` | 切换只读，不发写请求 |
| 发布/启用/停用 | 旧 registry lifecycle | `POST /api/jobs/actions/{id}/operations` | `expectedRevision` + `expectedStateRevision` |
| 调用固定版本 | 无 | `POST /api/jobs/actions/{id}/invocations` | `actionRef` + `requestKey`；unknown 只查询不重放 |
| Group 固定版本 | 无 | `PUT /api/action-groups/{id}` | 工具固定 `{id,revision,digest}`，仅可选已发布启用版本 |
| 旧 `job_template` 快创 | 仅 `jtpl_*` | 接受 `jtpl_*` 与 canonical `template_*`，解析已发布固定引用 | 保留旧 id 兼容；引用仍是显式 `{id,revision,digest}` |
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
  `tests/test_action_group_directory_reads.py`(7) + `test_action_unification_p6_families.py`(23)
  = **35 passed**。
- 前端：`tsc -b --noEmit` 通过；聚焦 **19 passed**；完整
  **239 files / 2749 passed / 3 skipped / 0 failed**。
- `pnpm build`：`Verified 90 compressed production assets`。
- 隔离真实 HTTP + production Chromium（`e2e/action-design-canonical.e2e.mjs`，
  fixture `e2e/action-group-unified-server.py`）：**11 checks** 通过——同一表格承载
  canonical 与系统行、单条目两版本只读切换、**快捷表单新建→canonical 立即可见**、
  **固定版本实际调用**、**表单结构化再编辑（生成新草稿版本）**、**Group 显式 Build
  产生新 release**、release 历史可打开旧版本、三种 MCP 模式与绑定 CAS、外部改版后
  保存草稿得到 `revision_conflict`、1280/390/320 无横向溢出。
  证据：`evidence/action-design-canonical-20261109/`。

## 5. 未完成 / 需集中报告（不阻塞以上范围）

1. **Jobs：后端兼容面已完成并通过隔离 HTTP 验证；页面移植待完成**
   - 新增 `packages/jobs/legacy_job_compat.py`（投影边界，不是第二运行时）：
     把 background-process / session-message / session-broadcast 的旧创建体映射为
     **普通 canonical Job definition**（复用 `product_process` /
     `product_session_message_v2` / `product_session_broadcast_v2` 与 literal 绑定），
     并把 canonical Job 反向投影为旧形状（kind/status/target/text/argv/pin）。
   - 新增 canonical 静态路由（注册在动态 `/jobs/{id}` 之前）：
     `POST /api/jobs/legacy-create`、`GET /api/jobs/legacy-list`、
     `GET|PATCH /api/jobs/legacy/{id}`、`POST /api/jobs/{id}/run-now`、
     `PUT /api/jobs/{id}/pin`、`DELETE /api/jobs/{id}`。
     run-now 走 `FinalService.create_run`（`cause=manual`，不推进 cursor，
     同一 `requestKey` 只回放）；delete 走 `control retire`（CAS）；pin 沿用
     `pins` 侧车；全部经原 `actor` 授权，不回退旧 writer。
   - 隔离 HTTP 测试 `tests/test_legacy_job_compat.py` **3 passed**：
     创建/列表/详情/pin/改名/删除、手动 Run 幂等且不产生 schedule cursor、
     `scheduled-task` 明确 `unsupported_operation`、session-message 创建成功。
   - 页面已移植：`packages/web/src/views/JobsView.tsx` + `services/legacyJobs.ts`，
     `/jobs` 路由指向它；list/detail/create/patch/run/pin/delete 走
     `/api/jobs/legacy-*`、`/run-now`、`/pin`、`/runs`。
   - 浏览器（隔离真实 HTTP + 生产 Chromium）新增 4 checks：旧形状创建/列表不重复、
     同键手动 Run 不产生第二个 Run、活动 Run 阻断 retire 且取消后历史仍可读、
     无活动 Run 的 Job 删除返回 `retired` 且历史仍可读（共 15 checks）。
   - **pin 红已修复**：根因是旧 `pins.set_pin` 走 `legacy_pass`，cutover 后 owner
     fence 拒绝（`Job runtime owner epoch fence rejected writer`）。改为在 canonical
     store 写 `job_pin` 投影（同 owner 事务），浏览器 cookie 断言恢复并通过。
   - 取消改为逐 Run 记录：任一失败返回 `reference_conflict` 且不再报整批成功。
   - 仍未完成：session-message/broadcast 的 `deliveryRuntime` 定时投递、
     `scheduled-task` 的 Creator/Trigger 组合；JobsView 的点击级流程（表单提交/
     运行/日志/pin 菜单）尚未逐项验证；真实执行日志需要可运行 worker 的 fixture。
2. ~~旧 `job_template` 只能指向旧模板~~：已支持 canonical `template_*`，保留 `jtpl_*`。
3. ~~canonical-only 只有 JSON 编辑~~：已做旧形状可表达类型的反向投影；不可表达
   类型保留完整定义在显式高级区。
4. 未验证：远端 CI、Linux/macOS、真实 data/profile 迁移、真实模型 CLI、部署。
