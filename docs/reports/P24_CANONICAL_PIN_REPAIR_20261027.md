# P24 canonical pin 双保护来源最小修复（2026-10-27）

本候选封住已被 MA 独立确认的两个 S13 产品缺陷：已提交的 config pin true 不再被
runtime metadata false 遮蔽；metadata 查询与 delete/retention 共用相同的有效 pin 规则。
原两条严格红断言不变，现均通过。仅交付本范围的待集成候选，不声明整体 S13、P21 或 R6 验收。

- 输入 HEAD：`e2c12fad1021584cda8a9e2a00f0bde841e7b375`，开始时工作区 clean。
- 源码 SHA：`24054a11c1d7c71fc676e2bf6cee9272d61d8f82`。
- 分支：`r6/p24-retained-history-20261027`；未 merge/push，未推进 main/practical。
- 接口固定基线仍为 `bdd4c778d55daff58529635b2adc464c03a1393c`，并非整体 MA 接受；
  集成基线仍记录为 `bac726cf84c0510dff8618afb82e227e660f590c`，本轮没有操作这些目标。

## 权威合同与本轮实现

[JOB_FINAL_MODEL](../design/job-unification/JOB_FINAL_MODEL_20261025.md) 的 Job retention 字段
允许 `pinned`、缺省 false，要求保护可恢复窗口；
[R5/R6 计划](../design/job-unification/R5_R6_EXECUTION_PLAN_20261026.md) S13/U12/G4 要求 pin、
unknown、cleanup_pending 的安全门禁接管。两份设计不要求 native canonical 读取旧 pins.json，
也不要求复刻旧 pin WS 广播。前轮报告提出的同步两个写入来源建议，现由 MA 的明确决策替代：
保留两个独立的既有写入协议，不合并 revision，不新增 HTTP wire，不改 FinalService.write。

| 来源 | 既有写入与 CAS | 解除条件 | 对外值的含义 |
|---|---|---|---|
| `config.retention.pinned` | HTTP Job definition PUT；configRevision + requestKey/operation | 新鲜 configRevision 下显式写 false | HTTP definition 的 retention.pinned 仍表示这个配置来源 |
| `runtimeMetadata.pinned` | FinalRuntime.set_pin；metadata revision | 新鲜 metadata revision 下显式写 false | 仅写这个来源，不改 config、configRevision、Run、operation 或通知 |
| 有效 pin | FinalRuntime.metadata、set_pin 回执、deletion_blockers 共用 effective_pin | 两来源均无 hold 且可判定才为 false | `pinned` 表示有效保护；回执 revision 仍仅属于 metadata CAS |

任何来源明确 true 都不能被另一个 false 清掉。任何来源显式非 bool（含 null、字符串、0/1），
或 config/retention/runtimeMetadata 容器不是 dict，查询抛 PinMetadataUnavailable，
delete/GC 产生 pin_metadata_unknown 并阻断。即使另一个来源 true，也不能把损坏掩盖成可用查询。
仅缺失容器/字段按兼容默认 false。旧 pins.json 不加入 native 权威。

metadata 查询或 setter 回执中，如果有效值与 metadata 自身 hold 不同，最小附加诊断字段
`metadataPinned` 表示 metadata hold；`pinned` 始终为有效值。例如 config=true 后
`set_pin(false, expected_revision=1)` 提交 metadata revision 2，回执为
`{"pinned":true,"revision":2,"metadataPinned":false}`。这表示本次 metadata hold 已解除，
但 Job 仍受 config hold 保护，不能把这次操作宣称为 Job 已 unpin。
HTTP GET 的 definition 并非有效 hold 合并查询；配置 false 与有效 true 可以并存，配置 true
与有效 false 不可并存。双 false 时不增加诊断字段，原有无冲突回执形状保持。

