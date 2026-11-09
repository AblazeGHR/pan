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

1. **Jobs：设计页、四 kind、真实运行/日志、取消与权限/owner 负控全部完成**
   - `packages/jobs/legacy_job_compat.py` 仍是投影边界（不是第二运行时）：
     旧创建体映射为普通 canonical Job（`product_process` /
     `product_session_message_v2` / `product_session_broadcast_v2`），并反向投影旧形状。
   - 静态兼容路由（注册在动态 `/jobs/{id}` 之前）：`legacy-create`、`legacy-list`、
     `legacy/{id}`、`run-now`、`legacy/{id}/pin`、`DELETE /jobs/{id}`、`/cancel`。
   - **创建即启动**（background-process）：`write → enable → create_run`，每步独立
     receipt 与派生 key；响应丢失/冷服务同 key 只回放原 Job/Run；start 被拒返回
     `phase: enabled` 与 `start.code`，无 Run，不谎报已执行。
   - **取消**走 `control_run_postcommit`（真实 owned-tree cleanup）；此前直接调用
     `control_run` 只写 cancelling intent，Run 会永久停在 cancelling，这是本轮修的
     真实缺陷。取消批次逐 Run 记录，失败返回 409 + `cancelled[]`/`failed[]`。
   - **状态投影**：background-process 显示最后 Run 的真实状态
     （running/completed/cancelled），不再固定 `scheduled`；行内按钮按原规则显示。
   - **详情抽屉日志**：每条 canonical Run 新增 `View output`，读取
     `/api/job-runs/{id}/logs` 与原始 attempt 日志，显示真实 stdout/stderr 正文；
     不是状态摘要。浏览器断言 `owned stdout` / `owned stderr`。
   - **真实取消长进程**：UI 行内 `Cancel job` → Run `cancelled`；抽屉输出保留已写
     markers 且没有完成 marker；`verify_job_execution.py` 读 owned execution 文档：
     `outcome=rejected`、`cleanupProof.ownedTree.confirmed=true`、`remaining=[]`、
     supervisor exit `confirmed(pid_absent)`、所有记录 PID 不在。
   - **权限/owner 负控**（`tests/test_legacy_job_compat.py`）：
     agent 撤权后 create/start/cancel/日志分别 `authorization_denied`；执行被撤时
     创建返回 `enabled` 且不启动；owner epoch 被另一 owner 推进后 create/start/cancel
     均 `revision_conflict`，Run 保持原状，只读列表/日志发现仍可用（owner fence 是
     writer 排除，不是读权限）。
   - **验证**：后端组合 **65 passed**（jobs-compat 14 + owned executor 6 +
     owned broadcast 6 + definition-preview 5 + directory-reads 7 + scheduler-api 27）；
     前端 **239 files / 2749 passed / 3 skipped**；`tsc`/build 通过。
   - **浏览器**（隔离真实 HTTP + 生产 dist + Chromium）：Jobs **9 checks**、四 kind
     全部经 `New Job` 创建、编辑/Pin/删除、真实日志、真实取消与整树证明；
     Actions/Groups 既有 e2e 仍 `passed`（17 checks）。证据：
     `evidence/jobs-clickthrough-20261109/`（含 `process-output.png` /
     `cancelled-output.png`、历史 partial/claimed-passed JSON 与 `CORRECTIONS.md`）。
   - 未覆盖：远端 CI、Linux/macOS、真实 data/profile 迁移、真实模型 CLI、部署；
     未知 effect 的 no-replay 由 Codex owned harness 的既有用例覆盖（不在本片重复）。
2. ~~旧 `job_template` 只能指向旧模板~~：已支持 canonical `template_*`，保留 `jtpl_*`。
3. ~~canonical-only 只有 JSON 编辑~~：已做旧形状可表达类型的反向投影；不可表达
   类型保留完整定义在显式高级区。
4. 未验证：远端 CI、Linux/macOS、真实 data/profile 迁移、真实模型 CLI、部署。
