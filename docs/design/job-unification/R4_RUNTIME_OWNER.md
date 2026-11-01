# R4 runtime owner 与离线包激活

R4 候选只接管受同一个 **profile 根** 约束的运行时写入。测试只使用合成 fixture。
这不是生产数据迁移签收；真实旧进程排空、旧版本二进制停写、外部独立服务根目录与部署版本仍须在生产接管前单独证明。

## Owner 协议

- authority 位于 `<profile>/job-runtime-owner.sqlite3`；进程存活锁位于同根 `job-runtime-owner.lock`。
- canonical runtime 启动先取得 OS 非阻塞独占锁，再在 SQLite `BEGIN IMMEDIATE` 中比较预期 epoch；
  只有 CAS 成功者持久化 `epoch+1,nonce,activating`。存活锁一直持有到 runtime 关闭，进程崩溃由 OS 释放锁。
  不按超时或 PID 猜测替换活 owner，不删除锁文件。
- canonical 的定义、Run、Attempt、Effect、Creator cursor、Event 和 outbox 写入都在 authority guard 内完成，
  token 必须匹配当前 epoch/nonce；本地实例必须仍持有存活锁。旧实例可读，不可提交迟到收据或新写。
- 旧 store 的同步提交与 owner claim 共用 authority 事务；跨线程/HTTP/await 的旧 pass 使用 durable reservation，
  不持有 SQLite 事务等待另一个 store/宿主。claim 看到任何 reservation 都拒绝，不能把未知结果当作已排空。
  两种机制分别覆盖实际提交与整段旧效果；runner 的 reservation 覆盖整个效果生命周期。
- 状态为 `activating → active → closed`，激活失败为 `failed`。`closed/failed` 均不重新开放旧写。
  重启取得更高 epoch；不能用同一个实例的旧 token 重入。遗留 reservation 不自动清除，必须另行核实原执行归属。

## 显式启动配置

`server.lifespan` 读取已有配置中的可选 `job_runtime_activation` 对象，传给 `FinalService.start`。
下面只解释合同，不提供真实 profile 的可直接运行配置：

```json
{
  "job_runtime_activation": {
    "path": "C:/Temp/owned-fixture/canonical.json",
    "expected_hash": "<原文件 bytes 的 SHA-256 hex>",
    "expected_plan_digest": "<审阅过的 WP08 planDigest>",
    "active_ids": ["<显式批准启用的 canonical ID>"]
  }
}
```

该包必须在 WP08 source root 外，source root 必须等于 authority 的 profile 根；canonical 对象的 profileId
必须匹配目标 FinalService。导入器只接受 WP08 v1 committed-offline 包中的完整 schemaVersion 2 对象，
校验 raw hash、bundleDigest、planDigest、唯一 ID/分配图、read-only/finalized 标记、Action/provider 引用和当前授权。
不读取或转换源旧 schema，不从 `source=agent/system` 推定权限。

Job/Trigger/Creator 先以 disabled 导入，Template 为 draft；只有 `active_ids` 中且离线 `desiredState=enabled`
的非 Template 定义才 enable。整图、链接、授权与 runtime activation receipt 在一个 Creator DB 事务中提交。
任何引用/权限/校验/提交异常都回滚导入，runtime 不启动，旧写仍关闭。

启动前持久化审阅配置的 intent digest。失败后不能通过省略包或更换配置绕过；完整已提交的图重启可直接恢复，
同包重放只核对 receipt，不重置配置、cursor、已自动停用的 Creator、Run 或 outbox。
首次导入按包中 nextFireAt 恢复单规则 time Creator，保留原 cursor 审计，不从 createdAt 重新起算。
nextFireAt 必须落在 canonical 规则网格上；无时区且 canonical 规则没有保留时区的 once/interval 游标、
DST 歧义、计数域不匹配、活动定义缺少 next-fire 均拒绝激活，不能猜测主机本地时区。
WP08 包自身保持原 bytes，仍标记 `runtimeActivated=false`；真实运行激活收据存在 runtime store，不伪改离线证据。

## 唯一生产者与恢复

lifespan 不再启动旧 scheduler/message recovery pass、旧 hooks 注册或旧通知恢复。
automation 在 canonical owner 下只运行 Event ingress/delivery → TriggerAdmission，不启动旧 Job Runtime、time source
pass、TriggerRun loop 或旧 ledger GC。旧 TriggerRun bytes 保留可读；新 admission 的去重事实存在 canonical DB。

Run 的执行、retry、settlement 和通知只从 canonical aggregate/outbox 驱动。通知使用稳定 outboxId；先提交 sending，
再调用 sender，明确成功才 ack。发送中断/宿主丢失为 uncertain，不自动重发。支持的 session sender 继续使用
enqueue_notice 的原 ID 去重；未绑定的通道显式 uncertain。WS 是投递调用已完成的收据，不宣称浏览器已收到。

重启推进全局 owner epoch 与 Creator fence，新 Attempt 带当前全局 epoch。WP05b 收据按原 effectId/Attempt fence
查询，已确定结果写回原 Run；不再 submit。活执行、缺收据、错 epoch/generation、错误或未知结果按 WP02 收敛 uncertain，
不把 unknown 当作完成/取消，也不重做成功 Step。已提交 pending Creator publication 保留原 Event/drive 身份恢复。

## 运行边界

- 默认 sibling stores：`<profile>/background_jobs`、`<profile>/automation`、`<profile>/<independent-root>`、`<profile>/job-final`。
  所有参与者必须运行包含 fence 的版本并指向同一 authority；不支持从不同 profile 根推定同一 owner。
- Windows 与 POSIX backend 共用同一协议和测试断言；本轮只在 Windows 执行。未知平台返回 capability_unsupported。
- 不提供跨主机/NFS 一致性，不证明真实断电持久性，不自动清除未知 reservation，不自动取消真实活资源。
- 旧 store/pass/table/handler 保留至 R5/R6；未改 React、MCP 或 CLI。

fixture 验收入口为 `tests/test_job_runtime_owner.py`；精确计数、测试工件和未测层见 R4 交付报告。
