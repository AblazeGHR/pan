# P24：Job 保留历史精确门禁候选

工作树 `D:/project/pan-worktrees/job-p24-retained-history-20261027`；分支
`r6/p24-retained-history-20261027`。固定接口基线
`bdd4c778d55daff58529635b2adc464c03a1393c`；集成基线仍是
`bac726cf84c0510dff8618afb82e227e660f590c`，不表示 P21 整体 MA 验收。

**测试源码 SHA：`5e71adfde62d266964dc818cbd86789578a71c2a`。** 本候选补齐指定 8 项
精确保留后继：7 个原 node 迁移非执行 fixture 后通过，1 个原 bridge node 原样保留且仍红，
新增严格 canonical 后继通过。最终定向回归 18 passed / 0 failed / 0 errors / 0 skipped，exit 0。
此为 TA 待 MA 审阅候选，不是整个 Job 统一工程、旧 host 删除或真实 profile 迁移验收。

## 修改范围与接口

- 修改 `tests/test_jobs_api.py`，只改混合列表与 metadata 六分支两个函数的 fixture/断言。
- 新增 `tests/test_job_retained_history_fixtures.py`：禁用历史记录、执行拒绝替身和文件字节快照。
- 新增 `tests/test_job_retained_history_bridge.py`：canonical manual admission + 原桥接历史终态。
- 产品源码、API/schema 均无修改；没有新增产品 writer、legacy fallback 或 host 删除。
  `packages/jobs/pins.py` 已核实为实际路径，现有 snapshot 对 JSON/encoding/read 故障均返回
  `pinned=None / pinMetadataStatus=unavailable`，本次无需修复。

历史 fixture 使用自有临时根，`enabled=False / executable=False / historicalEvidenceOnly`；
保留旧 status（包括 running/unknown）仅用于读取，不调用执行 loop。旧 start 已不存在的符号
不会由测试补回；仍存在的 spawn/loop/worker 操作被设为 forbidden。

metadata 六分支选择任务明确允许的**离线历史 fixture**路径：
真实 `background_jobs._create/update_job_field -> JsonRegistryStore/_write_job` 原子提交后，
真实 `_scheduled_task_emit -> jobs_api._emit` 发布，每次操作恰一条广播；测试不自造广播。
读取真实持久文件确认 name 配置，再经过实际 `_view`、HTTP GET/list 与 pin 拒绝。
没有调用退役 POST/PATCH 创建执行式旧任务，也没有恢复 writer。

这是离线历史存储/现有展示边界的成功证据，**不是 canonical CRUD 已接管 pins/WS 的证据**。
当前 `FinalService.write` / `final_api.definition_routes` 的 native 对象响应不含该旧展示协议；
本次未改 MA 持有的这些接口，不以离线绿测宣布最终 S13 接线或旧 facade 可删除。

## 原 8 node 到严格后继

表中 `API` 为 `tests/test_jobs_api.py`；metadata 前缀为
`test_pin_metadata_failure_does_not_fail_committed_operations`。
完整 pytest 身份和逐项结果见 [validation.json](../../evidence/p24-retained-history/validation.json)。

| 原 node | 严格后继 | 保留/加强的原断言及本轮结果 |
|---|---|---|
| `API::test_list_jobs_mixed_kinds_and_filters` | 同 node，离线历史 fixture | 两 kind、原 jobId/taskId 别名、中文名称、kind/status/includeCompleted 过滤；active-first、终态顺序及同状态 updatedAt 排序；原 dispatch/entry ID 的 unknown 历史可查；读取前后所有文件字节相同。通过 |
| `API::<metadata>[session-message-json]` | 同 node | 损坏 JSON 下历史记录提交与 name 编辑成功、实际磁盘持久、created/updated 各一次广播、详情/列表 unavailable、pin 明确拒绝、坏文件字节不变且无额外广播。通过 |
| `API::<metadata>[session-message-encoding]` | 同 node | 同上，非法 UTF-8 字节。通过 |
| `API::<metadata>[session-message-read]` | 同 node | 同上，pins 读取 PermissionError；读取失败期间仍用 read_bytes 核对原文件。通过 |
| `API::<metadata>[scheduled-task-json]` | 同 node | 同上，scheduled-task 非执行历史记录。通过 |
| `API::<metadata>[scheduled-task-encoding]` | 同 node | 同上，scheduled-task 非执行历史记录。通过 |
| `API::<metadata>[scheduled-task-read]` | 同 node | 同上，scheduled-task 非执行历史记录。通过 |
| `tests/test_scheduler_legacy_bridge.py::test_run_now_does_not_move_periodic_next_fire` | `tests/test_job_retained_history_bridge.py::test_run_now_does_not_move_periodic_next_fire_retained_successor` | 原文件/node 未修改，独立复跑仍因已退役 `system_actions.execute` 注入报 AttributeError；严格后继通过，细节如下 |

bridge 后继先在 owner 激活前创建合成历史 source/publishCursor、原 Event、completed subscription
receipt、原 task/job/run/dispatch/entry ID；未构造或运行 legacy Runtime。显式 canonical
adopt/status/settle，完整绑定原 Job ID 到 successor，查询原 receipt 的 status/runId/targets/done。
启用 successor Creator 后，canonical manual Run 完成，cause 精确为 manual、provider 恰一次；
按原 operationId/Run ID 查询结果，无重新提交。Creator **整个对象**与执行前相同，
`nextFireAt=2026-01-01T00:02:00Z`、producedCount/drive/cursor 不变，没有伪造时间 Event。

