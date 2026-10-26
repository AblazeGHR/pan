# R6-I 恢复交付：职责解耦与显式接管候选

- 工作树：`D:/project/pan-worktrees/job-p21-r6-time-events-20261025`
- 分支：`r5/p21-r6-time-events-20261025`
- 原始 baselineSha：`f0970e23df5108eabd7580847346129d1714ba14`
- 本轮实施 baselineSha：`bac726cf84c0510dff8618afb82e227e660f590c`，已从原树 `merge --ff-only`，无冲突。
- sourceSha：`371ef174c3c69542d519ec1dc9f2fa1382c711b0`。随后的证据提交不修改产品/测试源码；最终候选 HEAD 见交付回复。
- 结论：**迁移部分形成可审阅候选；跨 epoch 完整效果验收仍阻塞**。按最新指令保留三个 legacy hosts，没有推进其它分支或真实 profile。

上轮未提交报告与 `evidence/r6i-time-events/` 全部保留；其负控结果描述旧基线，不能作为本轮最终迁移结论。
本轮测试/探针/扫描、初始失败和后续纠正均在 `evidence/r6i-resume/`；摘要为
[validation.json](../../evidence/r6i-resume/validation.json)。该文件包含精确 32 条源码/测试/文档路径、状态和 SHA256、后继红测 node、上轮证据摘要及各矩阵统计。

## 完成内容与 E/S/U 映射

| 对象 / 追踪项 | 结果 | 精确路径与后继证据 |
|---|---|---|
| S04 / U08 时间能力 | 原 11 个函数 AST 与基线逐项相同；normalize、iso、anchor、next-fire、misfire policies 已抽出，无 legacy store/host 依赖 | `jobs/schedule_calculations.py`；`test_scheduler_store`、`test_scheduler_cron`、`test_job_final_creators`、`test_scheduler_canonical_migration`。canonical 分数秒/DST 与 100 occurrences 继续由 TimeProvider 覆盖 |
| S04 路径 | 统一纯路径策略，原环境变量优先级不变；兼容 facade 的 DEFAULT_ROOT hook 保留 | `core/platform/data_paths.py`；`scheduler/history.py`、`jobs/api.py`、`jobs/final_service.py::bind`、`jobs/final_runtime/coordinator.py` handoff 使用；handoff、isolation、canonical HTTP/stdio/CLI 测试 |
| S04 / S08 / quota payload | record 构造/投影、原 taskId 查找、安全删除与 run 证据 helper 独立于 store | `jobs/scheduled_records.py`；`jobs/action_runtime.py::config_job` 改用纯 calculator；`automation/quota_policy.py` 显式传 root；`jobs/api.py` 删除保留原 reservation/protected Action 检查；quota、删除保护、pin/retention 测试 |
| S04 / time_events 补充项 / E12 历史 facade | GET source 不再实例化 legacy Sources；只读 snapshot 带完整 source_links/requests，未知记录原样保留；旧写入口持续认证后 410 | `scheduler/source_history.py`、`scheduler/event_api.py`；`test_source_history_is_read_only_for_missing_and_pending_records`、`test_unrecognized_source_snapshot_is_preserved_without_auto_adoption`、scheduler API/retirement/history 111 原 ID 测试 |
| time_events 补充项 / S02 | 接管复用 P22 explicit adopt/status/settle；新 host port 提供授权、完整 successor mapping、显式首次 publication 与后续 canonical 驱动 | `scheduler/source_migration.py`、`jobs/final_service.py::scheduler_migration/refresh_subscriptions/control/_guard_creator`；`test_explicit_host_adoption_continues_same_grid_and_effect_keys[False-*]` 三断点端到端通过；`test_cold_creator_publication_and_canonical_admission_recover_before_effect_boundary` 三断点冷恢复通过 |
| S02 / S08 隐式调度 hook | 撤除旧 ActionRuntime.tick 中可注入 time_events Runtime 调用；canonical CreatorRuntime 为生产驱动链 | `jobs/action_runtime.py`；`test_legacy_job_pass_never_drives_injected_legacy_time_runtime` |
| automation 来源定义与引用 | v1 event type/schema 移到无 I/O 契约模块；历史 Links 不导入 host | `scheduler/time_contract.py`、`automation/service.py`、`automation/job_links.py`；automation canonical/composition/时间事件回归。公共 schema、协议与 React 源码未更改 |
| S06 / U09 bridge 保留接口 | bridge 只更换纯能力、记录/证据 helper 与路径依赖；原 cursor、receipt、legacy reservation 行为保持 | `scheduler/legacy_bridge.py`。原 `append_run` 后继为 `scheduled_records.append_run`，不能宣称 U09 旧证据写职责已最终退役。bridge 最后一次 cursor/receipt/owner 终态仍由 MA 另验 |
| FD01 import / startup | 包内生产静态旧 host import 归零；字面动态旧 host import 归零；运行期阻止旧 host 加载后启动、重开 canonical host 成功 | `scripts/check_scheduler_dependencies.py`、`test_scheduler_dependency_cutover`；[dependencies-final.json](../../evidence/r6i-resume/dependencies-final.json)、[startup-final.log](../../evidence/r6i-resume/startup-final.log) |