若 config 来源不可判定，setter 的拟写入经过相同解析后抛错，整个事务回滚，不产生成功回执。
损坏的 runtimeMetadata 容器拒绝 setter；显式 metadata CAS 可以覆盖其自身损坏的 pinned 字段，
不会借此修改 config。后一个修复坏字段的行为是实现推断，本轮未单独验收其恢复流程。
metadata setter 没有 requestKey/operation 协议，本轮没有增加；发生未知响应时应按原 Job ID
读取 metadata/revision，不能把配置 operation 重投或重复调用 setter当成查询。

产品改动只在 [coordinator.py](../../packages/jobs/final_runtime/coordinator.py)：新增共享解析函数，
metadata/set_pin 使用它，deletion_blockers 的 pin 段捕获不可用并阻断。坏 metadata 容器在
已记录 pin_metadata_unknown 后仅对后续既有 cancellation 检查使用空本地视图，未修改存储、
未放行删除，也未改变非 pin cleanup 的算法。

## 实测与原断言映射

所有测试通过隔离 wrapper，HTTP 为 ASGI TestClient，实际 FinalService/FinalRuntime、owner
事务与临时 SQLite 存储均使用产品实现。写失败注入在真实 `_save` 已执行 SQL 后抛 OSError，
检验事务回滚、数据库字节不变、磁盘重开一致，而非用 fake pin store 返回成功。
没有启动 TCP 服务或操作真实进程、真实 data/profile、保护端口或 51/60 真实集合。

| 原 node / 断言 | 严格后继与结果 |
|---|---|
| `tests/test_job_retained_canonical_pins.py::test_http_pin_true_must_protect_gc_after_runtime_unpin[delete]`：HTTP 已提交 true 后物理删除必须抛 ContractError/pinned | 同 node、同源码、同断言；原失败，现通过 |
| 同函数 `[gc]`：deleted 必须为空且 retained 原 ID 含 pinned | 同 node、同源码、同断言；原失败，现通过 |
| 前轮 canonical 其余 16 节点 | 同 node 重跑全部通过；完整 18→18 映射见 validation.json |
| 原历史读取/离线 metadata 六分支/bridge/pin successor 等 18 节点 | 使用前轮完整 argv 节点重跑，18 通过；原断言映射继续保留在前轮报告和 evidence |

新增 [test_job_retained_canonical_pin_resolution.py](../../tests/test_job_retained_canonical_pin_resolution.py)
40 节点：

- 8：两种写入顺序 × 两种解除顺序 × delete/GC；重开后单一 hold 仍阻断，双方解除后才删除。
- 1：无关 HTTP name 编辑保留 runtime hold；stale config CAS 和 metadata CAS 均拒绝，
  数据库字节与双方 hold 保持，重开后仍阻断。
- 24：12 种配置/metadata 值或容器损坏 × 另一 hold false/true；查询明确不可用，
  物理 delete 与 GC 都 fail closed；未知 config 或坏 metadata 容器下 setter 不落盘。
- 2：HTTP/runtime 两条实际 release 写入失败；既有双方 hold、revision、磁盘字节保持，
  重开后查询/门禁一致，失败配置 operation 不存在，没有虚假 outbox、provider 调用。
- 5：缺失容器/字段的兼容默认门禁，不将“缺失”改成损坏。

聚焦既有 pin 安全回归 16 节点覆盖：原子 replace 失败原字节及临时文件清理、旧 writer fence、
旧 pin 损坏 retention、metadata 并发 CAS/owner fence、10 个删除门禁、扫描后并发 pin 的重新检查、
日志资源 pin 防回收。本轮未执行 P23 的 independent/process cleanup 测试文件。

## 命令与证据

完整实际 argv/cwd/exit、逐节点结果、原节点映射、SHA-256、163 个既有文件 Git blob 保留检查见
[validation.json](../../evidence/p24-canonical-pin-repair/validation.json)。原红和前两轮报告/evidence
均保持原 blob，包括 R6-I 原 41/43 红记录；没有以本次绿矩阵覆盖原始失败记录。

