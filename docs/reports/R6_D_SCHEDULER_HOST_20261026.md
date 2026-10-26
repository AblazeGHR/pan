# R6-D scheduler 宿主删除门禁与客户端清理

工作树 `D:/project/pan-worktrees/job-p16-r6-scheduler-host-20261025`，分支
`r5/p16-r6-scheduler-host-20261025`，起始 HEAD
`81ad358d8cf26465d405ddd29827c1e8d38387b5`，起始工作区干净。

**结论：部分完成；宿主整体删除停止。** 已删除无外部调用的 React
`scheduleStore.ts` 和十个旧 task transport。S04/S05/S06/E06 其余项未满足
零引用、历史替代和 owner 接管门禁，保持原实现，不宣称 R6-D 验收通过。
没有 push、服务启停、8767/8768 操作、全仓 R6 组合或发布包构建。

## 删除与保留

| 对象 | 本次动作 | 证据与后续条件 |
|---|---|---|
| `packages/web/src/stores/scheduleStore.ts` | 删除 | 全仓扫描没有该 store 的运行时外部调用；十个 transport 的调用闭包仅在此 store 内，无需替换活 UI |
| `packages/web/src/services/api.ts` 十个旧 task exports | 删除 | fetch/create/update/delete/pause/resume/run-now/runs/next/status 全部移除，清理十二个对应 type imports；保留 Job 读取仍用的错误包络 helper |
| S04 `scheduler/store.py` | 保留，停止整删 | `final_service.bind` 仍用 `data_root`；`final_runtime/coordinator.py` handoff 根解析、`jobs/api.py` 历史/retention、quota 配置 `_job_from_payload` 和旧 Action Runtime calculator 仍有调用；list/get/runs 尚走此 store |
| S05 `scheduler/engine.py` | 保留，停止整删 | R6-A 已移除 startup/tick/run-now 的实现；当前 `web/server.py` 仍 import 并在 shutdown 调 `stop_loop`，`scheduler/api.py` 调 config/status/preview；不能报告本次删除了已不存在的 loop |
| `scheduler/time_events.py` Sources/Runtime | 保留，停止整删 | `automation/service.py` 与 `job_links.py` 仍 import Event 类型/schema；`event_api.py` 仍用 Sources 读取。历史 snapshot/cursor/pending/link 的已验收迁移测试也仍依赖它；未把其余写方法当成安全只读模块 |
| S06 `scheduler/legacy_bridge.py` | 保留，停止整删 | 未发现生产 Python 静态 import，但既有 migration/bridge 测试仍 import；内部仍有 projection/write 路径。没有本次真实 profile 的末次 cursor/receipt/owner 清零证据，不按“生产未挂载”整删 |
| `scheduler/cron.py` shim | 保留，停止整删 | `scheduler/api.py` 和四个既有测试模块仍 import；canonical `providers.py` 的独立 calculator 已存在，但不等于旧调用方已归零 |
| E06 HTTP router | 保留 15 条路由 | 六条 GET 历史/preview/status/source 查询，九条写地址保持认证后 410 retired；owner-switch 继续 404。route snapshot 与 R6-C 完全相同 |
| MCP scheduler 工具 | 保留三个只读查询 | `scheduler_list/get/runs` 仍调用旧历史 HTTP；六个写工具继续从 tools/list 与 call 中移除。完整工具快照为 118 项 |
| `AutomationView → automationWorkspace/EventRegister` | 保留并报告 | workspace 仍 GET `scheduler/event-sources`，EventRegister 仍 POST/PUT/DELETE 该路径。写请求已在公共 API 边界被 410 拒绝；本次未把旧 source ID/schema/referencesVersion 强转成 Creator ID/契约 |
| canonical Jobs/Triggers/Creators、pins/retention/handoff、independent、owner/fence、Event/Run 历史 | 保留 | 本次无后端产品实现修改；canonical 与 owner 定向用例通过不代表这些能力的全仓或真实部署组合验收 |

十个删除的 export 名称及逐文件 hash 见
[manifest](../../evidence/r6d-scheduler-host/manifest.json)。旧 Scheduler 类型声明暂留，
共享 schedule 编辑与预览组件未改。没有按文件名批量删除 `packages/scheduler/`。

## 替代检查与停止理由

1. **时间计算已有 canonical 实现，但调用迁移未完成。**
   [providers.py](../../packages/scheduler/providers.py) 使用 Decimal/显式 UTC、IANA zone 和
   DST policy；本次跑过 Creator 及 canonical migration 的秒、分数、fold/gap、固定偏移、
   unavailable zone、并发 occurrence/restart/pending/fence 测试。旧 `_spec_to_entry`、
   `_entries_next_fire`、`_job_from_payload` 的使用者仍在，不能据此删 store calculator。