其它变更仅为自有 launcher/fixture 迁移到新路径策略：`web/e2e/job-pin-server.py`、
`scripts/verify_action_groups.py`、`scripts/verify_action_panel_cache.py`、`tests/conftest.py`、
`tests/support/isolated_http_server.py`、`test_jobs_api.py` 与 `test_scheduler_legacy_bridge.py`
根解析 hook；没有降低原断言或增加 skip。平台 README/矩阵文档同步纯路径策略，未新增平台能力。

调用步骤、authority、successor revision 与旧/新 source 管理边界见
[R6_I_SOURCE_CUTOVER_20261027.md](../design/job-unification/R6_I_SOURCE_CUTOVER_20261027.md)。
没有新增 HTTP/MCP 迁移路由，显式 port 供审阅后的可信宿主调用。普通新 source CRUD
继续走已存在的 canonical `/api/event-creators`；旧 source 写路由未恢复，不留指向
旧 writer 的半可用 API。原 GET 仅显示历史事实，不代表 canonical 当前执行状态。

## 三断点、history 与持续恢复

本轮重新执行 P22 正向探针：**exit 0，3 个断点均同 identity settle，1 Event / 1 drive / pin 0**。
完整 JSON：[identity-positive.json](../../evidence/r6i-resume/identity-positive.json)。

- 未 admission：adopt 不 publish；仅显式用户请求且完整映射后允许第一次 canonical publication，沿用原 eventId / drive key / occurrence。当前 canonical targets 只在该授权首次 publication 冻结。
- 旧 commit、未 advance：保留原旧 target/subscription/delivery/uncertain 证据，canonical targets 为空；显式 settle 查询 receipt 后推进原 occurrence，不重投。
- 已 advance、未 ack：保留原 cursor，沿原 identity 释放 pin、完成 canonical ack，不重选 targets，不重计数。
- 普通 composition 仍拒绝 unsettled source；通用 v1 writer 仍拒绝旧 envelope。未知/缺失 admission 证据在 host port 保持 uncertain，不能 publish 或 enable。
- 同一 owner 内，接管后启用 future drives，经普通 CreatorRuntime → EventBus → canonical admission → executor 真实测试替身执行，重复 tick 不重复 Run/effect；source 原始字节及 requests/links 不变。
- 冷重开后，Creator 按保留的 nextFire/anchor 继续发布并 ack，新的 canonical Event 可按同 eventId 幂等 admission；未 dispatch 前效果调用为零。完整跨 epoch dispatch 见下节红证据，不能把该层成功当作效果验收。
- canonical retire 后，原 source ID、pending adoption、links/requests 和 Event history 仍可查；未进行原数据物理删除。未知 source 的显式历史导入不会创建 Creator/drive/Event。

## 精确阻断：既有 Attempt epoch 回执不一致

`test_scheduler_source_migration.py::test_explicit_host_adoption_continues_same_grid_and_effect_keys[True-*]`
三条保留期望完整续跑的测试均失败。实际已经发生一次自有 SessionProvider 调用，但
持久 Attempt 的 `ownerEpoch=1`，传给 executor 的值为 2，`finish_attempt` 对 epoch 2
完成回执返回 `revision_conflict: Attempt ownerEpoch fence rejected the receipt`。

