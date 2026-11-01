# Revision2：合成 runtime import readiness

后继 R3（2026-10-31）：已批准的 runtime target/text 矩阵使旧 message/broadcast 与
scheduled assign/send_session 不能继续无条件 literal 映射。当前 inventory/Mapper 将其
保全并报告 `runtime_binding_unavailable`，`runtimeBindingGap` 提供既有 `input` 路径、
owner/phase/receipt 接线要求；不是新用户决策，也不代表功能已迁移。旧 manager 保留，
Event backlog 不列为缺失。本页原 revision2 的 2+5 BG runtime 演练边界保持；涉及旧
literal message/scheduled 的历史测试断言原样保留，后继红项需在 R3 报告单列。

阶段候选 `ff255ccb3f1ddd036e129d111d5ac5377d251d4a` 已由 MA 收到，但迁移完整性未验收。
`runtime_binding_unavailable` 是临时实现阻塞；即使处于现有 `incompatible-preserved`
容器，也不等于 D6 永久不兼容、不授予删除或旧 manager 退休/接管权限。17 条新红不豁免。
等待产品所有者交付可消费契约及精确 SHA 后再恢复实际映射；不重复运行相同保护矩阵。
对照与恢复条件见 [runtime mapping handoff](RUNTIME_MAPPING_HANDOFF_20261031.md)。

基线 `b288613e8a1862ea82d16a27d27992b715d3e0ba` 包含已被 MA 独立验收的 revision1 离线候选及 r15 serializer。MA 报告组合 migration19+serializer22+原 quoted1 为 42pass。本批不把该历史计数当新运行数，不重新声称 r14 整体 accepted；原红和历史报告保留。

本轮明确授权替代 revision1 的本批 offline-only/P9D 禁用限制：允许全合成 snapshot/source-copy、新建自有 profile 的真实 FinalService.start import、owner fence、事务失败与进程崩溃恢复。实际用户升级、真实 profile、旧 51/60 集合、运行中源 owner、模型/provider/HTTP/端口、部署和删除均未获授权。既有 P9D 与 canonical importer 被复用，没有第二个导入器，也没有修改产品或信任检查。

## 重放

在候选仓库根执行，输出目录必须不存在；工厂创建自己的 2 simple + 5 incompatible/unknown 数据，绝不从真实目录取材料。

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 tests/support/job_migration_runtime_rehearsal.py run --output .validation/job-migration-runtime-r2/operator-01
```

输出目录含合成 snapshot、P9C draft 复核包、source-coverage.json、P9D 私有 profile/bundle/activation spec、failure-recovery.json、冷进程命令与 PID/epoch、summary/source-before/source-after/validation-bindings。仅合成统计、命令、hash、逐路径/记录处置提交 Git；完整源、context、授权 fixture 与运行库留在忽略目录中。

既有 P9D CLI 新增 `--expected-simple`、`--expected-preserved`；默认历史值保留兼容，本轮使用显式 2/5 或专属负控的 2/3。不要把历史样例命令的路径和 51/60 数字套入本轮。

单独盘点自有合成 root 的路径覆盖：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -X utf8 -m scripts.job_final_migration.source_coverage --root .validation/job-migration-runtime-r2/operator-01/snapshot/source-copy --out -
```

## 可核验链与信任范围

