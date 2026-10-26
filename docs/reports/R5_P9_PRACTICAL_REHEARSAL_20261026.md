# R5-P9 practical 隔离副本迁移预演

结论：**BLOCKED，不能作为最终合入的数据迁移绿灯。** 已完成只读采集、逐类盘点、真实副本的 WP08 拒绝证据及合成隔离恢复验证；没有完成 practical canonical 导入，更不是生产迁移完成。

源码基线：`bdb0a50bf735d682c11329220d8d40ce8fa2d009`，分支 `r5/p9-practical-rehearsal-20261025`。产品代码与 React 未修改。新增的采集脚本与三个测试只服务本次证据。

## 安全与采集范围

唯一真实数据输入为 `D:/project/Pan/data`。对该目录只进行目录枚举、metadata 与 `rb` 文件读取；不修改权限、不写标记、不调用源 Store/Session 构造器、不运行副本中的代码。未连接或操作 8767/8768，未启动、停止或重启真实 Pan/独立服务，未 push 或推进 main/practical。

私有证据根是本工作树的 [`.validation/p9-private-20261026`](../../.validation/p9-private-20261026)。该路径受已有 `.validation/` ignore 规则保护，包含真实文本、账号材料、命令或路径，**未提交仓库**。仓库仅提交本报告、工具、合成测试和[脱敏汇总](../../evidence/r5-p9-20261026/summary.json)。

实际采集结果：179,194 个普通文件，5,119,558,200 bytes；逐文件源读取流 SHA-256 与落盘副本 SHA-256 均一致。8,089 个链接/junction 只记录位置和目标，不跟随、不创建指回真实环境的链接；`actions/job-runtime/service.lock` 只读打开被拒，保留 PermissionError，没有绕过锁或停止其持有者。8 个文件在读取期间发生 size/mtime 变化，分布在 session-drafts、qq_plugins、mcp_remote、logs、background_jobs。初次采集进程中断后续传，旧清单和未记录完整的片段另行保留，未把中断记录覆盖成成功。

因此 `completePhysicalCopy=false`、`applicationConsistentSnapshot=false`。这是跨时间采集的普通文件副本及链接清单，不能声称整个活动 profile 的一致性快照。`changedDuringRead=0` 的单文件也不能证明整个目录同时刻一致。未在副本完成后重新读取源目录来宣称源前后不变。

## 数量、格式与覆盖

下表的“保留”指原始副本及内容 hash/记录 digest 可读，并不指已经进入 canonical store。

| 类别 | 副本观察 | 迁移/保留结论 |
|---|---:|---|
| Sessions | 137 个 JSON；65,033 条 history JSONL | 解析成功；原 bytes、ID 与关键字段 digest 保留，未进行 Session runtime 导入 |
| background-process Jobs | 102 | WP08：51 simple、50 complex、1 unknown；simple 只是白名单形状候选 |
| main-lifecycle Jobs | 5 | 全部 complex 保留，不生成 canonical ID |
| session-message Jobs | 4 | 全部 complex 保留，不生成 canonical ID |
| Action Jobs / Triggers / Templates / Scheduler tasks | 当前 profile 已知路径中可识别实例为 0；unknown 另列 | 不把不可识别记录排除后宣称全量为零；六 MIG 类型完整映射由合成回归验证 |
| Actions | 5 个定义，7 个 revision 项、2 个 generation 项 | 原 registry 保留；不把旧定义猜补成 WP01 固定 Action pin |
| Event Sources | 已知 registry 无 source/creator 定义；Event DB 687 条 events | 原 Event DB 保留；没有为事件推断新 Creator 或授权 |
| Runs | background_jobs JSONL 15 条；automation runs 0 | 6 completed、7 dispatched、1 running、1 error；原 dispatch_key 和内容 digest 保留，未重投 |
| pins / retention | Session pin 2 个；Job pin registry 为空；retention marker 1 份 | 原 ID 与文件保留，未执行 GC；不从 retention marker 猜补执行策略 |
| handoff | 12 个非空 handoff prompt；非空 handoff receipt 0 | 全部 Session 137 份 handoff/owner 等关键字段有 digest；没有重做 handoff |
| Session 通知 outbox | 9 项 qq_report_outbox | 原记录保留，不 ack、不重发；不能宣称已结算 |
| Agent Locker | 16 lockers、1,795 messages、1,795 origins、11 migrations | 原 SQLite 表与 ID/内容 digest 保留；sources/subscriptions/routes 等空表计数见私有 inventory |
| User Locker | 4 user_lockers、14 cards、3 user_cards、4 owner_receipts、4 tombstones | rejection_outbox 2、response_outbox 10，均 accepted；原键保留，未再次投递或改 receipt |
| independent 历史 | 本数据根未发现 jobs.sqlite3 / executions.sqlite3 | 不等价于共享/根外 independent 历史为零；没有扩展读取根外存储，完整性未证明 |
| workdirs / validation_env / cleanup archives | 所有可读普通文件均复制并 hash | 作为嵌套/共享/测试工件原始归档，不当成本 profile 的迁移对象；链接逐项列出 |

