# R6-G：删除 scheduler engine 与 cron shim

- 工作树：`D:/project/pan-worktrees/job-p19-r6-engine-cron-20261025`
- 分支：`r5/p19-r6-engine-cron-20261025`
- 基线：`8703526392bbab655020cb69fba9b3a2ecec3a43`
- 结论：两文件已删除，仓库内生产 import 为 0；形成隔离候选，未推进其他分支、未 push、未操作部署服务或 8767/8768。

## 删除与替代

| 对象 | 结果与替代证据 |
|---|---|
| `packages/scheduler/engine.py` | 删除。基线仅剩 config/status/preview 与 stop/drain 空桩；启动、tick、dispatch 实现在前序 R6-A 已删除，本次不重复宣称删除这些实现。 |
| API preview | `scheduler/api.py::_preview_next` 使用 canonical Creator 的 `TimeProvider.validate_config/next_after`，只转换历史 schedule、计算并排序/截断结果；不创建 Creator、不推进 cursor、不发布 Event。once/interval 保留本地 instant 约定；无时区 cron 保留本地朴素墙钟；显式时区走 canonical zone/DST 规则，非法或不可用时区明确失败。 |
| API status | 读取当前 app 绑定的 `FinalService.task/closed` 与 `CreatorRuntime` 实测扫描计数/时间；字段仍为 running/tickSec/dueScanned/lastTickAt。tickSec 对应 canonical loop 的 0.25s，未绑定返回 engine_not_running；不借 legacy recovery-loop 推断健康。新增计数仅属于运行期观测，不修改任何 pending/publication/cursor。 |
| server shutdown | 移除 engine import 与无效 stop_loop 调用；复用原有 `_owned_lifespan` finally 中的 `await final_jobs.close()`，没有增加第二次 close 或新生命周期循环。 |
| `packages/scheduler/cron.py` | 删除 shim。API parse_cron 与原 cron/store 测试直接导入 `packages.jobs.cron`。cron/calendar 原测试断言保持，仅改 import/模块说明。 |
| 三个保留模块 | `store.py`、`time_events.py`、`legacy_bridge.py` 对基线 diff 为 0。 |

旧 engine/dispatch/integration 中依赖已退役执行宿主的正例转为模块不可导入、HTTP 410、零 Worker/Creator 写入负控。canonical Creator 与 migration 套件继续覆盖时间计算、DST、misfire、pause、pending、publication、owner fence、恢复及幂等；preview 增加与 canonical cron 的精确结果对照、全局排序/截断、禁用任务、缺失任务、非法规则/时区和只读断言；status 校验真实 Task 存活/终止与累计扫描指标。

## 删除门禁与快照

- 删除前全仓扫描 6,340 个文本文件，生产对 engine/cron 的静态 import 均为 0，随后才删除。
- 删除后扫描 6,337 个文本文件，Python AST parse errors = 0，包含测试在内两模块静态 import = 0。
- 人工检查 30 处 Python 动态加载调用和 196 处 JS/TS 动态加载候选；生产加载器不选择两模块。保留的测试动态 import 是明确的 ModuleNotFoundError 负控；审计器测试中的 import 字符串属于合成输入。归档导入清单按现存文件探测。
- 扫描包含生产源码、API/server、tests、scripts、文档/manifest 与历史审计文本；既有 r6c/r6d 以及本次 r6g 生成证据目录排除，避免证据递归计入自身。非字面动态加载逐项审阅，不把扫描解释为外部插件/部署零调用证明。
- 删除前后 scheduler 路由 **15**、MCP 工具 **118**，快照逐字节一致；生产路由/工具 fixture 测试亦通过。
- 汇总：[deletion-gate.json](../../evidence/r6g-engine-cron/deletion-gate.json)；全量索引：[after-references.json](../../evidence/r6g-engine-cron/after-references.json)；快照：[before](../../evidence/r6g-engine-cron/before-catalog.json)、[after](../../evidence/r6g-engine-cron/after-catalog.json)。

## 验证与红证据

所有 Python 测试均经 `python scripts/run_tests_isolated.py -- ...`，使用测试临时根。HTTP 路由使用无 lifespan 的 ASGI/TestClient；MCP stdio 与 canonical client 套件另含自有临时传输/TCP fixture，不连接部署服务。

