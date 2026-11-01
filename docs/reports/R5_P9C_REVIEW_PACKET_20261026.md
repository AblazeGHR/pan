# R5 P9C 人类审阅包（2026-10-26）

任务 `T-JOB-P9C-REVIEW-PACKET-20261025-1` 已交付审阅材料，状态为 **REVIEW_PACKET_READY_UNAPPROVED**；数据迁移门禁继续 **BLOCKED**。没有签署授权，没有导入、转换、删除或激活任何真实记录。

本轮沿用工作树分支 `r5/p9-practical-rehearsal-20261025`，起点 `7da2aa4a1405109d9e212aa4a1b5a7f5f042c8d5`。已核对该起点是已接受集成 `d55d014c75de0f526e3ab078c863ed9b793a84f7` 的祖先；没有修改集成分支。实现提交为 `40e18f7eb0b1fda3275c4ccb76c7e62e6f6e2943`。

## 材料与数量

输入仅为既有 `.validation/p9b-private-20261026-v2/` 隔离证据。没有重新打开 `D:/project/Pan/data`，没有调用可在读取时创建目录的旧 get/list，也没有调用实际 migrate/Mapper/runtime store。拟映射只计算既有确定性 ID 公式与字段映射；真实映射/导入均为 0。

含原 ID、路径和引用的审阅材料保存在忽略的私有目录 [review-packet](../../.validation/p9c-private-20261026/review-packet/)，不提交到 Git：

| 文件 | 内容 |
|---|---|
| [simple-review.csv](../../.validation/p9c-private-20261026/review-packet/simple-review.csv) | 51 条 simple：ID、kind、MIG 类型、来源路径、record/file SHA、bundle/packet 身份、拟 canonical 映射、引用和风险/未知项 |
| [preserved-review.csv](../../.validation/p9c-private-20261026/review-packet/preserved-review.csv) | 59 complex + 1 unknown：逐 ID 原因、历史保留条件、旧读路径依赖、R6 阻断与建议 |
| [REVIEW.md](../../.validation/p9c-private-20261026/review-packet/REVIEW.md) | 人类摘要、全部 111 项索引、三组最少决策 |
| [records.json](../../.validation/p9c-private-20261026/review-packet/records.json) | 完整结构化审阅内容；所有实际 targetIds 均为空 |
| [reviewed-context.draft.json](../../.validation/p9c-private-20261026/review-packet/reviewed-context.draft.json) | 明确 draft_unapproved 的授权草稿 wrapper 与 nested context |
| [decisions.json](../../.validation/p9c-private-20261026/review-packet/decisions.json) | D1–D3 的默认值、所需证据及影响 |
| [packet-manifest.json](../../.validation/p9c-private-20261026/review-packet/packet-manifest.json) | 6 个内容文件 SHA、包自摘要、草稿 planDigest、计数及非执行标记 |

全部 51 条 simple 都是 background-process 形状候选，拟使用固定 `process.exec` Action，Job 初始 disabled/manual-only；原 exit/log/resource receipts 仅作为历史证据，不生成 canonical Run。Action pin、当前 grants、profile、owner/fence、retention/pins 和通知策略仍缺，不因候选形状简单就自动补齐。

60 条非 simple 的建议分为 **39 条“保留（待人类确认）”、21 条“需业务决策”**，没有自动转换或删除。4 条消息计划同时涉及 schedule/counter 耦合与未知效果，位于“需业务决策”组，须先按原键结算再讨论转换；本轮没有可直接转换的批准集合。59 complex 的构成为 50 background-process、5 main-lifecycle、4 session-message，另有 1 unknown background-process。

拒绝原因可重叠：unsupported_complex_instance=50、uncertain_effect=21、active_resource=14、schedule_semantic_loss=4、counter_coupling=4、unknown_field=3、unknown_schema=1、unverified_authority=1、corrupt_record=1、pending_notice=1。`active_resource=14` 是快照中的分类原因数，不是对当前进程存活的实测计数。

## SHA 与草稿身份

发现并在 P9C 处理了旧证据字段的格式陷阱：inventory 的逐文件 `recordDigest` 实际可能是规范化 JSON 文本。审阅包对可解析 record 重新规范化计算 SHA-256；无法解析时明确采用 raw bytes SHA 并标注 basis。原文件 bytes 的 SHA 独立列为 `sourceFileSha256`，没有改写 P9B inventory 或将 JSON 文本冒充 hash。

包身份绑定 source manifest、inventory 和非执行归档 envelope；canonicalBundleProduced=false，canonicalBundleDigest=null。`proposedOperationId` 和拟 canonical IDs 仅供审阅，没有进行分配或发布。

- packetId：`review1_1025fc1dae54c64b3a86b801`
- packetDigest：`42647c17b1643fd86366d554aaad408682895afcce7354d1e8552d1d9af63222`
- draft planDigest：`3c3d9be079bde09baeb9a0082cae1c3b9191348d00bdc11dc4220251dbae6b37`
- source manifest SHA：`b94efb1c455f400e41f795e8b0d24d19ce62562f1cbec109daabd03ad2edcba5`