多数业务记录未声明 schemaVersion；session-pins 与 bookmarks 是 v1。按真实结构记录版本，不把 `process.json` 的进程信息当作源代码版本证明。WP08 inventory 使用 `v1-known-20261025` 白名单，目标契约是 WP01 schemaVersion=2。实际各类版本计数、schema fingerprint、来源位置、file SHA、record digest 和关键字段 digest 在私有清单中。

三个 profile SQLite 的 scratch 只读检查均为 `integrity_check=ok`。检查只在私有 DB 副本上执行；不证明在线采集的 DB 与其它文件业务一致，不进行迁移/ack/repair。

## 实际 WP08 dry-run / apply

WP08 扫描 111 条实例：`111 = 51 simple + 59 complex + 1 unknown`，scanErrors=0。该等式只适用于 WP08 六类型已知路径，不能套用为整个 profile 的 `pendingClassification=0`。工具 `_inputs` 仅覆盖 115 个文件、257,472 bytes，另外 179,079 个普通文件没有被其作为输入扫描；大量是 workdirs，也包括 Sessions、Locker、通知和元数据。

阻断原因计数可重叠：unsupported_complex_instance=50、uncertain_effect=21、active_resource=14、pending_notice=1、schedule_semantic_loss=4、counter_coupling=4、unknown_field=3、unknown_schema=1、unverified_authority=1、corrupt_record=1。逐原 ID、位置、hash 与原因见 `blockers.json`；同一 unknown 记录可同时含多个原因。JSON 可解析与迁移契约判为 corrupt/unknown 不矛盾。

没有为实际数据伪造 Action pin、principal、旧 owner 已停止或 retention/notification policy。显式空 context 标为 unapproved，保留 context-bound planDigest。真实副本三次调用结果：

| 调用 | 实际结果 | canonical target |
|---|---|---|
| dry-run | `unknown/corrupt source or scan error: refusing whole apply` | 不存在 |
| apply + 错 planDigest | `plan_stale: --expected-digest required/mismatched` | 不存在 |
| 显式 apply + 本次 planDigest + mark-read-only | `unknown/corrupt source or scan error: refusing whole apply` | 不存在 |

没有删除未知记录后只迁 51 条，没有调用合成 context 工厂给真实数据补授权，也没有为绕过 source/fence root 约束重写 bundle.root。**完整 canonical bundle 未产生，实际 import/active_ids/restart/rollback 后续步骤被前置门禁阻断。** 实际 `canonicalRecordsImported=0`，原 ID→canonical ID 映射均未建立；未将“空 store”算成迁移成功。

`preservation-bundle-manifest.json` 是覆盖全部已采集普通文件的非执行归档索引，显式 `canonicalBundleProduced=false`；它不是 WP08 可导入 bundle。不能拿归档 hash 代替迁移图、授权、历史查询或原键结算的验收。

## 前后对照与失败恢复

对照结果见 `before-after-comparison.json`（仅重新读取副本，未重新打开真实源）。179,194 个已记录普通文件全部 SHA 相等、missing=0、mismatch=0。初次复核还发现拒读锁文件留下的 0-byte 片段，已移到 `failed-copy-partials/`，原结果保留为 `initial-before-after-comparison.json`；最终文件集合 extra=0。移动的只有不属于有效副本的失败片段，已校验的普通文件没有改写。链接、拒读文件和在线变化均保留为未完成的全量采集边界；续传前后的源整体漂移没有测定。

完整源文件清单 SHA-256：`944011720bb2e2907f296eab436d47fda88eb07cf43123a2e985cffad0eac769`。逐记录/关键字段索引 SHA-256：`d3931fb762ab802f79ac152cf3e0c9260159d8ae93bddab4863f6971182cfd3a`。24 个私有工件的 SHA 及测试命令均列入脱敏 summary。

actual activeLegacy / oldOwner / unknownOwnership 清零没有证明，也未操作真实 owner。归档中的旧记录没有被注册成可执行记录，未知、活动、partial、outbox 和原键均原状保留；没有生产者、sender 或 legacy pass 被启动。未将原 unknown 改成成功或生成新的 Run 重做。