1. 全路径 guard 以不跟随链接的文件树扫描，逐文件记录 raw bytes SHA/大小、sourceType、handled/preserved/unrecognized、lite 逐记录分类和原因。SQLite/sidecar 只作为 opaque bytes，绝不 sqlite.connect。directory/link/special 与错误也显式列出；链接/扫描错误阻断 P9D。未知文件不产生 targetIds。文件总数=handled+preserved+unrecognized，不能把路径保全等同语义迁移。
2. P9D 先要求 source 发现的文件路径/hash 集合等于完整 stable manifest，漏列 cursor/outbox 或后来新增文件即拒绝，且不创建新 profile。复制前拒绝任何现有 runtime authority/目标运行库路径，包含嵌套 owner 文件和大小写别名；不重置、采用或复制源 owner。
3. 原 review_packet 和 prepare 复核源/packet/per-ID hash，真实新 profile 通过原 OwnerFence.claim(0)/release 建立本地 closed authority。prepare 的 user/local instruction 是明确标为 synthetic 的测试输入，其 reference/时间不冒充实际用户消息。signature=null，外部 receipt digest 由受控测试调用边界独立传给 apply 与 activation；JSON 内容本身不构成真实授权。
4. 新 profile 实际发布的 product_process Action/ref/digest、context、receipt、planDigest、bundleDigest/file hash、source manifest 与 canonical schema hash 均记录在 validation-bindings.json。运行入口沿原 spec 只接受已有 bundle hash/plan/receipt pins；schema/source hash 是本轮 QA 绑定，不伪称新增了运行时 schema-pin 协议。权限、对象 schema、引用和当前 Action release 由原产品检查，未 patch/bypass。
5. 离线包仍只有 disabled Jobs，active_ids=[]。随后原 FinalService.start(activation=spec) 真正写入新 profile 的 canonical SQLite：2 Job、2 history、2 source-stated delivered 原键 receipt、5 unrecognized、1 activation。原 ID/raw bytes/log/provenance 可查，没有 canonical Run/outbox/effect。delivered 仅采纳源声明，不是新投递或外部验收。
6. 既有故障注入先令 bundle publish 失败，再在 import history 写入中抛异常，最后让自有子进程在 Job 写入事务中 exit77。失败后 graph/history/receipt/unrecognized/activation 计数均为零，旧 writer 仍被 fence；原 context/spec/IDs 恢复成功。NoEffects.execute 任何调用都失败，断言 calls=[]；未 patch 可信授权、epoch 或 provider。
7. 原 P9D 用新 service 实例重开；本轮再用真正新 Python 进程冷重开相同 profile/spec。计数、原身份与零效果保持，epoch 由正常 claim 递增。并发 owner 的真实 OS lock、stale expected_epoch CAS、释放后旧 token writer 均拒绝；没有清锁文件、清 lease、伪造 nonce 或手改 epoch。
8. 每个错误 pin/release/schema/activeIds/rolled-back 包负控使用独立新 profile。失败时没有部分导入行，owner 正常进入 failed 并释放锁，legacy writer 仍拒绝。已写 activation_intent 保留原 spec 摘要；不为让 corrected spec 通过而清 intent 或换身份。错误 spec 后如何人工恢复该 profile 是独立运行协议边界，本轮不开发恢复旁路。

## rollback 与覆盖边界

事务失败/崩溃由 SQLite 原子 rollback 保证 imported 图与历史全无。本轮验证此事实和相同身份重试。offline bundle 的 rollback 只撤销离线 artifact；如果运行库已导入，canonical rows 不随它删除。rolled-back 包再次激活会拒绝；恢复原离线包使用原 root/context/授权/digest 和 IDs，不重新签授权、不重置运行 owner。此行为不等于“运行迁移可逆”。

source coverage 的 handled 表示 lite 读取/备份范围，具体记录仍可能 incompatible；preserved/unrecognized 表示只保全。合成独立 cursor、SQLite time source/outbox、Session pending queue、unknown blob 都必须出现于 unmappedPaths，不能算已迁移或已安全接续。它们只按 manifest 原 bytes 复制，未解释 SQLite schema、未采纳 cursor/queue、未确认 pending/unknown 效果。`wholeProfileMigrationComplete=false`、`allLegacyZeroProven=false` 始终保留。

清理只释放本轮 service task/owner OS handle、等待自有 child 终态。RuntimeLock 的 `.lock` 文件按原合同保留，**不删除锁文件**；profile owner closed 不代表源 PID 已退出。文件输出保留供审阅，临时 `.tmp` 应为零。不清理任何其他根、PID、端口或 real data。

最终验收源/结果入口见 `evidence/job-migration-runtime-r2-20261029/REPORT.md`。未验层包括 POSIX 实机/远端 CI、真实授权/grants/provider release、真实数据/全 cohort 语义盘点、源进程与 lease 排空/交接、未知效果与通知 ack、运行回滚、真实升级/部署。不能用新合成 target 的本地 fence 证明已有源 authority 已迁移。
