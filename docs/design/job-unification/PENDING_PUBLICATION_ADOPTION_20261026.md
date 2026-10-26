# R6-J：旧 Scheduler pending publication 的同 identity 接管

本契约在 `f0970e23` 上增加显式的、受 canonical owner fence 保护的迁移入口。
普通 `legacy_source_plan` / composition 继续拒绝 unsettled 来源，普通 Event writer
继续只接收 schemaVersion=2。没有启动扫描、自动补发或 legacy host 删除。

## 入口与 identity

先通过 `CreatorStore.import_legacy_sources` 保存已核对 digest 的完整快照。然后调用：

```python
receipt = creators.adopt_legacy_pending(
    events, legacy_events,
    import_key="reviewed-history-key", source_id=original_source_id,
    timezone_name="UTC",  # 必须是已审阅的原始时区，不能猜测主机时区
)
port = PendingPublicationAdoption(creators, events, legacy_events)
receipt = port.status(receipt["creatorId"])
```

三个 store 必须属于同一 profile，两个 canonical store 必须使用同一持锁的
`OwnerFence` 对象；旧 EventStore 只用于读取原始证据和显式结算时释放原 pin。
新 Creator 初始为 disabled；接管不等于激活未来驱动或授权业务执行。

v1 没有独立 occurrence/drive 字段。原始 reserve identity 是
`["scheduler-event-v1", sourceId, sourceRevision, scheduledAt原字符串]`。
接管保存这个列表为 `identity.driveKey`，重新计算并校验其原始 SHA256 eventId，
保留 eventId；`driveId=原eventId`，`occurrenceKey="time:legacy:"+原scheduledAt`。
来源时区只用于 canonical cursor，不改写 drive key。原始 v1 envelope、source、
source_links、source_requests，以及旧 EventStore 的 targets、订阅回执、deliveryCount、
lastError、receivedAt、pin 均保存为证据。只接受已知 Scheduler v1 形状和匹配的
source/revision/payload；扩展或模糊来源仍拒绝。

canonical Event 表中的表示是经过原有 v2 schema 校验的投影，使用原 eventId 和
原 payload。原始 v1 envelope 单独保留，两个 digest 的语义不混淆。已 commit 的
旧事实复制到 canonical Event 表时 targets 为空；保留的旧 target/receipt 不转换成
一次新的 canonical admission。

## 回执与状态

回执 `recordType=scheduler_pending_adoption, version=1`，同时提供：

| 字段 | 含义 |
|---|---|
| `phase=prepared/adopted`、`adoptionComplete` | 两个 SQLite store 的接管是否均已提交 |
| `state=adopted_pending` | identity 已接管，publication 仍待显式结算 |
| `state=adopted_uncertain` | 缺少原 admission 证据；不允许重新投递或假装成功 |
| `publicationState=awaiting_admission/committed/uncertain/settled` | 按真实 canonical Event 记录和结算回执查询 |
| `cursorAdvanced` | 导入时保留旧 cursor 状态；随后只由持久 Event 回执推进 |
| `eventStoreReleased`、`publicationSettled` | 显式结算完成前均为 false |
| `execution=not_requested/preserved_only` | 不代表业务效果成功；旧 uncertain/error 回执保持原样 |
| `legacyEvidence` | 原库的 commit、pin、target、dispatch、subscription 证据 |

`publicationSettled` 只说明 publication/cursor/pin handoff 完成，不能用来证明
旧业务效果成功。`legacySource.publishCursor.eventStoreReleased` 保持历史原值，
不能改为 true 来伪造旧 runtime 的 ack。

## 三断点与显式结算

| 断点 | adopt 后 | 允许的下一步 |
|---|---|---|
| reserve 后、未 admission | `awaiting_admission`，原 cursor 未推进，ack=false | 保持 pending；需要执行时由已授权调用者显式调用现有 canonical `EventBus.publish`，沿用接管的 envelope/fence |
| 旧 EventStore commit 后、cursor 未 advance | `committed`，原 cursor 未推进，ack=false | `port.settle(creatorId)` 查询持久事实后推进同 drive 的 cursor |
| cursor advance 后、未 ack | `committed`，保留已推进的 cursor，ack=false | `port.settle(creatorId)` 沿同 identity 完成 handoff，不再选择订阅或调用旧效果 |

`settle` 不 publish、不 admission、不 execution。它从 canonical EventStore
查询并校验 eventId/factDigest，再按以下顺序执行：

1. 原 drive 的 canonical cursor 和 `lastPublication` 提交；producedCount 仅记一次。
2. 校验原库同 eventId 的原 envelope，释放原 Event pin；不改旧 source/links/requests、
   Event 内容、target、订阅或 delivery 状态。
3. 释放并查询确认 canonical pin 已释放。
4. 在 Creator 事务中保存 canonical ack 和 publicationSettled。

Creator 的普通 `advance` / `acknowledged` 和 Runtime 的内部 `_publish` / `_ack`
不能绕过该结算入口；统一 tick 对 unsettled adoption 不执行 publish/ack。
schema 校验未改变。首次显式 canonical publish 可以冻结当前 canonical targets，
后续同 identity 查询/重复 publish 使用已有回执及现有 admission/effect key 去重。

## 恢复与 R6-I 重试条件

Creator prepare → Event adoption → Creator receipt 是可重入 saga，不宣称跨库原子性。
任一边界崩溃后，新的 owner 取得下一个 epoch，用相同 import/source/timezone 重试
adopt；已提交的 event、drive 和 cursor 不重建、不回退、不重计数。cursor 已提交而
pin/ack 未完成时，重试 `settle` 仅完成剩余步骤。

原库不可读取会报错；文件不存在，或旧 cursor 已 advance 但原 commit 缺失，产生
显式 `adopted_uncertain`。unknown 不转换成一次首次 publish。恢复原 identity 的
真实 committed envelope 后，用相同参数重复 adopt 可重新核对证据，再显式 settle；
只有“查无记录”不能消除先前的未知结果。原 envelope 冲突继续拒绝。

R6-I 应改用本显式入口重跑三断点，而非放开旧 composition 或把 v1 envelope 直接交给
通用 writer。正向探针：

```powershell
python scripts/run_tests_isolated.py -- python scripts/probe_pending_adoption.py --output evidence/r6j-pending-adoption/identity-gate.json
```

成功条件：三断点同 identity adopt、初始 pending/cursor/ack 状态保留、重复 adopt
幂等、tick 无自动补发、通用 writer 对 v1 仍返回 invalid_schema、显式结算后只有
一个 Event/drive、pin=0。该结果允许继续 R6-I 的后续评估，不是删除 legacy host、
真实数据迁移或部署授权。旧效果 unresolved 仍需按原 identity 查询，不能自动重试。
