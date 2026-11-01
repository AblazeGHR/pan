# R5-P3 Automation 迁移与旧测试退役

分支 `r5/p3-automation-20261025`，基于 `8d0f0663e563c4ab1c7344ab9289d9f184800745`（快进候选）。

## 结论

Automation 只保留 Event 匹配、State evaluate、Trigger/Job admission、quota policy 四项职责。
旧 Engine Action 执行/重试/chain 宿主降为 read-only/兼容，不再被调用；旧 Store/Ledger 写路径在
owner 激活后拒绝，历史 Event/Run 仍可读。已删除的 `Engine.execute`、`Ledger.claim/finish`
未恢复。

focused 清单 26 个文件 **403 passed / 0 failed / 0 skipped**（隔离环境，非 8767/8768）。

## 改动范围

仅 `packages/automation/` 与 `tests/`（外加本报告与证据目录）。以下模块零改动：

| 目录 | 改动文件数 |
|---|---:|
| `packages/scheduler` | 0 |
| `packages/jobs` | 0 |
| `packages/core/server` | 0 |
| `packages/mcp` | 0 |
| `packages/cli` | 0 |
| `packages/web` | 0 |

源码改动 6 个文件，+63/−279 行：

- `chain_engine.py`：`JobAdmission.target/admit` 加 `legacy_writer` 围栏。
- `engine.py`：`Engine.match` 加围栏；删除 admission 结束后的 `job_runtime.tick(...)` 唤醒。
  Automation 不再驱动任何执行宿主。
- `job_links.py`：221 行组合/恢复实现整体退役为只读证据；`reserve/checkpoint/quarantine/
  remove_owned/compose/recover/cancel` 一律抛 `Conflict`，不再建 Job、不再启用时间源；
  `references()` 降到 `enabled: False` 且返回深拷贝。
- `quota_policy.py`：`create_monitor/ensure_job/tick_jobs` 加围栏（`ensure_job` 用
  `reservation=True`，在 store 锁外预留）。
- `service.py`：`start()` 去掉整段 legacy 宿主启动（`jobs.action_runtime.Runtime`、
  时间源、`job_links.Links`、`LegacySchedulerBridge`、quota adapter/recover、issue observer）；
  无 canonical owner 时抛 `DependencyUnavailable`；`start()` 现在幂等（重复调用不重建任务）；
  `quota_policy` 提前到 `__init__` 构造（`Store.__init__` 只存路径，不落盘），使
  `close()` 无条件安全；`close()` 清空 `ingester/dispatcher` 引用。
- `store.py`：`Ledger.update` 拒绝非 `recordType == 'triggerAdmission'` 的历史 Run。

`Ledger.mutate`（`store.py:56`）是唯一写入漏斗，已带 `legacy_writer` 围栏，因此 12 条公共写
方法全部被同一围栏覆盖，无需逐条加装饰器。

## QA 156 项集成新增红的处理

`tests/automation_retirement.json` 逐项记录；156 项与本清单**精确划分、无重叠无遗漏**
（`qaNewFailuresCovered` ∩ `qaNewFailuresOutsideScope` = ∅，并集 = 156，已用脚本核对）。

| 归类 | 数量 | 说明 |
|---|---:|---|
| 本任务改写/退役覆盖 | **85** | 其中 `rewrite` 64、`retire` 21 |
| 明确越界（不属 automation/event/ETA/quota） | 71 | 见下表 |

85 项中 `rewrite` 64 项改为 canonical admission / Run / Step / Event inbox / 只读历史断言；
`retire` 21 项**全部**填写后继节点（无「只删不补」）。

清单同时审计了受影响测试文件的**全部 126 个基线用例定义**（不限于 QA 红项）：
`rewrite` 85、`retire` 22、`keep` 19。三类均填写 `successors`（`keep` 填自身），
无一条为空。`keep` 19 项经 collect 确认仍存在且未改语义。

### 后继断言校验

- 后继节点共 82 个唯一 ID，跨 17 个文件，**全部可 collect（missing = 0）**。
- 21 项 retire 的后继示例：
  - `test_atomic_claim_fences_concurrent_readers_and_trigger_limit` →
    `test_automation_retirement.py::test_legacy_execution_api_is_absent` +
    `test_automation_concurrency.py::test_same_event_concurrent_admission_has_one_run_and_operation`
  - `test_legacy_running_migrates_to_one_uncertain_attempt_without_event_replay` →
    `test_automation_retirement.py::test_history_is_read_only_and_never_recovered`
  - `test_cancel_backoff_head_wakes_serial_tail` →
    `test_automation_concurrency.py::test_serial_cancelled_head_does_not_starve_next_admission`
  - `test_fa01_direct_trigger_reuses_visible_managed_job_across_events_and_restart` →
    `test_job_final_admission.py::test_fa01_explicit_target_reused_across_events_and_restart`
- 4 项为「同 ID 就地改写」（`quota_policy_http`、`quota_host_http`、`fa01_two_triggers…`、
  `eta_ledger_growth`），节点名保留但断言已改成拒绝语义；例如
  `test_owned_ledger_growth_measurement` 由「测 update 耗时」改为
  `pytest.raises(Conflict, match='read only')` + 前后 bytes 相等，并把产物写到 `tmp_path`
  而非仓库 `artifacts/`。

