# P24 后继：canonical pin 权威冲突及持久通知边界

工作树 `D:/project/pan-worktrees/job-p24-retained-history-20261027`，分支
`r6/p24-retained-history-20261027`。本轮起点为 MA 已审查的
`075d857a1f6f39cfe6998edff7e621417770b864`，其历史读取/离线 metadata/bridge 证据保持原样。
接口依赖仍为 P21 `bdd4c778d55daff58529635b2adc464c03a1393c`，没有将其提升为整体已接受；
集成仍为 `bac726cf84c0510dff8618afb82e227e660f590c`。

**测试源码 SHA：`98b58048cb6051de406103047c359466b218a2de`。**
新增 `tests/test_job_retained_canonical_pins.py`；产品代码零修改。
最终新增矩阵 **18 tests / 16 passed / 2 failed / 0 errors / 0 skipped / exit 1**。
两条严格红分别证明同一实际产品缺口的物理删除与 GC 路径，保留红测等待 MA 协调修复。
不能把这个 clean 提交称为 S13 已关闭。

## 权威合同与实际实现

本轮重新核对 [终局模型](../design/job-unification/JOB_FINAL_MODEL_20261025.md) §2.3、§10.1、
§10.2/10.4、S11/S13、FR04/FR05 与
[R5/R6 计划](../design/job-unification/R5_R6_EXECUTION_PLAN_20261026.md) G4、S11/S13/U12、FD04。

- 原生 Job schema 有 `retention:{policyRef,pinned}`，pinned 默认 false；定义更新必须
  `expectedRevision` CAS，caller-scoped requestKey 持久化 operation，unknown 查询原身份。
- 保留 pin/retention，pinned/active/unknown/cleanup_pending 必须阻断 GC；没有要求 native Job
  自动读取旧 `pins.json`。旧复杂数据与历史仅作为非执行证据保留。
- 唯一终态 outbox 以稳定身份持久化，durable enqueue 后才 ack；未知不自动重发。
  原子事务、通知落盘与 WS 发出是三个不同边界，不能互相代替。

实际权威接口如下，均从现有源码确认，没有自造路由：

| 层 | 现有接口/位置 | 已确认行为 |
|---|---|---|
| native config pin | `FinalService.write`，`final_api.definition_routes` 的 POST/PUT `/api/jobs[/id]` | 定义的 `retention.pinned` 与 configRevision、operation receipt 在 Creator SQLite 同一事务提交；GET/list 返回 canonical 定义 |
| runtime metadata pin | `FinalRuntime.metadata/set_pin`，`final_runtime/coordinator.py:239/248` | metadata 自有 revision CAS；`runtimeMetadata.pinned` 优先，缺失时才使用 config.retention.pinned；底层 AggregateStore 加入同一 owner/SQLite 事务 |
| 物理删除/GC | `FinalRuntime.deletion_blockers/delete/retention`，`:384/412/434` | 检查 metadata pin、运行/效果/cleanup/outbox/引用等，删除 live definition 并保留 runtime_archive；GC 在删除事务重验摘要 |
| HTTP DELETE | `final_api.definition_routes.delete` | **仅 retire**，不暗含资源清理或物理 purge。本轮没有把 retire 成功当成 pin 安全缺陷 |
| Run settlement | `FinalService._settle`，`:813` | Run summary outbox 与 operation 终态在同一事务；默认产生 ws row，原 key 为 `canonical:<runId>`，逐接收人有稳定 outboxId |
| 发送/恢复 | `FinalService.deliver_outbox/recover`，`:1003/832` | 发送前 pending→sending 先提交；成功回执才 ack，失败/丢失回执 uncertain；重启 sending→uncertain，不自动重新发送 |
| 产品发送适配 | `packages/web/server.py:send_final_notice`（startup 中的闭包，约 :337） | ws 调 broadcast；session 通过真实 `worker.enqueue_notice(event_id=outboxId)` 落盘。未承诺 config create/update 复刻旧 `job.created/job.updated` 广播 |

因此没有要求 canonical CRUD 使用旧 pins.json，也没有新增“创建/编辑必须广播旧格式”的伪测试。
定义更新的持久成功由 operation 证明；终态通知由 outbox/发送回执证明。当前实现确有 terminal
ws sender，但本轮未运行完整生产 lifespan 或真实 WS 客户端，不声称已验证该 wire。

## 实测结果与严格红

所有以下测试位于 `tests/test_job_retained_canonical_pins.py`。fixture 复用真实 native HTTP
test harness，激活自有 canonical owner；重开新 FinalService/SQLite 连接并替换实际 ASGI
service binding，不复制内存状态。Session provider 为记录型端口，存储、路由、授权、CAS、
Run/Attempt/effect/outbox 和 owner fence 都是产品实现。

