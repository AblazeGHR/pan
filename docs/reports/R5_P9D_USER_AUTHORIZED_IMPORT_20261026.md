# R5 P9D：用户授权的隔离副本迁移（2026-10-26）

**P9D 的 51 条 simple 已在新建隔离 canonical profile 实际 apply/import，通过映射、历史/日志读取、原键历史收据登记、重复/重启幂等和失败恢复验证。** 59 complex + 1 unknown 全部保留原 bytes/身份并标记 `unrecognized`；没有转换、重投、结算未知效果或删除。这是受限集合的隔离预演通过，不是生产迁移完成，也未放行完整 R6。

用户最新决定已替代 P9C 的待决策状态：51 条全部按 user 授权和归属；此前 21 项及 4 条消息计划均按 unrecognized 保留，不再等待逐项业务决策。P9C 草稿、P9B BLOCKED 历史证据保持不变；本轮创建新的授权审计及上下文，没有给旧草稿伪造签名。

工作树分支为 `r5/p9-practical-rehearsal-20261025`，起点 `88861ebc1c2c89fed1ff1d3417a14883051ff8e5`。功能提交 `29fc8a6ea9abe8b7fb043f7d41c5839f3fcfa344`；含 retention 元数据测试的验证提交 `b72a18ff7bc0bf224ed9335b5ebaa9d9f85fb4be`。

## 授权审计与隔离范围

[user-authorization.json](../../.validation/p9d-private-20261026/rehearsal/user-authorization.json) 保存本次用户指令文本及 hash、`conversation/user/P9D-policy-decision-current-turn` 引用、记录时间 `2026-10-26T11:22:10.7473131Z`、51 个 ID/file SHA/record SHA、选择集合、packetDigest、隔离 profile 及 context payload 摘要。时间是记录收据的时间，没有假定原消息时间。

authorizationSource=user，canonical principal/owner 均为项目已有的 `user/local`。assistant 只记录指令，不作为批准人；signature=null。既有 source/creatorSessionId/sourceStruct 等原审计字段仍保存于原 bytes/历史，不被冒充为新的权限依据。

本版本没有密码学身份验证契约。可信来源是本次明确的用户指令及受信任的会话/操作员交接；receiptDigest 只证明完整性。apply 与 runtime activation 均要求 context/bundle 外单独固定的授权摘要，不能由嵌入的 approved 字段自行授予。来源=agent、占位 reference/signature、缺少外部 pin、修改选择/hash/owner/context 均拒绝。能够控制可信调用边界和文件的恶意操作员不在该非密码学审计机制的防御范围内。

私有证据目录为 [rehearsal](../../.validation/p9d-private-20261026/rehearsal/)。仅从既有 P9B `source-copy` 读取并复制 **1,971 文件 / 723,823,286 bytes** 到新建 `profile/`；没有重新打开 `D:/project/Pan/data`，也没有改变 P9B/P9C 原证据。没有采用或重置复制的真实 owner authority。本副本建立了自己的 OwnerFence；真实旧进程、原服务、端口及外部 independent 根没有被操作或探测。

`process.exec` 固定引用取自新 profile 实际发布的 `product_process` Action/revision/definition digest，不使用合成 Action context 工厂为实际数据造 pin。全部 Job 初始 disabled/manual-only，active_ids=[]，executor 拒绝任何效果。导入策略显式为禁止重叠、无自动恢复、无新通知、保留/pinned 历史；这是新导入策略，不声称旧记录原本具有这些政策。原 pins/通知状态另存原样。

## 实际数量与前后对照

| 项目 | 结果 |
|---|---:|
| WP08 原身份 | 111 = 51 simple + 59 complex + 1 unknown |
| canonical Job 导入 | 51，createdBy/owner 全部 user，state 全部 disabled |
| 原 ID→canonical 映射 | 51，可逐 ID 核对原 SHA 与目标 ID |
| 独立历史行 / 原日志可读 | 51 / 51 |
| 原 terminal key 历史收据 | 51，不再次投递 |
| unrecognized 行 | 60，targetIds=[]、executable=false、unknownSettlementAttempted=false |
| bundle preserved 项 | 61 = 60 个原身份 + 1 份非实例 retention 元数据 |
| runtime activation receipt | 1 |
| 新 Run / outbox / effect | 0 / 0 / 0 |
| 未知效果结算 / 自动删除 | 0 / 0 |
| 重复 apply 写入 | 0 |