同一测试通过 `History` 的原 jobId/taskId 查询原 completed run；通过 `SourceHistory` 查询
原 `scheduler-event-v1` owner 标签、publishCursor.eventId、nextFireAt；原 source/JSON/JSONL
字节不变。旧桥的 switch/run_now、旧 Runtime.launch、source reserve/advance 均 forbidden。
新 canonical owner 下旧 legacy_pass 明确被 fence 拒绝，legacy_leases=0；关闭后 owner=closed。
这证明合成 fixture 的 owner/receipt/cursor 终态，**不是 51/60 真实集合的终态或整 host 删除许可**。

既有 `tests/test_job_retained_pin_successor.py` 未修改且最终矩阵实跑通过：实际
`coordinator.atomic_write` 故障仍返回 persistence_failed、pins 字节不变、无广播、GET 仍 pinned。
未以 JsonPins 读取故障替代该独立的原子写失败语义。

## 实际验证

所有测试命令均由本目录 `run_matrix.py` 记录完整 argv/cwd/exit，并调用隔离 wrapper：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest <nodes> -q --tb=short --junitxml=<artifact.xml>
```

完整 `<nodes>` 不是省略的交付信息：每轮实际命令见同名前缀 `.json` 和汇总 validation.json。
可直接复跑的最终矩阵入口：

```powershell
python evidence/p24-retained-history/run_matrix.py final tests/test_jobs_api.py::test_list_jobs_mixed_kinds_and_filters tests/test_jobs_api.py::test_pin_metadata_failure_does_not_fail_committed_operations tests/test_job_retained_history_bridge.py tests/test_job_retained_pin_successor.py tests/test_jobs_api.py::test_pin_persists_without_touching_lifecycle tests/test_jobs_api.py::test_pin_validation_and_independent_owner tests/test_jobs_api.py::test_multiple_pins_filter_and_corrupt_metadata tests/test_jobs_api.py::test_empty_list_exposes_pin_metadata_failure tests/test_scheduler_source_migration.py::test_canonical_retirement_keeps_original_identity_links_requests_and_event_history
```

| artifact 前缀 | tests | passed | failed | errors | skipped | exit |
|---|---:|---:|---:|---:|---:|---:|
| baseline：固定基线 8 原缺口 + 原子写后继 | 9 | 1 | 8 | 0 | 0 | 1 |
| history-metadata-v1：测试 guard 误假定退役符号仍存在 | 8 | 1 | 7 | 0 | 0 | 1 |
| history-metadata-v2 | 8 | 8 | 0 | 0 | 0 | 0 |
| bridge-v1：fixture 写 receipt 时已激活 owner，正确被 fence 拒绝 | 1 | 0 | 1 | 0 | 0 | 1 |
| bridge-v2：缺显式 successor review，正确被合同拒绝 | 1 | 0 | 1 | 0 | 0 | 1 |
| bridge-v3 | 1 | 1 | 0 | 0 | 0 | 0 |
| focused-final：加强前矩阵 | 18 | 18 | 0 | 0 | 0 | 0 |
| strengthened-final：测试误读 publishCursor.event，实际为 eventId | 18 | 17 | 1 | 0 | 0 | 1 |
| **final：最终源码的 8 精确后继 + 原子写 + 相邻回归** | **18** | **18** | **0** | **0** | **0** | **0** |
| original-bridge-preserved：原 node 原样保留复跑 | 1 | 0 | 1 | 0 | 0 | 1 |

各轮不相加；失败实验均保留。最终 [JUnit](../../evidence/p24-retained-history/final.xml)、
[log](../../evidence/p24-retained-history/final.log)、[完整命令](../../evidence/p24-retained-history/final.json)。
现有 FastAPI/Starlette httpx 弃用 warning 保留，没有新增 skip/xfail、吞 unknown 或降低安全行为。
`git diff --check` 通过；源码提交使用正常 hook（core.hooksPath=scripts，未触发 React 路径校验）。

## 原证据与未验证层

`original-test-sources.zip` 保存固定基线两份原测试源码。`validation.json.preserved` 保存
原 R6-I 全 evidence、原报告、三个 host、原 bridge 测试和原 pin 后继的 Git blob 与当前文件 hash，
逐条核对与固定基线一致。原 41/43 红报告/XML/assessment 均未改；本轮没有复跑整组 41/43，
不重新计算其整体当前绿红计数。未处理 29 retired-positive、stale-injection 原 node、
P23 的三个删除安全缺口或额外两条 quota/automatic-recovery 旧红。

实测层为本机 Windows 隔离 pytest、ASGI TestClient 读取/拒绝、canonical Service/ledger/owner
和记录型 Session provider。未做真实 TCP HTTP、真实 Session/provider/QQ/进程效果、浏览器、
React 构建、全仓 backend、三平台远端 CI、真实数据迁移、部署与人工验收。
未读取或更改 51/60 真实集合、真实 data/profile；未启动监听端口或操作已有服务。
未派子 TA，未改共享产品/React/conftest/support、P23 文件或三个 host，未 merge/push，
未推进 main/practical。最终 clean 候选 SHA 见交付回复；后继 evidence/report 提交不改测试源码。