源码位置：`jobs/final_service.py::_drive_run` 将 `begin_attempt` 返回值改为当前 epoch；
`jobs/final_runtime/ledger.py::begin_attempt` 的持久 validated record 与返回值不是同一
对象，随后回执按当前 epoch 核对原记录而失败。本轮没有修改这条 owner/fence 实现，
也没有把执行结果伪装成成功或重发。

为区分候选引入回归和旧问题，另用 **无 source migration、无 Event、普通手动 Job**
的 epoch 2 探针，在 `bac726cf` 临时 `git archive` 导出目录和本候选分别运行：
两边 **exit 1，输出逐字段相同**，均为 runtime epoch 2 / persisted Attempt epoch 1 /
providerCalls 1。证据：[baseline-epoch.json](../../evidence/r6i-resume/baseline-epoch.json)、
[candidate-epoch.json](../../evidence/r6i-resume/candidate-epoch.json)、
[owner_epoch_probe.py](../../evidence/r6i-resume/owner_epoch_probe.py)。

因此，这不是只保存一份接管投影：publication/cursor/admission 和同 epoch 完整执行
都有后继断言；但**跨 epoch 完整效果恢复仍未验收**。该阻断需由 owner/fence 所属流修复并重跑三条红测。

## 测试、命令与统计

最终证据按 node 去重：**690 项，646 passed / 44 failed / 0 errors / 0 skipped**。
44 红项中 41 条原有接口/删除/bridge 红测均已在 `bac726cf` 独立导出复现；另 3 条是
本轮新增的跨 epoch 端到端覆盖，其普通 Job 根因也由上述基线探针复现。扩大矩阵仍红，
候选不等于整体接受。

| 矩阵 / artifact 前缀 | tests | passed | failed | skips | exit |
|---|---:|---:|---:|---:|---:|
| 核心 time/source/canonical/retirement/history：`core-final-v2` | 388 | 385 | 3 | 0 | 1 |
| 最后新增 opaque source、依赖与纯 calculator 后继：`changed-final` | 54 | 51 | 3 | 0 | 1 |
| 旧调度注入 hook 后继：`legacy-pass-final` | 1 | 1 | 0 | 0 | 0 |
| canonical HTTP/runtime/真实 stdio+CLI TCP、quota、handoff、retention、删除保护：`consumers-final` | 243 | 240 | 3 | 0 | 1 |
| Job API / bridge / composition：`legacy-final` | 63 | 25 | 38 | 0 | 1 |
| baseline Job API / bridge：`baseline-red` | 57 | 19 | 38 | 0 | 1 |
| baseline 原删除红项：`baseline-safety` | 3 | 0 | 3 | 0 | 1 |
| 依赖/import/startup 初验：`startup-final` | 3 | 3 | 0 | 0 | 0 |
| 最后抽取后独立 import/startup：`import-after-extraction` | 1 | 1 | 0 | 0 | 0 |

表内有重复 nodes，不能逐行相加。每项都有同前缀 `.xml` / `.log`，完整 node 及去重统计见 validation.json。
四份早期失败 XML/log 仅规范化生成内容的行尾空白；原始字节另存 `raw-tracebacks.zip`，
前后摘要见 `artifact-whitespace.json`。上轮 `r6i-time-events` 证据未作该处理。
另有 7160 tests collected，exit 0；初始 4 个 collection error 是当前系统解释器缺少 Playwright，
在 `.validation/r6i-venv` 的 system-site-packages 临时 venv 安装该依赖后收集通过，未改项目依赖。
没有安装/启动浏览器。旧测试生成的两份 tracked audit 输出已另存到本证据目录，再恢复其原文件，未覆盖历史证据。

