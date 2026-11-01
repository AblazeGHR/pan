# R5-P4 executor/platform 候选与接口交接

工作树 `D:/project/pan-worktrees/job-p4-executors-platform-20261025`，分支
`r5/p4-executors-platform-20261025`，输入 `cb3b5bd16f3ed43e54df7ceffc1249b71476df3a`。
实现提交 `021b4f603112e1af56aefaa7b49f4003b26f89f0`；本文件所在的后续证据提交仅补文档与工件。
线性候选交 MA 审核和快进，未推进其它分支、push、部署或启停真实服务。

## 实现范围

- `JobStore` 保留导入名，负责 canonical execution host 容器及历史查询。新 root 不创建
  `jobs.sqlite3`、旧 logs 或旧 owner 账本；历史 GET 使用 SQLite `mode=ro`，缺库 list/events 为空。
  旧 create/retry 的宿主方法、客户端 helper 与 HTTP 写入口全部显式拒绝。
- canonical 恢复不再处于 `legacy_write` 内。active canonical owner 可恢复原 effect；若旧数据库
  仍存在，切换后保持其字节和状态不变。切换前仍允许核验旧 runner、写 unknown、cancel、ack，
  这些只用于旧资源/通知结算，不准新建或重投。历史公开结果带 `legacy=true/readOnly=true`。
- `ServiceLock` 保留导入名并委托 `platform.runtime_lock.RuntimeLock`，使用同一平台进程生存锁。
- system Action 的固定定义与不可变校验迁入 `actions.store.system_definition/validate_system_action`。
  旧模块重导出定义供历史校验，`invoke/execute` 抛 `PermissionError`，system_job 恢复准入为 false。
  历史 snapshot/finish/notify/wait 等结算 helper 保留至 R6；canonical 路径不调用它们。
- `execute_typed` 统一进入 `TypedExecutor`。调用者复用 executor、注入 receipt store，或为匹配的
  process/lifecycle operation 注入持久宿主；不能借不相关宿主创建临时 message executor。
  本地 typed effect 在第一次 provider await 前写 unknown intent；终态持久化失败仍能跨 executor
  查询这个 intent，unknown/partial 不重发。跨宿主并发准入仍由 canonical Attempt claim 负责。
- process 只用 `ProcessOwner.spawn(argv, ...)`，拒绝 shell 字段、command 字符串、NUL 与相对 cwd。
  根进程退出后须经 retained owner handle 的整树清理；timeout、异常和 close 失败保持证据式判定。
  independent runner 只记录同一 ProcessOwner 清理证明，不另写一套根退出即成功的逻辑。
- `ExecutionStore.phase` 校验单向阶段，重复或逆向 phase 拒绝。process 为
  `preparing → executing → running → cleaning`；lifecycle 为
  `preparing → stopping → stopped → starting → checking_ready`，exit 禁止 starting。
  任意阶段仍需原 Attempt lease/epoch/generation 和 supervisor claim，effect boundary 前事务关闭取消。
- `ProtectedLifecycleExecutor` 保留实时授权、固定单步和 ready/offline 证据门禁，新增同身份只读
  `lookup_receipt`，主服务离线时仍可查询原 operation。旧 `run_supervisor/run_exit_supervisor`
  只读原记录并返回 1；旧 CLI 输出 `legacy_lifecycle_retired`，不会 stop/start 或推进 phase。
- Windows desktop/token 实现移入 `core/platform/desktop_win.py`、`restricted_token_win.py`；旧文件
  保留 capability facade。CreateProcess ABI 移入 `process_structs_win.py`，desktop/token 不再依赖
  旧 independent runner。owned Job 属性改为懒加载 Windows 类型并先检查能力。

没有删除旧文件；没有修改 scheduler、automation、web/server、mcp、cli、React 产品代码。
原生安全测试使用显式合成历史 fixture，继续测真实 Job/令牌/多代进程清理，不重新开放旧 creator。

## 接口清单

独立服务仍是 loopback 内部端口，沿用 Host/Origin/JSON 门禁，不代替产品授权。