合成隔离测试与 practical 数据证据分层：

| 验证 | 结果 | 证据范围 |
|---|---:|---|
| WP08 apply/inventory/WP01 contracts + R4 owner + inbox migration | 208 passed | 六 MIG、hash/planDigest/ref、显式 active_ids、稳定 IDs、源不变、rollback/reapply、未知不重投；partial-write/fsync/replace 与 publish 前/后真实子进程退出 |
| runtime registry | 30 passed | pins/retention、handoff 冻结历史、原通知键结算、uncertain 保留、迟到/重复 receipt、失败不 GC |
| P9 新增恢复控制 | 3 passed | 当前 Agent grant 真实拒绝；写 Trigger 后抛错整图回滚；导入事务中子进程 exit 77 后原 bundle 恢复、epoch 前进、ID 不重建 |

合计 **241 passed，0 failed，0 errors，0 skipped**。每轮有一条 FastAPI/Starlette 关于 httpx 的 deprecation warning。新增测试失败后还核对 definitions/authorities/Creator links 全空、runtime task 未启动、legacy writer 仍被 fence；失败后省略原 bundle 不可绕过。它们只使用 pytest 临时合成 profile，无实际账号或外部效果。

命令、退出码、JUnit 计数和 artifact SHA 见脱敏 summary；原 JUnit/log 在私有目录。没有全量后端、React/build、真实 HTTP/浏览器/MCP、三平台/远端 CI、真实 provider、部署或人工验收结果。

## 证据入口与后续门禁

私有目录保留：`copy-manifest.jsonl`、`copy-summary.json`、`copy-blockers.json`、中断清单/片段、`source-copy/`、`wp08-inventory.json`、`record-index.jsonl`、`references.json`、`object-identities.json`、`sqlite-inventory.json`、`status-counts.json`、`supplementary-counts.json`、`blockers.json`、`unapproved-context.json`、`apply-attempts.json`、`preservation-bundle-manifest.json`、`before-after-comparison.json`、测试 logs/JUnit。

重新达到数据门禁至少需要：可验证的一致性/受控源快照；锁文件/链接/共享根的明确采集范围；unknown 与所有复杂/不确定/在途对象的处置；真实受控授权、固定 Action/provider 引用与策略上下文；Sessions/历史/Locker/outbox/independent 的正式导入或原键只读索引契约；随后在另一个隔离 canonical profile 完成实际导入、原 ID 查询、历史/pins/handoff/通知结算、restart、重复 apply 及恢复验证。上述工作不包含在本次已通过的 241 条合成回归结论中，也不授权停写、服务操作或生产迁移。

## P9B remediation（2026-10-26）

本次继续修复没有把 BLOCKED 改成 green。快照脚本新增了明确 allowlist：`sessions`、`background_jobs`、`automation`、`scheduler`、`actions`、`attachments`、Locker/Inbox、历史、pins、drafts、terminals 等真实数据目录；worktrees、依赖、缓存、构建产物和其它无关根目录被排除，`followlinks=false`。allowlist 副本统计为 **1,971 files / 723,823,286 bytes / 22 explicit exclusions / 3 SQLite snapshots / 0 capture blockers**。SQLite 先在私有 DB/WAL 副本上完成 backup 和 integrity check，不通过源库连接。跨 store 一致性仍未声称。

先前观察到的 8 个变化文件逐一二次读取，全部得到稳定的双 hash（`oldChangesStable=8`, `oldChangesBlocked=0`）。这些文件中只有 `session-drafts` 和 `background_jobs/logs` 属于迁移范围；其余是日志或插件状态，明确保留为范围外变化证据。旧 full-copy 中锁文件的拒读不再作为业务文件失败：`actions/job-runtime/service.lock` 是 `action_runtime.py` 的进程互斥锁，按显式排除保留其理由；不停止持有者、不把它算成迁移记录。

WP08 新增显式 `--preserve-incompatible` partial apply。它要求 context 中的 `partialPolicy={version:1,mode:preserve-incompatible}`，并把 `selectedMigrationIds` 纳入 planDigest；只允许 selected simple 候选进入 disabled/draft canonical graph，complex、unknown、未选 simple 和未分类文件带原路径/hash/原因进入 `preserved`，`targetIds=[]`、`historicalOnly=true`、`executable=false`。默认 apply 仍 fail closed，未知或 scan error 不会被静默跳过。对实际 allowlist 副本的部分 apply 尝试结果为：

```text
dry-run: blocked, unknown/corrupt source or scan error
apply with stale planDigest: blocked, plan_stale
full explicit apply: blocked, unknown/corrupt source or scan error
partial-preserve-apply: blocked, unverified_authority: missing reviewed record <migrationId>
```