| node / 参数 | 实际证明 | 结果 |
|---|---|---|
| `test_old_pin_corruption_never_enters_native_crud_run_or_retention[json/encoding/read]` | 旧根与 canonical 根放置损坏 pins.json；创建、定义更新、GET/list、Run、operation 查询、retention/archive 均走实际产品路径；native pin 默认 false，不读取旧文件，坏字节不变。read 分支在任何读取尝试时抛 PermissionError，实际读取次数为 0 | 3 passed |
| `test_http_config_pin_cas_operation_and_disk_reopen` | HTTP pin true、configRevision=2、stale PUT 409 且数据库字节不变；重开 GET 与 operation 同值；pin 阻断 delete/GC；HTTP unpin 后允许删除并保留历史 | 1 passed |
| `test_runtime_metadata_pin_cas_persists_without_mutating_config_or_runs` | 独立 metadata revision=1，stale CAS 明确冲突且磁盘不变；重开 pin 保持，config/Run 不被改写；该 pin 阻断删除/GC | 1 passed |
| `test_pin_write_failure_rolls_back_disk_and_has_no_success_or_outbox[http-config/runtime-metadata]` | 在真实 `_save` 执行 SQL 后、事务提交前注入 OSError：HTTP 503 storage_unavailable 或底层抛错，磁盘字节完全不变、pin 未更改、无 operation 假成功/无 outbox/无 provider 调用 | 2 passed |
| `test_lost_pin_response_queries_original_operation_without_reposting` | 真实 HTTP write 已提交后注入响应丢失，收到 503；重开后按原 requestKey/operationId 查询同一完成 receipt，configRevision 只增加一次；没有第二次 PUT 或任何效果/通知 | 1 passed |
| `test_canonical_unknown_pin_and_cleanup_pending_block_delete_and_gc` | 持久 canonical metadata pin=None/true、cleanupPending=true、status=cleanup_pending 四种合成状态，真实 delete 与 retention 均 fail closed；未知 pin 的 metadata 查询明确异常，原文档不变 | 4 passed |
| `test_http_pin_true_must_protect_gc_after_runtime_unpin[delete/gc]` | 先 runtime.set_pin(false)，再 HTTP PUT retention.pinned=true 成功，disable 后物理删除/GC 必须拒绝 | **2 failed：实际放行** |
| `test_pending_terminal_outbox_reopens_and_acks_same_key_without_reexecution` | 完成 Run 后 outbox pending 可重开；sender 被调用前 sending 已落盘；原 key/outboxId 发送一次并 ack，再驱动不重复效果 | 1 passed |
| `test_notification_ack_commit_failure_recovers_uncertain_without_resending` | sender 已成功后 ack 的真实 SQL 写入后注入失败，事务回滚保留 sending；重开变 uncertain，不重发；原 recipient row/key 回执显式 settle 且重复同回执幂等 | 1 passed |
| `test_unknown_run_queries_original_operation_and_never_resends_effect` | provider unknown 后 operation=unknown、Run=uncertain；重开按原 requestKey/Run ID 查询，多次 dispatch 不新增 Run/Attempt 或 provider 调用 | 1 passed |
| `test_session_notice_is_durable_before_ack_and_settles_original_row_after_restart` | 真实 worker.enqueue_notice、Session ordered writer 与磁盘 JSON：原 outboxId 作为 eventId，通知先持久化，再注入 outbox ack 提交失败；清缓存重读队列，原 item 身份保持，重开不重发；查询该 durable enqueue 后显式 settle 原 row | 1 passed |

Session 通知测试只隔离 `_wake_worker` 与 `_schedule_auto_force`，防止启动 Worker/自动强制执行；
没有替换 save/save_async、queue append、dedup 或 outbox。Session provider 与 sender 是明确的
测试端口；session sender 使用生产适配器同样的 `worker.enqueue_notice` 调用，但不是直接执行
startup 中不可单独调用的闭包。ws 行仅观察 sender 边界，未发送真实 WS。

**红测精确原因**：`FinalService.write` 更新 `stored.canonical` 与 `stored.config`，保留旧
`runtimeMetadata`；`FinalRuntime.deletion_blockers` 使用
`metadata.get('pinned', config.retention.pinned)`。因此历史 runtime false 会覆盖随后已成功提交的
native HTTP true。两条红不依赖旧 pins.json、不经过旧 writer、不触碰真实独立进程。

最小复现顺序：create draft → `final.set_pin(id,false,expected_revision=0)` → HTTP PUT
`retention.pinned=true,expectedRevision=1,requestKey=pin-update`（200/configRevision=2）→
enable/disable → `final.delete(expected_revision=2)` 或 `final.retention(...)`。
前者没有抛预期 ContractError；后者实际返回 `deleted=[原Job ID], retained={}`。
数据库 live definition 被删而 runtime archive 保留；这是原生 pin 未保护**物理定义删除**，
没有声称测试删除了 OS 进程或用户文件。

