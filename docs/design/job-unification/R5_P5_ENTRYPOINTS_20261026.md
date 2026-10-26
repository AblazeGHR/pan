# R5 P5 HTTP/MCP/CLI 旧写入口退役

基线 `54481fa4`（`integration/job-r1-20261025`），候选分支 `r5/p5-entrypoints-20261025`。
本轮只做旧 Job 写入口退役，不删除 handler 实现（留给 R6），不改 canonical 契约，
不 push、不启停服务、不操作 8767/8768、不动真实 profile/data。

## 1. 退役范围

| 层 | 退役的写入口 | 保留的读/控制 |
|---|---|---:|
| Action Job（`action_api`） | 13 条：create/update/run-now/lifecycle/pause/resume/delete/retry/confirm-recovery，Template create/update/delete/instantiate | 6 条：list、runs、schema、Action 引用、**cancel**、Template list |
| Scheduler（`scheduler/api.py`） | 10 条：tasks create/patch/delete/pause/resume/run-now/owner-switch，event-sources create/update/delete | 6 条：tasks list/详情/runs、next、status、event-sources list |
| 旧 Job facade（`jobs/api.py`） | 全部写方法（login 后的 POST/PUT/PATCH/DELETE），含 `settings/completed-retention`、`pin`、`templates` | 全部 GET；`/api/jobs/{id}/runs` |
| 持久 Job（`web/server.py`） | 4 条：`POST /api/background-jobs`、`POST /api/session-message-jobs`、`PATCH /api/background-jobs/{id}`、`POST /api/background-jobs/{id}/retry` | GET list/详情、**`POST /api/background-jobs/{id}/cancel`** |
| MCP 工具 | 10 个：`agent_background_start/retry`、`agent_message_job_create/update`、`scheduler_create/update/delete/pause/resume/run_now` | 9 个：`agent_background_get/list/cancel`、`agent_message_job_get/list/cancel`、`scheduler_list/get/runs` |

保留 `cancel` 与全部历史读，因为设计 §6 要求「活资源必须仍可查询与取消」：
删掉取消会让旧 Runner 进程变孤儿，删掉读会让审计看不到既有记录。

## 2. 边界语义

1. **先鉴权，再拒绝。** 旧写路径统一返回 410 `{"code":"retired"}`，但鉴权在 410
   之前执行：匿名调用者得到原来的 401/503，无法用 410 探测哪条旧路径还存在。
2. **拒绝发生在任何副作用之前。** 410 在进入 runtime / store / process / 重投之前抛出；
   `test_job_final_retirement` 断言调用前后 Job 与模板集合不变。
3. **canonical 优先。** `/api/jobs`、`/api/jobs/{id}`、`POST /api/jobs`（Run 准入）
   等 canonical 地址完全不受影响；旧写只有在 canonical 不占用该地址时才注册 410，
   不产生同 method+path 重复路由（快照冲突为 0）。
4. **`/api/legacy/jobs` 是唯一 facade 地址。** 读仍在原地址，写只在该前缀上给出 410。
5. **MCP `tools/list` 与 `tools/call` 一致。** 退役工具用 `remove_tool` 撤出目录，
   直接按名调用得到 unknown tool 错误，而不是旧行为。

## 3. 与设计/计划的偏差（需 MA 知悉）

- **CLI 未退役。** `pan start/exit/restart/show/hide` 原实现全部保留（E14 的
  「迁到固定系统 Action」属 P5b）。理由：`pan final` 只操作 Job 对象，不提供
  host 启动/停止能力；`packages/core/platform/` 没有服务安装单元。若本轮退役
  CLI，唯一的合法停机路径与 `scripts/stop.bat`、`restart.bat` 会同时失效。
  **部署停机路径保留，未迁移。**
- **`object_path` 授权桥未收紧。** 保持原 allowlist：收紧会让
  `test_authorization_mcp` 的 27 项能力映射契约失效，而该契约是本轮范围外的
  授权语义。已实测：即使委托调用者能"寻址"旧路径，四条旧写路由都返回 410，
  可信桥下无法产生写效果（`/api/automation/triggers` 等未被重挂的旧地址为 404）。
- **旧 handler 只改名不删除。** 写成 `_legacy_*` 并保留函数体，供 R6 整段删除；
  因此 `web/server.py` 里存在当前不可达的函数。
- **`test_jobs_api.py` 的 11 项失败为基线既有**，与本轮无关（同身份、同首行原因，
  已用 stash 对照确认）；本轮未修改该文件。

## 4. 命令与结果

均经 `python scripts/run_tests_isolated.py -- ...` 执行，隔离 profile/端口。

| 命令 | 结果 |
|---|---|
| `pytest tests/test_job_path_flip.py tests/test_job_final_transports.py tests/test_job_final_clients.py tests/test_job_final_http_service.py tests/test_job_final_retirement.py tests/test_legacy_mcp_compat.py tests/test_job_action_templates.py tests/test_scheduler_api.py tests/test_scheduler_mcp.py -q -o addopts=` | **317 passed, exit 0** |
| 上述 + `test_jobs_api.py test_job_system_actions.py test_job_final_{contracts,admission,runtime,executors,creators,migration,migration_apply,independent_execution}.py` | 668 passed / **11 failed**, exit 1；11 项全部是 `test_jobs_api.py` 基线既有失败 |
| 基线对照（同一 11 项，stash 本轮改动） | 11 failed / 81 passed — 同身份 |

## 5. 未验证层

未运行完整 `tests/`、前端构建与 Vitest、真实 HTTP 浏览器、三平台 CI、真实 profile
数据迁移、部署 readback 与人工验收。没有真实调用方迁移确认：旧 MCP 工具的 10 个
消费者是否已切到 canonical，需 R5-B 用真实 `tools/list` 与调用方清单核对。

## 6. 测试改动

- 新增 `tests/test_job_final_retirement.py`（78 项）：31 条旧写矩阵（无业务 handler
  可达、匿名 401/503 不泄露、委托调用者 410 且零副作用）、7 条保留读、MCP 目录
  精确集合、CLI 保持可用、canonical 面不变。
- `test_job_path_flip.py`：facade 写断言由「旧 handler 保留」改为「读/cancel 保留、
  写为 410」；`POST /api/legacy/jobs` 由 `invalid_argument` 改为 410；重录路由快照
  （108 → 127 条，冲突 0）。
- `test_job_final_transports.py`、`test_job_final_http_service.py`：facade 行为断言
  改为「读保持、写 410；匿名 401」。
- `test_legacy_mcp_compat.py`、`test_job_action_templates.py`：旧正向流程改为
  「退役 + 零副作用 + 鉴权优先」的后继断言，未删除安全负控。