最后一项是预期的真实阻断：没有获得可验证的实际 owner/authorization/context，不能把 51 个 simple 形状候选伪造成可导入。它不再因为 59 complex + 1 unknown 而拒绝整个 partial plan，而是在第一项真实授权缺失处停止；目标文件不存在，未产生任何 canonical ID。合成 partial apply 已证明 6 个 selected simple 进入 bundle、2 个 complex/unknown 原 bytes 进入 historical preserve，重复 apply 不重复，失败 replace 保留旧完整包，unknown 不执行/不重投；allowlist 和变化文件重试也有测试。

P9B 新增通过 13 项，当前组合回归为 **254 passed、1 skipped、0 failed、0 errors，退出码 0**（57.71s，1 条既有 deprecation warning）；原先的 241 个测试全部保持通过。唯一 skip 是 Windows 当前用户没有创建 symlink 的权限，并在测试中显式记录；allowlist/cache/锁排除已拆成独立通过项，不因该 skip 而漏测。拒读文件按次数重试后必须阻断，SQLite rollback journal 存在时也必须阻断，不可静默丢弃。新增内容没有 build、HTTP、8767/8768、服务或真实 runtime activation。这个 SHA 可作为“快照范围/partial policy 的实现证据”，不能作为 R6 迁移完成依据；R6 仍被实际 authorization/fence、simple 集合正式导入、complex/unknown legacy 保留、independent 根外历史和全量 activeLegacy 证明阻断。

本轮私有证据目录是 `.validation/p9b-private-20261026-v2/`；原 P9 及首次 SQLite handle 失败证据保持原样。对该目录的 `source-copy` 重新逐文件复核：**1,971 files / 723,823,286 bytes，missing=0、extra=0、mismatch=0**，不重开真实源。SQLite 比较对象是已校验 backup，不把 backup 的物理文件 SHA 冒充源 DB 的物理 SHA；manifest 另列 rawSourceSha256。快照 manifest SHA-256 为 `b94efb1c455f400e41f795e8b0d24d19ce62562f1cbec109daabd03ad2edcba5`。

当前 bounded 副本观察到 Sessions=139、history=65,304、Event DB events=707、Actions=5、Agent Locker=16/1,795 messages、Runs ledger=15。与早先 P9 的 137/65,033/687 不同，代表读取批次间实际 profile 已变化，不是 canonical 前后差异。各类格式、关键字段 digest、文件/记录 SHA 在 `audit-summary.json`、`record-index.jsonl` 和 `snapshot-manifest.json` 中。本轮跨 store 一致性、源库整体停写点仍未证明。

`remaining-legacy-by-id.json` 明确列出全部 **111 个仍保留的原身份：51 simple 缺 reviewed authorization context、59 complex、1 unknown**，每项附路径、原 SHA、分类、原因和空 targetIds；complex/unknown 的细分原因另见 `blockers.json`。清单 SHA-256 为 `deafc00de451a103b1a92d0594ea807446a4b0ebf8e0954421ad8d51ecb62a23`。这里的“仍保留”指副本中记录存在；真实进程活动没有探测，不能把 14 条 active_resource 拒绝原因解释为当前实际运行 14 条。实际导入集合为空，不能以空集对照宣称 activeLegacy 清零、原键结算或历史导入成功。

剩余门禁具体为：① 51 simple 的逐条 current authority、固定 Action/provider、old owner/fence 与 retention/notification 上下文尚缺，partial 实际停在首个 missing reviewed record，尚未逐项触达后续引用检查；② 59 complex + 1 unknown 的 legacy 身份尚未退役，原 bytes 保留没有替代迁移；③ Sessions/Locker/通知/根外 independent 的正式可读与结算路径尚未通过 actual import 验证；④ 只有 per-file/SQLite group 稳定，未建立应用整体一致性。实际 canonical imported=0、active_ids=[]、target 不存在。不能将工具合成数据上的 import/restart/rollback 测试用于填补这些实际证据。

公开脱敏入口为 [`p9b-summary.json`](../../evidence/r5-p9-20261026/p9b-summary.json)，固定实现提交 `d195dcc60171ba108b3b9a87333afbf4b8a76616`，包含 18 个私有工件 SHA、完整测试命令与 JUnit 计数。新快照/审计/副本复核的可复现命令见 [`README.md`](../../scripts/job_final_migration/README.md#p9b-partial-preserve)。本报告及所有 private 清单不构成生产迁移完成、R6 放行或源 owner 停写的证明。