## MA 所需最小接口补丁方案（未实施）

1. **先封住安全缺口**：在 `FinalRuntime.deletion_blockers` 统一校验 config 与 runtime pin。
   两者有任一 true 必须返回 pinned；任一未知/类型坏或权威冲突不能被 false 遮蔽，应明确
   pin_metadata_unknown/conflict 并阻止 delete/retention。复用同一解析逻辑供 metadata 查询，
   避免查询称 false 而 HTTP 显示 true。不要让旧 pins.json 介入 native 权威。
2. **协调唯一权威**：schema 已对外允许 retention.pinned；runtime 又有独立 metadata CAS。
   需 MA 明确二者关系并修改共享 `FinalService.write` 与 `FinalRuntime.metadata/set_pin`：
   在同一事务同步明确 pin 变更及 revision，或拒绝冲突并要求调用者重新查询/确认，不能无声
   保留遮蔽值。若保留独立 metadata CAS，管理接口必须携带其 expected revision；定义更新
   不应以旧快照覆盖并发 metadata pin。此项是接口协调建议，不是本 TA 自行冻结新 wire。
3. 保留 existing `requestKey/operation` 事务与 unknown 查询协议；无须为了 pin 更新新增
   terminal outbox 或复刻旧 WS 格式。修复后只复跑新增两红与相关 CAS/失败/重开测试，随后由 MA
   决定组合回归。禁止仅把红测改成接受被删除。

通知落盘与原 key 恢复在本次指定的端口/Session 持久层没有发现需修复缺口；完整生产 sender
绑定、真实 WS、接收方真实 CLI 消费仍是未验层，不能据这些单测声明端到端通知验收。

## 命令、计数与保留证据

实际完整 argv/cwd/exit/逐 node 结果和 hash 见
[validation.json](../../evidence/p24-canonical-pins/validation.json)。每轮均经过
`scripts/run_tests_isolated.py`，没有继承真实 PAN_*，所有存储为 pytest 自有临时根。

```powershell
python evidence/p24-canonical-pins/run_matrix.py final tests/test_job_retained_canonical_pins.py
```

该入口实际执行 `python scripts/run_tests_isolated.py -- python -m pytest
tests/test_job_retained_canonical_pins.py -q --tb=short --junitxml=<final.xml>`。

| artifact | tests | passed | failed | errors | skipped | exit |
|---|---:|---:|---:|---:|---:|---:|
| first：draft 不能直接 disable；异常 message 大小写不应作 revision 断言 | 16 | 9 | 7 | 0 | 0 | 1 |
| second：修正 fixture 使用 enable→disable，并改验 revision_conflict code | 16 | 15 | 1 | 0 | 0 | 1 |
| session-boundary：新增真实持久通知边界 | 1 | 1 | 0 | 0 | 0 | 0 |
| **final：两条严格红分别执行 delete/GC** | **18** | **16** | **2** | **0** | **0** | **1** |

最终 [JUnit](../../evidence/p24-canonical-pins/final.xml)、[log](../../evidence/p24-canonical-pins/final.log)、
[命令](../../evidence/p24-canonical-pins/final.json)。各轮不相加；所有失败实验保留。
pytest 红测输出中的两处行尾空格仅在审阅副本去除；所有原始 log/XML 字节保存在
`raw-output.zip`，前后 SHA-256 见 `artifact-whitespace.json`，没有改断言、结果或计数。
本轮最终日志有 1 条现存 FastAPI/Starlette httpx 弃用 warning，无新增 skip/xfail。
旧 18 节点矩阵没有重复运行；离线六分支、pin 原子写后继、bridge、前轮报告/原始红证据
均按 Git blob 核对保持，清单在 validation.json.preserved。

源码提交使用正常 hook，`git diff --check` 通过。本轮仅新增上述测试、本报告及
`evidence/p24-canonical-pins/`；final_service/final_api/coordinator 等共享产品文件、
conftest/support、React、P23 文件和三个 legacy host 均未修改。未派子 TA、未 merge/push、
未动 main/practical、未读取/迁移 51/60 真实集合或真实 data/profile、未操作保护服务或监听端口。

未验证：TCP/完整生产 lifespan/真实 WS、真实 provider/CLI 消费、OS process cleanup（P23 所属）、
前端结构化编辑（P21 所属）、全仓/远端三平台 CI、真实迁移、部署与人工验收。
本候选只证明上述实测层与两条保留红；补丁方案是代码推断与建议，尚未实施验证。
