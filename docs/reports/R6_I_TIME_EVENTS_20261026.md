# R6-I：同 identity 门禁阻塞，停止迁移与删除

- 工作树：`D:/project/pan-worktrees/job-p21-r6-time-events-20261025`
- 分支：`r5/p21-r6-time-events-20261025`
- baselineSha / sourceSha / 当前 HEAD：`f0970e23df5108eabd7580847346129d1714ba14`
- 结果：**未实现迁移、未通过删除门禁**。按用户“无同 identity 证据就停止报告”停止产品改动。本轮仅新增报告、隔离探针及证据；没有新提交。

## 实测阻塞

用三个独立临时 profile 合成同一类旧 source，包含 creation request 与完整 `source_links`，分别停在：

1. 已 reserve、尚未 admission；
2. 旧 EventStore 已 commit、source cursor 尚未 advance；
3. source cursor 已 advance、EventStore 尚未 ack/release。

每个场景都先冻结旧 SQLite 字节与 snapshot，再使用现有 OwnerFence 激活 canonical owner，显式导入历史并尝试 CompositionCoordinator adoption。探针没有改 owner/fence 实现，没有启动服务或执行 Action。

三场景结果一致：

- 历史导入完整保留 source、pending/cursor、links、requests，旧 source 与 EventStore 数据库字节不变。
- adoption 返回 `pending_publication: Legacy source publication is unsettled; history only`。
- 将原 envelope 直接传给 CanonicalEventStore，返回 `invalid_schema: Legacy Event wire name is not accepted: id`。这只证明现有直传路径不兼容；没有尝试放宽 v2 schema。
- canonical runtime tick 产生 **0 Events、0 drives**，候选 Creator 仍 disabled；原 pending/未释放 cursor 没有被 canonical 接管、结算或 ack。

因此，**保留历史与拒绝重投已验证，同 identity 迁移尚无成功证据**。不能把零重投解释成迁移成功，也不能先生成新 v2 identity 再宣称旧 occurrence 已结算。

原始结果与重跑入口：
[identity-gate.json](../../evidence/r6i-time-events/identity-gate.json)、
[identity-gate.log](../../evidence/r6i-time-events/identity-gate.log)、
[identity_probe.py](../../evidence/r6i-time-events/identity_probe.py)。探针明确 exit **1**，表示需求门禁阻塞；不是 pytest 的失败计数。

## 代码推断与删除闭包

- `providers.py::legacy_source_plan` 明确拒绝 pending 和 `eventStoreReleased != true` 的 cursor；`composition.py::_restore_legacy_sources` 在此处终止。
- `CreatorStore.import_legacy_sources` 只写非执行 history。它没有把旧 EventStore 的原 receipt 导入 canonical，也没有建立旧 pending → canonical drive 的结算映射。
- `CreatorStore._occurrence_ids/reserve` 为新的 canonical Creator/revision/occurrence 生成 drive 和 Event identity，现有路径不能据此证明旧 `evt_schedule_*` 的同 identity 接管。
- 保留且禁止修改的 `legacy_bridge.py:18–19` 顶层依赖 `store` 和 `time_events.EVENT_TYPE`；未迁移这些依赖前删除两模块会破坏导入。
- AST 扫描 `packages/**/*.py` 还发现题述以外的 store 依赖：`jobs/action_runtime.py:53`、`automation/quota_policy.py:552`、`jobs/final_runtime/coordinator.py:729`、`jobs/final_service.py:1030`。后两者分别位于 handoff root 解析与生产 bind 路径，不能遗漏。
- 包内共有 14 处静态 import（其中 1 处为 `web/e2e/job-pin-server.py` helper），其余 13 处未归零。此扫描不宣称覆盖任意外部插件或非字面动态 import。

位置、文件摘要与测试统计见 [summary.json](../../evidence/r6i-time-events/summary.json)。

## 删除、保留与替代证据

| 对象 | 本轮状态 | 证据 / 未完成项 |
|---|---|---|
| `scheduler/time_events.py` | 保留，零修改 | 旧 pending/cursor 仍需原路径；没有 canonical 同 identity 替代证据 |
| `scheduler/store.py` | 保留，零修改 | 生产 import 非零；纯时间能力抽取未实施 |
| `scheduler/legacy_bridge.py` | 保留，零修改 | cursor/receipt 安全契约未更改；本轮不声称完成 bridge 验收 |
| CreatorStore / CanonicalEventStore / CreatorRuntime | 零修改 | 原有 canonical 行为回归通过；跨存储 pending 接管不支持 |
| `jobs/api.py` | 保留，零修改 | data_root/delete_task 尚未迁移；已有非 legacy `scheduler.history.data_root` 可作为后续根解析候选，删除必须保留原保护与 reservation 语义 |
| 产品测试、独立执行、owner/fence | 零修改 | 不降低断言、不增加 skip、不改变运行协议 |
| 删除后历史可读 | 未验证 | 本轮没有删除模块；历史导入可读不能替代删除后的完整验收 |

后续实现首先需要一条经过验证的原 Event/occurrence/drive 结算映射，分别处理“未 admission / 已 admission 未 advance / 已 advance 未 ack”，核对原 EventStore receipt 与 pin，并在 crash/restart 下沿用原 identity。不能只复制 source JSON 或只复用 eventId 字符串。除此之外，删除依赖闭包需要处理上述剩余调用方，并解决保留 bridge 的模块依赖边界。本轮没有实施这些修改。

## 验证计数

| 检查 | 结果 |
|---|---|
| time_events / canonical migration / final creators / cron | **99 passed，0 failed，0 errors，0 skipped；exit 0** |
| 同 identity 隔离探针 | **3 个场景均阻塞；exit 1**；原 DB 字节与历史 snapshot 保留，0 canonical Event/drive |
| `git diff --exit-code -- packages tests scripts` | exit 0，产品与既有断言未修改 |

测试命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_time_events.py tests/test_scheduler_canonical_migration.py tests/test_job_final_creators.py tests/test_scheduler_cron.py -q --tb=short --junitxml=evidence/r6i-time-events/baseline-tests.xml
python scripts/run_tests_isolated.py -- python evidence/r6i-time-events/identity_probe.py evidence/r6i-time-events/identity-gate.json
```

JUnit：[baseline-tests.xml](../../evidence/r6i-time-events/baseline-tests.xml)；
日志：[baseline-tests.log](../../evidence/r6i-time-events/baseline-tests.log)。

99 项包括原 envelope 在旧存储重启重试、canonical 自身的 receipt/cursor/ack/crash/contender、已结算 source 的 cursor/link adoption、reserve misfire/expire、DST、秒、分数、anchor 与 100 occurrences。这些不能作为旧未结算 identity 跨存储接管的替代证据。

未运行全量后端、前端构建、完整 app lifespan、真实 HTTP/浏览器、远端 CI、Linux/macOS 或真实数据迁移。没有 push、服务启停、部署或 8767/8768 操作。源码实现、迁移验收、删除验收均未完成；本轮交付为阻塞证据。