| 层 | 结果 | 证据 |
|---|---|---|
| 核心 canonical/retired/cron/preview/status | **197 passed，0 skipped，exit 0** | [core-tests.xml](../../evidence/r6g-engine-cron/core-tests.xml)、[log](../../evidence/r6g-engine-cron/core-tests.log) |
| scheduler API/MCP/host/history/store/time-events/legacy-bridge + canonical runtime/clients | **306 passed，1 failed，0 skipped，pytest exit 1** | [surface-tests.xml](../../evidence/r6g-engine-cron/surface-tests.xml)、[log](../../evidence/r6g-engine-cron/surface-tests.log) |
| 基线复测同一失败 | **1 failed，exit 1** | [baseline-legacy-bridge.xml](../../evidence/r6g-engine-cron/baseline-legacy-bridge.xml)、[log](../../evidence/r6g-engine-cron/baseline-legacy-bridge.log) |
| React router retirement / canonical service / canonical view | **41 passed，3 files，exit 0** | [react-tests.log](../../evidence/r6g-engine-cron/react-tests.log) |
| 全仓 tests 收集 | **7,115 collected，exit 0** | [collection.log](../../evidence/r6g-engine-cron/collection.log) |
| 全生产模块 import | **300 passed，0 failed，34 launch/test files excluded，exit 0** | [imports.json](../../evidence/r6g-engine-cron/imports.json)、[probe](../../evidence/r6g-engine-cron/import_smoke.py) |
| diff / 保留模块 | `git diff --check` 通过；三保留文件无修改 | deletion-gate.json |

唯一业务测试红项：`test_scheduler_legacy_bridge.py::test_run_now_does_not_move_periodic_next_fire` 在 patch 已退役的 `system_actions.execute` 时 AttributeError。测试和被 patch 文件均与基线相同；通过 `git archive 87035263 packages tests scripts pytest.ini` 导出临时基线并在其 cwd 用隔离 runner 独立复现同一错误。未修改断言、未加 skip、未扩展本任务到 bridge/system_actions。

最初全仓收集缺 Python Playwright（4 文件），全模块导入缺可选 nonebot（7 模块）；在独立临时 venv（system-site-packages）安装 Playwright、QQ 声明的 nonebot 依赖后重跑上述检查，未修改仓库依赖文件。初始红证据保存为 collection-before-deps / imports-before-deps。新增生命周期 AST 检查曾选中外层 lifespan，修正为实际持有 finally 的 `_owned_lifespan` 后通过，首次失败 XML 亦保留。

核心命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_engine.py tests/test_scheduler_api.py tests/test_scheduler_cron.py tests/test_scheduler_canonical_migration.py tests/test_scheduler_dispatch.py tests/test_scheduler_integration.py tests/test_job_final_creators.py tests/test_r6_legacy_leaves.py -q --tb=short
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_mcp.py tests/test_scheduler_surface_retirement.py tests/test_scheduler_host_retirement.py tests/test_scheduler_history_read.py tests/test_scheduler_store.py tests/test_scheduler_time_events.py tests/test_scheduler_legacy_bridge.py tests/test_job_final_runtime.py tests/test_job_final_clients.py -q --tb=short
pnpm --dir packages/web exec vitest run src/router.retirement.test.tsx src/services/canonicalJobs.test.ts src/views/CanonicalJobsView.test.tsx
python scripts/audit_scheduler_surface.py evidence/r6g-engine-cron/after-references.json
python scripts/run_tests_isolated.py -- python scripts/audit_scheduler_surface.py evidence/r6g-engine-cron/after-catalog.json --snapshot
```

## 未测边界

未执行全量后端测试、React build（无 React 源码修改）、完整 app lifespan、真实浏览器 E2E、真实用户数据迁移、外部插件/部署兼容、远端 CI、Linux/macOS 门禁、部署或人工验收。全模块导入禁止 socket bind/connect、subprocess 与临时目录外写入，不能替代真实服务启动验收。保留基线红项意味着定向扩大矩阵仍为红，候选不等于整体集成验收通过。