草稿 `planDigest` 绑定精确 nested context，其中包括逐 ID file/record hash、拟选择的 51 个 migrationId、partial policy 和 review metadata；摘要置于 wrapper，避免自引用。profileId、principal、owner、grants、oldOwnerStopped/epoch、fence、importedAt 和策略都留空；signature=null、approvedSelection=[]、active_ids=[]、executable=false。

P9C 格式在进入源读取、映射或 target 创建之前一律拒绝 apply/dry-run。无签名、占位签名、自报 signer、编辑 approved 状态均不提供可信授权；去除标记后草稿的空 authority/fence 也仍被拒绝。当前没有可信签署者注册和签名验证协议，所以不提供“填写一个签名即可放行”的入口。这个规则针对 P9C review 格式，既有 v1 操作员 attestation 没有被升级成密码学签名系统。

## 最少需要用户确认的决策

| 决策 | 需要确认什么 | 影响 |
|---|---|---|
| D1：simple 选择及签署责任 | 哪些 simple 原 ID/hash 纳入后续迁移；由哪位有当前权限的数据所有者/复核人签署 reviewed context，并提供 Action/provider/policy 证据 | 绑定精确选择集合；任何选择、原 SHA 或上下文变更都需要重新审阅。确认责任人不会直接变成执行授权；可信验证方式仍需实现 |
| D2：永久历史保留或转换 | 按私有逐 ID 清单确认哪些 complex 可永久历史保留，哪些必须另行设计 canonical 定义；39 条保留候选尚未被用户接受 | 永久保留仍须独立的非执行原 ID/历史/log/receipt 读路径与 retention/pins 验收；转换必须另开实现/验收任务，不能自动复制运行态 |
| D3：未知效果的结算责任 | 为 21 条需业务决策项指定结算责任人，按原资源、effect、dispatch/notice 键和已消费计数决定处置 | 不重投 unknown，不清零计数，不伪造完成/通知收据；在结算与旧执行退役证据完成前继续阻断 R6 |

上述三组可按 ID 分组回答；默认均未批准，保持原 bytes。回答决策不替代当前权限、owner/fence、应用一致快照或正式导入验证。

## 旧读路径与 R6

每条 complex/unknown 都列出历史保留的边界和静态读依赖，包括原 Job JSON、logPath、`background_jobs/runs.jsonl`，以及适用时的 Session 原身份/history/notice 引用。现有读取链包括 `packages/core/background_jobs.py:get/list_jobs/list_run_records` 和 `packages/jobs/final_runtime/coordinator.py:read_legacy_history`。这些只是静态源码依赖分析；没有在真实源上调用，也没有跟随绝对 logPath 到根外打开文件。旧 get/list 的 `_root` 会创建目录，不能当成纯只读采集 API。

历史保留在本轮仅表示原 bytes 可归档。能否永久替代旧宿主读路径、安全展示、逐原 ID 查询、未知/partial 表示、pins/retention/handoff/通知原键结算，都仍缺独立读层及实际导入验收。**111 个 legacy 身份全部仍保留，原 ID→canonical 实际映射为 0，全量 activeLegacy=0 未证明。**

本包引用的是 P9B 既有采集批次，不代表此刻 practical 的记录数、权限或进程活动。跨 store 一致性与根外 independent 历史未验证。P9C 不缩减这些真实环境边界，也不能作为 R6 迁移完成或生产放行依据。

## 验证与复现

两个不同私有输出目录重复生成，7 个文件 bytes 全部相同，packetDigest 与 draft planDigest 相同；两次生成分别复核全部 1,971 个快照文件和 WP08 输入。P9B 原 18 个证据工件 SHA 完全不变，canonical target 仍不存在。复核记录见 [verification.json](../../.validation/p9c-private-20261026/verification.json)。

组合测试 **267 passed、1 skipped、0 failed、0 errors，退出码 0，60.61s**；此前 254 项全部通过，新增 P9C 13 项通过。覆盖 draft/未签名/占位或自报签署拒绝、wrapper/nested context 拒绝、去标记不获得权限、hash 与选择绑定、六类型拟 ID 对照、包 bytes 稳定、源和证据不变、篡改输入拒绝且不创建输出。唯一 skip 仍为 Windows 用户无创建 symlink 权限；allowlist/cache/锁排除为独立通过项。另有一条既有 FastAPI/Starlette httpx deprecation warning。

生成命令：

```powershell
python -m scripts.job_final_migration.review_packet --evidence .validation/p9b-private-20261026-v2 --out .validation/p9c-private-new/review-packet --expected-manifest-sha256 b94efb1c455f400e41f795e8b0d24d19ce62562f1cbec109daabd03ad2edcba5
```

`--out` 必须是新的目录。完整测试命令、JUnit 计数及 packet/test/verification SHA 在公开脱敏 [p9c-summary.json](../../evidence/r5-p9-20261026/p9c-summary.json)；实现和格式合同见 [README](../../scripts/job_final_migration/README.md#p9c-人类审阅包非执行材料)。初始 fixture 缺少父目录与 allowRealProfile 元数据不匹配的失败测试日志保留在私有 validation 中，未降低断言。

本轮仅交付源码、隔离单测和只读快照上的审阅材料。没有 build、React 修改、真实 HTTP/浏览器、8767/8768、服务操作、真实 import/activation、远端 CI、部署、生产迁移或 push。
