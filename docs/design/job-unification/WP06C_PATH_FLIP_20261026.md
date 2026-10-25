# R3 WP06c canonical 路径翻转与兼容

基线：`111ae6c14c86a83d7d70eaee9dca7b9873288118`。候选仅提交到
`r1/wp06c-path-flip-20261025`，供 MA 快进整合；不推进其它分支，不 push。

## 当前路径

| 资源 | 正式入口 | 兼容入口 |
|---|---|---|
| Job 定义、操作、Run 准入/列表 | `/api/jobs[/{id}[/operations或/runs]]` | 同路径加 `/final`，例如 `/api/final/jobs` |
| Trigger 定义/操作 | `/api/triggers[/{id}[/operations]]` | `/api/final/triggers…` |
| Event Creator 定义/操作 | `/api/event-creators[/{id}[/operations]]` | `/api/final/event-creators…` |
| Job Template 定义/操作 | `/api/job-templates[/{id}[/operations]]` | `/api/final/job-templates…` |
| Run 详情/操作 | `/api/job-runs/{id}[/operations]` | `/api/final/job-runs…` |
| 原操作查询 | `/api/operations[/{id}]`，或 `?operation=…&requestKey=…` | `/api/final/operations…` |
| canonical published Action Ref 目录 | `/api/jobs/actions` | `/api/final/actions` |
| 旧通用 Job facade | `/api/legacy/jobs…` | 不冲突的 `/api/jobs` 具体子路径继续保留 |

所有 `/api/final` 别名在 OpenAPI 中标记 deprecated，绑定同一个 endpoint，
不是重定向，也不重新准入写请求。新生成操作回执的 `links.self` 使用 `/api/operations`；
既有回执不做数据重写。`/api/actions` 继续承载旧 Action registry，故 canonical
只读 Action Ref 目录使用 `/api/jobs/actions`。

旧 facade 的全部 handler 和写方法均重挂到 `/api/legacy/jobs`；原模块 router 保留，
由统一安装入口决定生产路径。旧 `/api/jobs/action-jobs`、`job-templates`、
`action-references`、`kinds`、`templates`、`next`、`settings` 等具体子路径先注册。
原 facade 的 PATCH、pin、run-now 等不冲突写入口也保留原地址。
`/api/jobs` 根、GET/DELETE 动态详情与 GET 动态 runs 由 canonical 接管；旧通用
facade 客户端需使用 `/api/legacy/jobs`。两种格式不按 payload 猜测分流。

## 客户端与授权

React 只迁移旧 facade service 的 URL 前缀，并补上同等 Bearer 注入；Jobs 页面与
旧 Action/Template 客户端不改行为。canonical UI、MCP 和 CLI 客户端使用正式路径；
CLI 子命令仍名为 `final`，参数、工具名、旧工具实现保持兼容。

可信 IPC 的 `object_path` 同时识别正式路径和 final 别名；旧工具白名单保持原状，
不将通用 `/api/legacy/jobs` facade 暴露给 Agent。canonical structured errors 与
未知写入回执在两个路径上保持一致。Cookie 写操作仍要求同 Origin，显式无效
Bearer 仍不回退 Cookie；远程 MCP 不得借用本地 stdio 权限。

## 路由快照与验证

固定快照：[job_path_flip_routes.json](../../../tests/fixtures/job_path_flip_routes.json)。
108 条相关生产路由；按 method 和标准化参数路径计，冲突为 0。
测试展开 FastAPI 实际挂载前缀，核对顺序、每条路由的首个匹配 handler、
31 对新旧 canonical endpoint 身份，以及旧 facade 全部 handler 的重挂载。

最终定向后端命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_path_flip.py tests/test_job_final_transports.py tests/test_job_final_http_service.py tests/test_job_final_clients.py tests/test_jobs_api.py tests/test_jobs_completed_retention.py -q -o addopts= --junitxml=artifacts/wp06c/targeted.xml
```

- 256 passed，0 failed，0 skipped，exit 0。路由整理后重新运行 transports + path-flip：78 passed，exit 0。
- 自有临时根、loopback 随机端口 TCP HTTP：四类定义双向 CRUD/retire、操作回执和
  原 requestKey 查询、跨路径幂等、legacy facade 读/错误、具体旧 Action 路由优先、
  Cookie Origin、无效 Bearer 401。未绑定旧 runtime 的具体路由返回原 handler 的 503，
  不被 canonical ID handler 吞掉。
- 真实 stdio MCP core/native 投影与可信 IPC：四类资源、Run 六操作、operation 查询、
  逐项 grants 403、旧 `authorization_objects` 读回归、92 个旧工具目录不丢失。
- CLI 真实 TCP：四类资源 CRUD/retire、Run 六操作、401、原回执恢复。
- 前端：`pnpm exec vitest run src/services/api.test.ts src/services/canonicalJobs.test.ts src/services/api.legacyJobs.test.ts src/views/CanonicalJobsView.test.tsx src/views/AutomationWorkspace.test.tsx`，88 passed，exit 0。
- `pnpm install --frozen-lockfile` 成功；`pnpm build` 执行 TypeScript、Vite 和压缩资产校验。

## 保留的失败与未测边界

扩大回归 `test_job_action_http.py`：候选 1 failed，旧 Action Job `run-now`
返回 overlap capacity 409；同批其它 254 项通过。未修改断言、增加 skip 或更改业务。
未修改的 `111ae6c1` archive 副本独立跑同一用例两次均失败，但失败点为模板事件
等待超时、`KeyError('actionId')`，与候选不同。基线也红不能证明两个失败同源，
这项旧全链路验收仍未通过。原始 JUnit 留在本机隔离证据目录，入库证据仅保留
测试计数、入口与脱敏错误分类。

未运行完整后端测试集、真实浏览器、三平台远端 CI、真实 profile 数据迁移、
真实 provider/账号、部署或人工验收。未启停用户服务、未操作 8767/8768；
仅运行测试自有的临时 HTTP/IPC/fakeCLI fixture 并由 fixture 清理。
scheduler/automation 业务代码、旧 handler 与 R5 旧写入口未删改。