2. **已接受的历史函数尚未替代公开查询。**
   [migration_history.py](../../packages/jobs/migration_history.py) 有
   `read_original/read_receipt/read_log`，runtime activation 安装审计行；当前 HTTP/MCP
   scheduler list/get/runs 没有调用这些函数。
   [P9D 报告](R5_P9D_USER_AUTHORIZED_IMPORT_20261026.md) 的“R6 可执行与不可执行”也明确要求
   保留 get/list、raw logs、runs.jsonl，并指出 HTTP/React 历史替代尚未接入验收。
   Creator 的 `legacy_history(request_key)` 只读保存 source snapshot，不能冒充 task/run list。
3. **owner 的单测证据与真实清零证据分开。** 本次验证临时数据上的 fence 与写拒绝，
   不读取真实 profile，不生成 oldOwner/activeLegacy/unknownOwnership 的全实例零计数，
   不处理末次 projection、在途 owner-switch 或通知 receipt。相关删除停止。

## 引用审计与快照

[全仓反向引用](../../evidence/r6d-scheduler-host/reverse-references.json) 扫描 git tracked
及 unignored 文本，包含 scripts、tests、前端、文档和 manifest；列出全部 Python 动态
`import_module/__import__` 与词法 JS/TS `import()/require()` 候选。新 `hostImports`
解析显式及相对 Python imports，包括函数内部懒加载；Python AST parse errors 为零。
五个目标模块仍有 57 条静态 import：store 33、engine 7、time_events 9、bridge 3、cron 5。
这些包含测试、内部引用及 harness，不冒充 57 个生产调用点。

动态 import 列表保留原调用表达式供复核；可计算模块名、字符串拼装和外部 plugin 的
运行时加载不能由静态扫描证明为零。本次没有全仓零调用声明。代码、保留的契约测试与
历史文档中的名称分别理解，不能将审计/负控里的旧名称作为生产调用。

[route/tools 快照](../../evidence/r6d-scheduler-host/routes-tools.json) 通过生产 app import
和 MCP tool registry 生成，未进入 lifespan。与
`tests/fixtures/scheduler_surface_catalog.json` 一致，因此不改写基线 fixture 或旧 R5/R6
规划 manifest；本次增量 manifest 单独记录 blocked/removed。除客户端模块外，不伪造 route/tool 退场。

## 验证

- `pnpm install --frozen-lockfile`：exit 0，独立 worktree 安装依赖；lockfile 无变更。
- `pnpm exec tsc -b --pretty false`：exit 0，仅 TypeScript 检查，不运行 Vite/发布包构建。
- `pnpm exec vitest run src/services/schedulerRetirement.test.ts src/router.retirement.test.tsx src/views/AutomationWorkspace.test.tsx src/views/CanonicalJobsView.test.tsx`：4 文件、32 passed，exit 0。
- `python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_host_retirement.py tests/test_scheduler_api.py tests/test_scheduler_mcp.py tests/test_scheduler_surface_retirement.py tests/test_scheduler_canonical_migration.py tests/test_job_final_creators.py tests/test_job_runtime_owner.py -o addopts= -q --junitxml=evidence/r6d-scheduler-host/pytest.xml`：157 passed、0 skipped、0 failures/errors，exit 0；保留 FastAPI/Starlette 与 Pydantic 的两条依赖 warning。
- 新增 29 条后端用例：九个写地址 × 匿名/user/delegated 三种主体共 27 条生产 ASGI 负控；阻止旧 store 写、Sources/Bridge/canonical service/Creator/independent/owner 初始化，检查临时树 bytes 不变；另有客户端零生产引用、scanner 的显式/相对/动态 import 与 parse error 测试。
- 既有定向用例包含 real MCP stdio 的 core/native tools/list 与未知工具拒绝；ASGI 调用未开 HTTP listener，未执行应用 lifespan；server 和兼容调用方 import 通过。
- `python scripts/audit_scheduler_surface.py evidence/r6d-scheduler-host/reverse-references.json`、隔离 runner 下的 `--snapshot`、`git diff --check`：exit 0。

日志/JUnit 位于 [evidence/r6d-scheduler-host](../../evidence/r6d-scheduler-host)。
未验证真实 HTTP socket/浏览器、完整 startup/shutdown、三平台/远端 CI、真实数据迁移、
部署、服务重启、人工验收或全仓 R6 组合；未运行发布包 build。

后续解除阻塞需先提供并验收历史 list/get/runs 的授权客户端替代，再迁移残存路径解析、
quota 与 calculator 调用，确认 source/cursor/receipt 和 owner 门禁；之后重新跑全仓引用，
逐项删除，不能把本候选当成宿主已退场。