51 条源状态为 34 completed、17 failed；exit/log/resource 仍为历史证据，没有伪造新引擎执行过的 Run。原文件及关键字段随 raw bytes 全部保留。源副本与 P9D profile 的已采集 **1,971 个原文件 SHA 全部一致**，原 ID 历史/未识别对象 **111 份 raw bytes** 与原 file SHA 一致；P9B 原 18 个工件、P9C 原 7 个包文件 SHA 均不变。

原 pins/retention 标记没有丢弃：额外 `.completed-retention.json` 在源文件及 bundle.sourceBackups 中原样保留，并作为无 migrationId/无目标的 metadata preserved 项列出。它不伪造第 61 个 legacy Job，也不进入 60 条手动删除清单。

51 条通知原状态均为 delivered。本轮按**同一个原 terminalEventId** 创建 durable `migration_receipt`，状态 historical-delivered，basis 明确为“源快照记录 delivered”。没有向 sender 发请求，没有新 outbox，没有再次 ack 或投递；externalDeliveryVerified=false。因此“可结算范围”是接纳已有原键历史收据，不是重新核验外部收件、清空 Sessions/Locker outbox 或确认 unknown 效果。

历史/原键查询使用 `packages/jobs/migration_history.py` 的非执行读函数与 canonical SQLite，日志只读 hash 固定的隔离 profile 文件，不回调旧 loader。独立测试禁止读取源 Job JSON 时仍能查询原 ID 历史和原键收据；日志路径逃逸被拒绝。unrecognized 以独立 kind 保存原 bytes/原因，不被 canonical Job scanner、admission 或 executor 当成配置。

`activeLegacyMigratedSet=0` 仅针对该隔离 profile 中的 51 个已映射身份：没有启动旧 producer，参与者旧写在实际 owner authority 下被拒绝，canonical 定义全 disabled；owner 关闭后仍拒绝旧写。其余 **60 个未迁移 legacy 身份仍在**，逐项见 [unrecognized-index.json](../../.validation/p9d-private-20261026/rehearsal/unrecognized-index.json)。已迁移 51 条的原始 JSON 也保留为历史，没有自动删除。**没有宣称真实环境或全量 activeLegacy=0。**

## 失败、恢复与幂等

实际副本经历了三个失败点，详见 [failure-recovery.json](../../.validation/p9d-private-20261026/rehearsal/failure-recovery.json)：

1. bundle 原子发布 replace 抛错：target 不存在、无遗留 lock，不留下半个包。
2. 已写完整 51 Job 后，在第 17 份历史行写入时抛错：Job/history/receipt/unrecognized/activation 均回滚为 **0**，owner=failed，旧写继续封锁。
3. 独立子进程在事务内写到第 17 条 Job 后 `os._exit(77)`：同样五类行数均为 **0**，持久 owner epoch=3/activating，旧写仍被拒绝。

随后从相同 bundle、授权摘要和源 bytes 恢复：全部 51/51/51/60 行一次提交，owner epoch=4。再次重启 epoch=5，所有数量及原键不变，无新 Run/outbox/effect。重复离线 apply 返回 idempotentReplay=true、targetWrites=0；bundle 原文件 SHA 不变。最终组件已关闭，owner 状态 closed/epoch=5，旧写封锁仍有效。没有回滚到旧执行能力，也没有修改任何源记录。

## 手动删除清单

[manual-delete.csv](../../.validation/p9d-private-20261026/rehearsal/manual-deletion/manual-delete.csv) / [JSON](../../.validation/p9d-private-20261026/rehearsal/manual-deletion/manual-delete.json) 共 60 条，列出逐 ID、类型、原因、真实原始位置、file/record SHA、共享历史/日志/Session/Locker/pins/retention/handoff/notice 依赖和单条人工命令。

[MANUAL_DELETE.md](../../.validation/p9d-private-20261026/rehearsal/manual-deletion/MANUAL_DELETE.md) 给出人工步骤；[manual-delete-commands.txt](../../.validation/p9d-private-20261026/rehearsal/manual-deletion/manual-delete-commands.txt) 是惰性文本，包含对应 ID 的 Get-Item、Get-FileHash、Remove-Item -LiteralPath/-Confirm 命令。命令中的真实源路径只是文本，生成器没有打开或执行它们。没有 ps1 自动执行脚本、删除函数、批量循环、通配符或递归删除。

人工未来删除前需核对精确路径/非链接父目录/当前 SHA、资源与未知效果归属、共享引用和保留约束，并确保原 ID 历史/原键备份仍可读；不能因 unrecognized 标记就把未知效果当成功，也不能级联删除共享日志、Session 或 ledger。本任务未执行任何删除，不证明真实删除后的行为。