所有测试通过 `scripts/run_tests_isolated.py` 清理应用环境并使用自有根。主要完整命令如下
（日志重定向至同名前缀 `.log`；JUnit 位置与命令一致）：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_source_migration.py tests/test_scheduler_dependency_cutover.py tests/test_scheduler_pending_adoption.py tests/test_scheduler_time_events.py tests/test_scheduler_canonical_migration.py tests/test_scheduler_store.py tests/test_scheduler_cron.py tests/test_scheduler_api.py tests/test_scheduler_history_read.py tests/test_scheduler_host_retirement.py tests/test_scheduler_surface_retirement.py tests/test_job_final_creators.py -q --tb=short --junitxml=evidence/r6i-resume/core-final-v2.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_source_migration.py tests/test_scheduler_dependency_cutover.py tests/test_scheduler_store.py -q --tb=short --junitxml=evidence/r6i-resume/changed-final.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_dependency_cutover.py::test_legacy_job_pass_never_drives_injected_legacy_time_runtime -q --tb=short --junitxml=evidence/r6i-resume/legacy-pass-final.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_final_http_service.py tests/test_job_final_runtime.py tests/test_job_final_clients.py tests/test_automation_canonical.py tests/test_automation_job_links.py tests/test_quota_policy.py tests/test_quota_host.py tests/test_handoff_job_retarget.py tests/test_job_delete_safety.py tests/test_job_delete_http_owned.py tests/test_jobs_completed_retention.py tests/test_test_isolation.py -q --tb=short --junitxml=evidence/r6i-resume/consumers-final.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_jobs_api.py tests/test_scheduler_legacy_bridge.py tests/test_automation_job_links.py -q --tb=short --junitxml=evidence/r6i-resume/legacy-final.xml
python scripts/run_tests_isolated.py -- python scripts/probe_pending_adoption.py --output evidence/r6i-resume/identity-positive.json
python scripts/run_tests_isolated.py -- python evidence/r6i-resume/owner_epoch_probe.py evidence/r6i-resume/candidate-epoch.json
python scripts/run_tests_isolated.py -- python evidence/r6i-resume/compare_baseline.py
python scripts/run_tests_isolated.py -- python evidence/r6i-resume/compare_baseline.py --safety
python scripts/check_scheduler_dependencies.py --output evidence/r6i-resume/dependencies-final.json
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_dependency_cutover.py -s -q --tb=short --junitxml=evidence/r6i-resume/startup-final.xml
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_dependency_cutover.py::test_web_import_and_canonical_startup_reopen_without_loading_either_legacy_host -s -q --tb=short --junitxml=evidence/r6i-resume/import-after-extraction.xml
python scripts/run_tests_isolated.py -- .validation/r6i-venv/Scripts/python.exe -m pytest tests/ --collect-only -o addopts= -q
git diff bac726cf --check
```

## 删除/保留、动态证据与未验证层

- **本轮删除文件：0**。`store.py`、`time_events.py`、`legacy_bridge.py` 按最新任务边界全部保留；不能宣称完成最终 host 删除验收。
- 生产扫描 341 Python 文件，parse error 0，静态 legacy host import 0、字面动态 legacy host import 0。30 处动态调用中，2 处为固定 stdlib time/signal，28 处为 e2e fixture loader；没有未审阅生产动态调用。任意第三方插件、外部代码与发行包不由此得到零依赖证明。
- 独立子进程主动阻止 store/time_events import，成功 import `packages.web.server` 并启动、关闭、重开自有 canonical host。网络 connect/bind 与 subprocess 在应用导入/运行阶段被探针拒绝（Windows event loop 私有 socketpair 在 guard 前创建）。重开后 epoch 2，只有原 1 Event、计数 1、pin 0。该场景没有新效果，不能替代跨 epoch dispatch 验收。
- HTTP/stdio/TCP 层证据来自现有 canonical client 套件的自有临时服务；不是已有 Pan 服务或部署验收。未启动完整生产 app lifespan，没有操作 8767/8768。
- 51 已迁与 60 unrecognized 的真实集合本轮均未检查或变更；原 ID/111 历史用例属于合成数据，不声称真实全 profile activeLegacy=0。
- 未执行全量后端、React build/Vitest/tsc（无 React 源码变化）、真实浏览器、远端 CI、macOS/Linux、真实数据迁移、部署或人工验收。独立 host、Session/Locker 引用和 owner/fence 实现未修改；现有测试范围以矩阵为准，不额外宣称其所有真实场景验收。
- 无 push、无 main/practical 推进、无已有服务启停；所有新增效果仅为测试替身或自有 fixture。