| 接口 | 本包行为 |
|---|---|
| `GET /health` | 健康、instanceId；不创建旧 registry |
| `POST /executions` | caller-owned `jobId/Attempt/effectKey/frozenPlan`；202、同 fence 同 plan 幂等查询；仅 typed process |
| `GET /executions/{effectId}` | 原 effect/Attempt、phase、evidence、result 查询；不重投 |
| `POST /executions/{effectId}/cancel` | body `{attempt: ...}`；原 lease/epoch/generation；仅 pre-effect 窗口 |
| `GET /execution-events` | canonical durable receipt outbox |
| `POST /execution-events/{eventId}/ack` | 幂等 ack；consumer durable commit 后才调用 |
| `GET /jobs[/{jobId}]`、`GET /events` | 历史兼容只读；缺库列表为空、单项 404 |
| `POST /jobs`、`POST /jobs/{jobId}/retry` | 410；不创建旧记录、不 spawn；坏 JSON 仍 400 |
| `POST /jobs/{jobId}/cancel`、`POST /events/{eventId}/ack` | 仅旧资源/通知结算，继续受原 identity 与 owner fence 限制 |

`ExecutionHost` 与 HTTP client 都提供 `get_execution/cancel_execution/execution_events/ack_execution`。
`IndependentExecutor.lookup_receipt`、`ProtectedLifecycleExecutor.lookup_receipt` 校验完整原身份。
`recover_execution_events(client, persist)` 在 `persist(event) is True` 后 ack；consumer 按 eventId 幂等并
核验 Attempt fence。unknown/partial 的外部收据只允许经 `ExecutionStore.reconcile` 的可信只读 lookup
查原身份；不新增公开、自报成功的 reconcile HTTP 入口，不实现自动 lease 接管。

新 executor API：

```python
await execute_typed(attempt=attempt, effect_key=effect_key,
                    frozen_plan=frozen_plan, executor=shared_typed_executor,
                    retry=False)
```

`retry=True` 不授权重发 unknown/partial。独立宿主的显式 retry 仍仅允许明确 rejected、cleanup
confirmed、不同 Attempt ID、递增 generation、非退回 epoch，且保留原 Job/Run/Step/effectKey。

## 独立能力矩阵（P4 增补）

此表是本包相关 capability 的文档增补，真源仍为 `core/platform/__init__.py`；不修改 P7 持有的总表。

| capability | Windows | Linux | macOS | 证据与边界 |
|---|---|---|---|---|
| `process.hiddenDesktop`（新增） | 声明可用；原生单测通过 | false，构造显式 unsupported | false，构造显式 unsupported | 私有桌面、token、CreateProcess Job 属性；不是安全沙箱，不承诺隔离外部 broker |
| `processOwner` | 声明可用；本机真实 Job/树/身份测试 | 已有实现，本轮无实机 | 已有实现，本轮无实机 | typed 进程统一经过该接口；根退出不能代替整树证明 |
| `service.lifecycle` | 已有实现；本轮 launcher 仅 mock | false | false | 单步骤、授权、phase、cancel fence、offline/ready 证据；无真实服务 stop/start |
| `job.runtimeOwner` | 已有实现；active-owner canonical 恢复实测 | 已有实现，本轮无实机 | 已有实现，本轮无实机 | 本地 RuntimeLock + SQLite owner fence；无跨主机租约 |

## 测试与证据

所有测试经 `scripts/run_tests_isolated.py`；使用临时 data/profile、自建进程和随机非保护端口。
命令数组、解释器、exit 与源 SHA 见 [commands.json](../../../artifacts/r5-p4/commands.json)，逐文件源 hash
与静态闭包见 [source-closure.json](../../../artifacts/r5-p4/source-closure.json)。实现提交前仅做了
`process_structs_win.py` 文件尾空行规范化；行为代码与最终测试一致。

