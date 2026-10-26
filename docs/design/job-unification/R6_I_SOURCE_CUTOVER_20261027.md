# R6-I source cutover：显式接管与 canonical 持续驱动

前置：[P22 pending adoption](PENDING_PUBLICATION_ADOPTION_20261026.md)。P22 的
普通 composition 负控、通用 v1 writer 拒绝和原 receipt/pin 结算顺序均保持。

## 能力与调用者

| 能力 | 入口 | 约束 |
|---|---|---|
| 原 schedule normalize / iso / anchor / next-fire / misfire policies | `jobs.schedule_calculations` | 无 registry/profile/host import；既有墙钟与秒精度行为保持。canonical 分数/DST/次数规则仍由 `scheduler.providers.TimeProvider` 提供 |
| Job 根路径 | `core.platform.data_paths.job_root` | Scheduler env > Background env > default；纯 Path 解析，无创建/迁移副作用 |
| scheduled record 投影/构造、原 ID 查找、安全删除与 append-run 证据 | `jobs.scheduled_records` | 构造可显式传 root；删除与 append 保留原 legacy reservation、protected Action 检查；没有 scheduler store import |
| 原 source 的只读 list/get/references/snapshot | `scheduler.source_history.SourceHistory` | SQLite `mode=ro` / query_only / snapshot 读事务；缺库不创建；未知字段原样保留；不 normalize、reserve 或启动 runtime |
| 新 source 的定义、状态、引用、reserve/publication/ack/expire | canonical `/api/event-creators`、CreatorStore / CanonicalEventStore / CreatorRuntime | 既有 v2 契约；不存在第二个调度 loop |
| 旧未结算 source 的接管 | `FinalService.scheduler_migration(legacy_events)` | 显式用户操作、明确原 EventStore、先导入审阅的完整历史；不通过 startup 发现或扫描 |

`scheduler/event_api` 的 GET 保留旧列表协议与原 source ID；即使未绑定历史 Links，
也从 `source_links` 返回完整引用。旧 POST/PUT/DELETE 持续认证后 410，直接挂载
该子 router 也不会启动旧 writer。新 source 管理继续使用已有 canonical API，
没有更改 wire schema、开放旧 UI 写入口或新增前端协议。原 source 列表是历史事实，
不能把其 enabled 值解释为 canonical 激活状态。

## 显式步骤

以下是可信宿主内的 Python port，不是新的 HTTP/MCP 路由。`actor` 必须由宿主
认证后传入，不能来自任意请求体。绑定的 service 必须持有该 profile 的有效 owner。

```python
from packages.scheduler.source_history import SourceHistory
from packages.jobs import final_contracts as fc

history = SourceHistory(reviewed_source_root)
service.creators.import_legacy_sources(
    history, request_key=reviewed_import_key,
    expected_digest=reviewed_snapshot_digest,
)
handoff = service.scheduler_migration(original_event_store)
receipt = handoff.adopt(
    import_key=reviewed_import_key, source_id=original_source_id,
    timezone_name=reviewed_timezone, actor=authenticated_user,
)
creator_id = receipt["creatorId"]
```

`adopt` 沿用 P22 identity，不激活 future drives，也不发布 Event。它在完成 P22
跨 store saga 后，安装 canonical 宿主所需的 durable authority，并将新 canonical
Creator 的内部 profile-owner 投影为该显式用户 principal；原 attribution 在
`pendingAdoption.legacySource` 中不变。崩溃后可重入，不依赖原内存对象。

先通过普通 canonical API 创建或更新审阅后的 Job/Trigger successors。每条原
source link 都要有明确的 Trigger/Job ID 映射；映射不得缺边、合并原 ID、猜测内容
相似的对象。successor Trigger 必须显式选择该 Creator，并声明原 pending v1
与 future canonical event type；原 event filter / 参数绑定如需变化，由用户按新
payload 明确审阅。共享 Trigger 的显式多 Creator selector 仍可使用。

```python
handoff.bind_successors(
    creator_id, trigger_map=reviewed_trigger_map, job_map=reviewed_job_map,
    expected_revision=creator_revision, actor=authenticated_user,
)
status = handoff.status(creator_id, actor=authenticated_user)
# 仅当用户明确请求首次 publication 且状态确为 awaiting_admission 时：
receipt = await handoff.publish_pending(creator_id, actor=authenticated_user)
# 对已 committed 的两个断点，直接 settle，不调用 publish_pending。
status = handoff.settle(creator_id, actor=authenticated_user)
```

`publish_pending` 对 agent、committed、settled、uncertain 或未完成 adoption 拒绝。
仅已证明未 admission 的原 identity 可以显式首次 canonical publish。旧 commit
沿 P22 的空 canonical targets 保存，原 target/subscription/delivery/unknown 证据
保留，不选择新 targets 或重投。`settle` 从不 publication 或 execution。

结算后，用普通 `FinalService.control(..., operation="enable")` 明确启用后续
驱动。启用和每次 future drive 都检查 successor Creator/Trigger/Job revision、
原 cohort 映射与实际 creator_links。引用定义改变后必须显式重新审阅绑定，不能
用旧批准备案驱动新的 graph。未结算 adoption 不能通过 canonical enable 激活。

后续 reserve、Event commit、cursor、pin/ack、misfire/expire 和计数完全由普通
CreatorRuntime 完成；不会回调旧 Sources/Runtime。原 ActionRuntime tick 中的
可注入 time_events 驱动钩子已经撤除。

## 验证边界与保留

同一 owner 下，三个断点均已通过接管、必要时的显式首次 publication、结算、future
网格驱动与实际 canonical admission/executor 测试。独立冷 host 能继续 Creator
publication 与同 eventId 的幂等 admission。canonical retire 后原 source ID、links、
requests、pending adoption 与 Event 历史仍可查询；没有物理删除原数据。

**跨 epoch 完整效果验收仍阻塞**：epoch 2 的 host 写出 Attempt 后，持久 Attempt
仍是 epoch 1，执行端却收到 epoch 2，完成回执被既有 fence 拒绝。无 adoption 的
普通 Job 在 `bac726cf` 与本候选均能独立复现；本轮保留红测，不修改 owner/fence。
纯 startup/reopen 与 source publication/admission 的绿证据不能代替该效果验收。

按最新任务边界，`store.py`、`time_events.py`、`legacy_bridge.py` 本轮均保留。
bridge 只更换依赖接口，cursor/receipt/legacy reservation 行为保持，最终退役由 MA
另验。原 profile 的 51 已迁集合与 60 unrecognized 集合没有在本轮扫描、执行、迁移
或删除；合成 fixture 验证不能证明真实全 profile activeLegacy=0。