### 新增负控（`tests/test_automation_retirement.py`，44 个用例）

覆盖：11 个旧执行 API 必须不存在；6 种历史 Run 状态只读且不被恢复；12 条 legacy 写路径被
owner 围栏拒绝且 bytes 不变（owner 释放后仍拒绝）；无 owner 的 Service 不能启动 legacy 宿主，
且不创建 ledger/events 文件；canonical Service start/close 双向不触碰旧 runtime
（用 `__getattr__` 抛错的 `Retired` 哨兵）；`match` 只调用 admission port 且不 tick；
7 个 legacy links 方法拒绝变更且不触碰 runtime；quota query 宿主在查询/效果前被拒绝。

### 71 项越界明细（未处理，理由：非本次职责域）

`job_action_templates` 13、`plugin_events` 9、`locker_trusted_binding` 7、`job_action_chain` 4、
`authorization_mcp` 3、`job_lifecycle_prevention` 3、`locker_independent_review` 3、
`session_query` 3、`authorization_runtime` 2、`codex_session_conversion` 2、
`job_chain_phase5_e2e` 2、`job_overlap` 2、`legacy_mcp_compat` 2，其余 16 个模块各 1。
这些属于 scheduler/jobs/executors/server/mcp/cli/UI 与授权域，本次任务明确不改。

## 测试计数

隔离环境（`scripts/run_tests_isolated.py`，继承的 `PAN_*` 被清除，`PYTHONPATH` 重写，
测试端口由脚本探测分配；未使用 8767/8768）：

| 项 | 值 |
|---|---|
| focused 文件 | 26 |
| collected | 403 |
| passed | 403 |
| failed | 0 |
| skipped | 0 |
| 用时 | 69.9 s |
| 证据 | `artifacts/r5-p3-automation-20261026/focused-v3.xml`（JUnit XML，已入库）；`focused.log`/`collection.log`/`dependencies.log` 受仓库 `.gitignore:77`（`*.log`）约束，仅保留在本机 |

逐文件收集数（合计 403）：`quota_policy_safety` 65、`quota_policy_idle` 59、
`automation_retirement` 44、`quota_host` 43、`quota_provider_source` 39、
`automation_retry_parallel` 37、`automation_core` 20、`quota_policy` 19、`event_store` 17、
`job_final_admission` 12、`runtime_issues` 10、`automation_job_links` 6、
`automation_action_chain` 6、`event_trigger_reliability` 5、`automation_concurrency` 5、
`worker_runtime_issue_identity` 2、`event_store_http` 2、`eta_report_coexistence` 2、
`eta_final_combined` 2、`automation_retry_http` 2、`automation_http` 2、`quota_policy_http` 1、
`quota_host_http` 1、`eta_report_integration_http` 1、`eta_ledger_growth` 1。
`test_automation_canonical.py` 无自有用例（是共享 `canonical` fixture 模块），符合预期。

无 skip、无 xfail、未弱化任何断言。解释器：`C:\Users\14709\AppData\Local\Programs\Python\Python314\python.exe`，
pytest 9.1.1（`pytest-timeout` 未安装，故 `pytest.ini` 的 `timeout` 行只产生 config warning，
不影响其余选项；实测最慢单用例 1.62 s、合计 68.33 s，fixture 级 `asyncio.wait_for` 超时仍在生效）。命令：

```
python scripts/run_tests_isolated.py -- python -m pytest <26 文件> -o addopts= -q \
  --junitxml=artifacts/r5-p3-automation-20261026/focused-v3.xml
```

## 未测边界

- 仅 focused 清单；未跑全仓 6500 后端身份与全量 Vitest，因此**不能**据此声称全仓绿。
- 71 项越界红未修（按分工不属本任务），集成侧仍有已知红。
- 未验证真实 profile 迁移、真实 provider、真实服务重启、部署与人工验收。
- HTTP 相关断言走 TestClient/ASGI 与自有临时 store，未开真实 socket/Node/Chromium/MCP。
- 未测多进程/多主机的 owner 抢占与崩溃恢复竞态；围栏只在单进程并发与显式 epoch 下验证。
- `job_links` 只读化后，旧 UI/旧客户端的组合入口行为未做端到端兼容验证（属 UI/旧入口域）。
- 未在真实 7 天保留窗口上验证历史 Event 容量行为。

## 提交约束遵守

- 未 push、未启停服务、未触碰 8767/8768。
- 未恢复 `Engine.execute`、`Ledger.claim`、`Ledger.finish`（由 `test_legacy_execution_api_is_absent`
  的 11 个 `hasattr` 参数化断言守护）。
- 测试运行覆盖的 `artifacts/job-chain-phase5/*`、`artifacts/job-templates/http-evidence.json`、
  `audit/job-delete-main-integration/*` 已被其他套件重写，提交前已 `git checkout --` 还原，
  不进入本次提交。