| 层 | 结果 | 工件 |
|---|---|---|
| 22 个定向/组合测试模块 | **369 passed、20 skipped、4 failed**，exit 1，393 cases | [final.log](../../../artifacts/r5-p4/final.log)、[final.xml](../../../artifacts/r5-p4/final.xml) |
| 最终新增/相关 executor 测试 | independent execution 62、typed executor 19、independent service 28、main lifecycle 19、system Actions 11，均通过（已包含于上一行） | [test-summary.json](../../../artifacts/r5-p4/test-summary.json) |
| Windows `import packages.web.server` | exit 0；无服务启动 | commands.json 的 import 命令 |
| Windows 全仓收集 | **6573 collected**，exit 0 | [collection.log](../../../artifacts/r5-p4/collection.log) |
| 原始收集环境 | 缺 playwright，6568 collected / 4 collection errors，exit 2；保留红证据 | [collection-missing-deps.log](../../../artifacts/r5-p4/collection-missing-deps.log) |
| 独立 baseline 复现 | cb3b5bd1 临时 git archive 副本：3 个 pre-import legacy + 1 个 delete 失败完全复现 | [baseline-preimport.log](../../../artifacts/r5-p4/baseline-preimport.log)、[baseline-delete.log](../../../artifacts/r5-p4/baseline-delete.log) |
| 真实 owned-tree delete HTTP | 2 cases 通过，最终自建进程为空、端口释放 | [http-owned.json](../../../artifacts/r5-p4/http-owned.json)、[http-owned-recheck.json](../../../artifacts/r5-p4/http-owned-recheck.json) |
| 静态/提交检查 | `git diff --check` 通过；canonical 两模块无旧 `.create/.retry` 属性；无文件删除、无越界产品文件 | source-closure.json |

20 skip 为：18 个真实 POSIX 契约、1 个仅 POSIX 才验证的 Windows unsupported 检查、1 个需显式 opt-in
注册自建 Windows Task 的部署测试。未新增 skip/xfail，没有弱化这 4 个失败断言。基线失败为：

1. `test_runner_death_at_creation_edge_converges_without_survivors[base-pre-import-legacy]`；
2. 同测试 `[venv-outer-pre-import-legacy]`；
3. 同测试 `[venv-inner-pre-import-legacy]`；三者均为 `legacy.reconcile_running(root)` 返回 0 而预期 1；
4. `test_authorized_action_delete_preserves_active_snapshot_and_legacy_guard`，旧 DELETE 返回中缺少 `deleted`。

前 3 项涉及 P1 的旧 reconciler，第 4 项涉及 P1/P5 的删除路由/兼容模型；本包保留红项供 MA 组合判定。
早期 fixture 迁移前的失败 JUnit 也保留，不把最终候选报告成全绿或已验收。

收集依赖 `playwright` 和 `pytest-timeout` 仅安装到 repository/profile 外的临时目录，未装 browser、未改
全局 Python；版本见 [dependency-env.json](../../../artifacts/r5-p4/dependency-env.json)。复现收集可先用
`python -m pip install --target TEMP_TEST_DEPENDENCIES playwright pytest-timeout`，再用 commands.json
中的 `with_temp_deps.py --deps TEMP_TEST_DEPENDENCIES` 包装器。

## 交接与未测边界

- P1/P5 必须撤掉旧 `start/retry/system.execute/run_supervisor` 调用；旧 Pan bridge 可能把 retirement
  拒绝投影成静态 unavailable，它不能被当作可继续使用的创建路径。本包没有修改这些跨组调用方。
- canonical 只接受 caller-owned 身份；产品 read/operation authorization、outbox consumer 的启动接线
  由相应包负责。旧 pending outbox/旧活资源未清零前，不移除这里的兼容结算或底层 cleanup primitive。
- P7 合并能力矩阵和平台 snapshot；`process.hiddenDesktop` 是新增的布尔键。R6 才删除旧文件。
- 已测真实 Windows detached worker、HTTP、原生 token/owned-tree cleanup；lifecycle 的 stop/start 为 mock。
  未测 Linux/macOS 实机与远端三平台 CI、生产浏览器/MCP/CLI、真实 provider、真实数据迁移、部署及人工验收。
- 没有在 8767/8768 监听、调用服务控制或接管已有进程。没有变更 main/practical、push 或发布。
  本包可作为线性候选审阅，不能据此关闭 R5 全局出口或三平台完成定义。
