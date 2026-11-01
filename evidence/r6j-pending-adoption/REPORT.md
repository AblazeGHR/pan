# R6-J pending publication adoption 候选报告

工作树：`D:/project/pan-worktrees/job-p22-r6-pending-adoption-20261025`。
分支：`r5/p22-r6-pending-adoption-20261025`。
基线：`f0970e23df5108eabd7580847346129d1714ba14`。
本报告随候选提交；完整候选 SHA 在交付消息中提供。验证输入的逐文件 SHA256
固定在 [validation.json](validation.json) 和 [identity-gate.json](identity-gate.json)，
提交前再次核对一致。没有 push、main/practical 推进、服务启停或 8767/8768 操作。

## 实现与新增契约

- `CreatorStore.adopt_legacy_pending(events, legacy_events, import_key, source_id, timezone_name)`
  与 `PendingPublicationAdoption.adopt/status/settle` 是受 profile/owner fence 保护的显式入口。
- 原 eventId 不变；原 reserve tuple 保存为 driveKey，driveId 使用原 eventId，
  occurrenceKey 保留原 scheduledAt 字符串。原始 envelope/source/links/requests 和
  原 EventStore commit、targets、subscriptions、delivery/error/pin 证据保留。
- Creator prepare → Event adoption → Creator receipt 可重入。只复制已有 commit 的
  v2 表示，不选择新订阅；没有 admission 的 reservation 保持 awaiting_admission。
- 新增 adopted_pending/adopted_uncertain、prepared/adopted、adoptionComplete、
  publicationState、cursorAdvanced、eventStoreReleased、publicationSettled 等可查询状态。
  旧 cursor 已推进且有原 commit 时，直接保留 published drive 和一次 producedCount，
  不再预留或重复推进。所有案例 adopt 后 ack=false。
- settle 只查询真实 Event 收据，依次提交 cursor、释放原 pin、释放并核验 canonical pin、
  保存 canonical ack。旧 source 数据库始终 byte-identical，不伪造旧 runtime ack。
  settle 会释放旧 EventStore 中实际 pin，其他原 Event/target/subscription/delivery 行不变。
- 普通 Creator advance/ack、Runtime 内部 publish/ack 和 tick 都不能绕过 unsettled
  adoption。结算后，后续显式启用的正常 canonical 驱动仍可工作；重复 adopt 不污染新驱动。
- 普通 v1 writer 仍返回 invalid_schema。legacy composition 对 unsettled 来源的旧拒绝
  仍成立，未放宽 schema、覆盖旧效果回执或加入自动重投。

详细契约与调用方式见
[PENDING_PUBLICATION_ADOPTION_20261026.md](../../docs/design/job-unification/PENDING_PUBLICATION_ADOPTION_20261026.md)。

## 验证

最终组合命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_scheduler_pending_adoption.py tests/test_job_final_creators.py tests/test_scheduler_canonical_migration.py tests/test_scheduler_time_events.py tests/test_event_store.py tests/test_r6_legacy_leaves.py tests/test_job_final_contracts.py tests/test_job_final_admission.py tests/test_job_final_http_service.py tests/test_job_final_runtime.py -q -o addopts= --junitxml=evidence/r6j-pending-adoption/regression-final.xml
```

退出 0，**334 passed / 0 failed / 0 errors / 0 skipped**，79.50 秒。
其中 **27 项**是新增 pending adoption 专项测试，包含在 334 项内。
[最终日志](regression-final.log)、[JUnit](regression-final.xml)、[统计与源码哈希](validation.json)。
保留一个现有 FastAPI/Starlette TestClient 的 httpx 弃用告警，未抑制。

专项覆盖：

- interval/once 各三个断点；重复 adopt/settle；原始 identity、cursor、pending、ack
  状态和 links/requests 的保持。
- Event commit 前后、cursor commit 后、原 pin 释放后、canonical pin 释放后的五个
  故障边界，关闭并以新 owner epoch 重开恢复，不重复 Event/drive/count。
- 真实子进程在 cursor commit 后 `os._exit(73)`，随后冷开获取下一 owner epoch，
  完成 pin/ack；没有重新 publication。
- 旁路 advance/ack、Runtime 内部 publish/ack、fact/timezone rebinding、foreign profile
  均拒绝；真实查询检查 cursor commit → 双 pin 释放 → ack 的顺序。
- 原 commit 缺失时保持 adopted_uncertain；查无记录不授权重投。恢复同 identity 的
  原 committed envelope 后重复 adopt 可恢复，再显式 settle。
- 旧 uncertain subscription、runId、error、delivery evidence 保持不变，未转为 succeeded。
- 使用真实 `FinalService` admission / TypedExecutor，注入测试 SessionProvider，未启动服务：
  三断点在 adopt 后均为 0 Run / 0 效果调用。只有未 admission 案例经独立、显式的
  canonical publish 后成为 1 Run / 1 效果调用；重复 publish/tick 仍为 1/1。

R6-I 正向探针命令：

```powershell
python scripts/run_tests_isolated.py -- python scripts/probe_pending_adoption.py --output evidence/r6j-pending-adoption/identity-gate.json
```

退出 0，**三断点均通过**；每例结算后 1 Event / 1 drive / 0 canonical pins，
adopt 时两份 legacy 数据库 bytes 均不变；settle 后 source bytes 仍不变。
[探针结果](identity-gate.json)、[探针日志](identity-gate.log)。

初次独立探针在三例断言后遇到 Windows 临时目录清理失败：sqlite3 连接上下文不会
自动 close。已改为显式 `contextlib.closing`，同一探针包括 cleanup 重跑退出 0，
且最终组合回归通过。原失败保存在 [probe-initial-failure.json](probe-initial-failure.json)。
早期专项/组合 XML 和日志保留；最终验收以 regression-final 和对应哈希为准。

## R6-I 可重试条件及边界

1. 集成这个候选后，采用新增显式 adoption 入口重跑三断点；不要把原有 negative
   composition/v1-writer 探针的拒绝改成成功。
2. 使用同一 profile 的 reviewed history、原 sourceId/eventId/drive key、原 timezone
   和持锁 owner；重复调用保持相同 import/source/timezone。
3. awaiting_admission 继续保持 pending；确需执行时由已授权调用者显式调用现有
   canonical EventBus/admission/execution，不能自动重投。已有旧 commit 不重新选 target。
4. adopted_uncertain、冲突或不可读取的原证据不算成功。恢复原 identity 的真实
   committed envelope 后才可重新核对；旧效果 unknown 仍按原 identity 查询，不重试。
5. publicationSettled 只证明 publication/cursor/pin handoff，不能证明旧业务效果成功。
   本轮不授权删除 legacy host。

`time_events.py`、`store.py`、`legacy_bridge.py`、`final_contracts.py` 及生成 schema
均相对基线未变，未删除 legacy hosts。实现与测试均使用独立临时根。
未运行真实 HTTP/浏览器、远端 CI/三平台执行、真实用户数据迁移、部署或人工验收。

自动审批拒绝了首次失败探针临时目录的递归清理，工具仅返回 `blocked by policy`；
该目录保留。修复后的探针临时目录已由正常 TemporaryDirectory 流程清理。