## R6 可执行与不可执行

可执行清单及条件已形成 [p9d-r6-assessment.json](../../evidence/r5-p9-20261026/p9d-r6-assessment.json)：

| 类别 | 结论 |
|---|---|
| 可进入 R6 整合 | 整合 user audit、51 条 disabled 定义、原 ID/原键历史读层、60 条 unrecognized 与人工删除材料；保持组合验收 |
| 零调用验证后可删的旧执行分支 | 旧 message/scheduler/recovery producer、旧创建/重试写入口、已退休的 system_actions.invoke 及旧 inline 执行接线；保留明确拒绝边界和后继负控测试 |
| 必须保留或先提供已验收替代 | 60 条原 bytes/身份、get/list 历史读 facade、raw logs、runs.jsonl 原键读取、Session/Locker/pins/retention/handoff/通知关联、其它 Event/Run 历史 |
| 本证据不允许整删 | core/background_jobs.py、coordinator.py、automation/scheduler store 全模块及数据；这些仍混有读路径、canonical runtime 或共享后端 |
| 本证据不允许提前移除 | 未识别/活资源的 inspection/cancel/owner 查询、independent/execution host/typed receipts/锁、runtime fence 与未完成 rollout 的启动恢复/import guard |

新独立读函数已经可在本批次工作，但尚未接入并验收 unrecognized 的 HTTP/React 读取替代；原 GET/read facade 不能据此直接删除。外部 independent 历史、真实 owner/cohort/活资源、应用整体一致快照、真实部署/重启与 provider 行为也未验证。它们是剩余技术门禁；**不再将 21 条旧业务决策项或 4 条消息计划列为等待用户逐项决定的阻断。**

R6 可以开展有条件的旧执行叶子清理及读写拆分，不能据此生产全量退场或删除所有 legacy 身份。未在本任务修改 R6 的旧宿主删除范围。

## 测试、SHA 与复现

最终组合 **282 passed、1 skipped、0 failed、0 errors，退出码 0，68.63s**；原有 267 项全部通过，新增 P9D 15 项通过。覆盖 user audit/外部摘要 pin、agent/占位/自批拒绝、51 导入、60 不执行、unknown 不重投、原键/日志读取、收据漂移拒绝、手动删除材料、retention metadata 保留、真实子进程退出和整图回滚。唯一 skip 仍是 Windows symlink 创建权限不足；allowlist/cache/锁排除为独立通过项。有一条既有 FastAPI/Starlette httpx deprecation warning。

- user authorization receiptDigest：`a4731b0ea97622e036c6d8dabdb30a1093cb9d1b5e32abb795648cf6c8336a70`
- P9C packetDigest：`42647c17b1643fd86366d554aaad408682895afcce7354d1e8552d1d9af63222`
- 实际 planDigest：`b936b274e0455be5aa1dd55bd9f06e5263d229a208758030b76fef4b648332bd`
- bundleDigest：`4ca1a1d9eaefcd6d1a122d6525ec90f65b1ce116c7bd461e037b587e9b34ba75`
- bundle 文件 SHA-256：`922251424435ba06fb79ad38be1171630cc5a092c4be617860ce47444347cc9b`

[p9d-summary.json](../../evidence/r5-p9-20261026/p9d-summary.json) 保存 19 个私有工件、8 个测试/验证入口 SHA、完整命令和 JUnit 计数；[verification.json](../../.validation/p9d-private-20261026/rehearsal/verification.json) 为独立 SQLite/bytes/SHA 复核结果。验证脚本 [verify.py](../../.validation/p9d-private-20261026/verify.py) 只读取隔离根和既有 P9B/P9C 证据，命令为 `python .validation/p9d-private-20261026/verify.py`。

预演复现命令见 [README](../../scripts/job_final_migration/README.md#p9d-user-authorization-与隔离导入)。必须使用新 private 目录、已留存的原用户指令和本次记录时间；新的 profile 路径/时间会生成新的上下文与审计摘要，应重新固定，不能拿旧 digest 静默套用。初次测试因错误复用已释放 owner 实例而失败的日志，以及额外 retention 项导致初次总数断言失败的核对过程均未用降低语义断言掩盖；最终已按真实协议/分类复核。

本轮完成的是源码、隔离单测、真实数据副本上的受限实际 import、失败恢复与只读核对。未 build、未改 React、未连接 8767/8768、未启停真实 Pan 服务、未重新读取或写入真实源、未 push；未运行真实 HTTP/浏览器、远端/三平台 CI、真实 provider、生产迁移、部署或人工删除。main/practical 引用未改变。