```powershell
python evidence/p24-canonical-pin-repair/run_matrix.py new tests/test_job_retained_canonical_pin_resolution.py
python evidence/p24-canonical-pin-repair/run_matrix.py canonical tests/test_job_retained_canonical_pins.py
$prior = Get-Content evidence/p24-retained-history/final.json -Raw | ConvertFrom-Json
$nodes = $prior.command[6..14]
python evidence/p24-canonical-pin-repair/run_matrix.py retained-final @nodes
python evidence/p24-canonical-pin-repair/run_matrix.py safety tests/test_job_runtime_registry.py::test_atomic_replace_fault_preserves_bytes_and_cleans_temp tests/test_job_runtime_registry.py::test_json_writers_and_pins_are_fenced_but_old_bytes_remain_readable tests/test_job_runtime_registry.py::test_legacy_retention_obeys_pin_and_corrupt_metadata tests/test_job_runtime_registry.py::test_canonical_metadata_cas_uses_owner_transaction tests/test_job_runtime_registry.py::test_deletion_gate_is_kind_independent tests/test_job_runtime_registry.py::test_retention_rechecks_pins_and_activity_even_when_config_revision_unchanged tests/test_job_runtime_registry.py::test_log_retention_cannot_collect_pinned_resources
python evidence/p24-canonical-pin-repair/manifest.py
```

runner 实际执行 `python scripts/run_tests_isolated.py -- python -m pytest <nodes> -q --tb=short
--junitxml=<对应文件>`；manifest 在上述源码提交上生成。

| run | count | pass | fail/error | skip | exit | JUnit |
|---|---:|---:|---:|---:|---:|---|
| new | 40 | 40 | 0/0 | 0 | 0 | [new.xml](../../evidence/p24-canonical-pin-repair/new.xml) |
| canonical | 18 | 18 | 0/0 | 0 | 0 | [canonical.xml](../../evidence/p24-canonical-pin-repair/canonical.xml) |
| retained-final | 18 | 18 | 0/0 | 0 | 0 | [retained-final.xml](../../evidence/p24-canonical-pin-repair/retained-final.xml) |
| safety | 16 | 16 | 0/0 | 0 | 0 | [safety.xml](../../evidence/p24-canonical-pin-repair/safety.xml) |
| retained（初次节点截取漏 mixed-list，随后纠正并完整重跑） | 17 | 17 | 0/0 | 0 | 0 | [retained.xml](../../evidence/p24-canonical-pin-repair/retained.xml) |

最终完整范围为 92 个不同节点，全部通过；初次 17 节点不重复计入。每次日志有 1 条现存
FastAPI/Starlette httpx 弃用 warning，未新增 skip/xfail。原 pin successor 和两条严格红测试源码
未变；没有降低断言、吞 unknown、恢复旧 writer 或修改已有证据。

## 通知与未闭合层

三个边界仍明确区分：pin 写入自身的 SQLite 原子提交；Run 终态通知 outbox 的持久化与
原 key 恢复；真实传输/WS 广播。pin 设置不伪造终态 outbox 或旧 WS 广播格式。
前轮实际产品 outbox pending→重开→同 key ack、ack commit 失败→uncertain→原 key settle、
Session notice 文件先落盘后 ack 失败、unknown Run 只查询原 operation 不重发等节点本轮均重跑通过。
这些证明存储/恢复边界；sender 端口观察不等于真实网络或真实 CLI 消费。

未闭合 S13：完整生产 sender/WS 连接和客户端通知、TCP/lifespan、真实 provider/CLI 消费、
Settings/retention 全管理面与前端 pin 双来源表达、跨进程长层、OS cleanup/P23、真实迁移、
三平台远端 CI、部署和人工验收。本轮未重复全仓、29 retired-positive 或其它 quota 旧红，
没有扩大到完整 handoff/Settings/S13 验收。

源码仅 coordinator 授权 pin 段与新测试；其余新增仅本报告和独立 evidence。final_service、
final_api、ledger、非 pin cleanup、conftest/support、React、P23 文件和三个 legacy host 未修改。
已通过 diff --check、正常 Git hook 提交；最终候选 SHA 由交付消息给出，报告自身不嵌入循环提交 SHA。
