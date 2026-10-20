# Illegal / Wake 独立整合验收候选（main 20473b41）

日期：2026-10-21。整合工作树 `D:/project/pan-worktrees/legal-offline-sticky-wake-integration-20261021`，分支 `integration/legal-offline-sticky-wake-20261021`。产品整合 merge：`c04447c63bd3c55844c73c98ab293d9010d2ee0e`；本报告及后续测试/证据提交在该 merge 之后。功能验证通过；严格只读环境存在已披露的初始工具缓存写入例外，不能宣称全程没有越过该约束。候选供 MA 最终验收后快进，本任务不移动 main/practical。

## 整合范围与冲突

- 基线 main：`20473b41aeeb8dec9aadf4dac1bfe7368ca3f737`。原 review 树保持 `e160782e636d828427e9262e952a1f25c09117b9`。
- `20473b41..e160782e` 恰为 `64742747`、`bad516b9`、`2f1c2b3b`、`e160782e` 四笔。采用 merge 保留全部祖先，包括旧关机实验文档/原始证据和 bad516 的历史，不 cherry-pick 丢祖先、不带入其他分支 WIP。merge 的两个 parent 分别是 20473b41 和 e160782e。[祖先清单](../../audit/sticky-wake-integration-20261021/merge-ancestry.json)。
- 两处内容冲突：`packages/web/src/services/api.ts` 的 retention 类型/scanWorkdirs 与 Wake 尾部导出；`docs/skills/pan/references/http-api.md` 的 retention 与 Wake 段落。均保留双方并独立闭合函数/章节。worker、InputRow 测试、AppSettingsModal 测试和 Pan skill 自动合并后另行审查。
- [语义保持脚本](../../audit/sticky-wake-integration-20261021/verify_semantics.py) 的 26 个断言通过：[结果](../../audit/sticky-wake-integration-20261021/semantic-preservation.json)。worker 的 native stdout/history 同 main，staged writer/Force/Wake/ready 恢复函数同 incoming；session/retention/workspace、MCP Force 工具定义、InputRow、server picker、handoff UI、DataSettingsPanel 与 main 相同。保留 main 的“当前任务补充默认 Force，无需紧急判断”及 originalPrompt handoff 原段落，并补充 Force 不覆盖 Illegal 保护。

## 设计优先级与原始授权

[修订设计](../design/PAN_LEGAL_OFFLINE_ILLEGAL_LOCK_AGENT_TASK_REVISED_20261018.md) 在标题后、所有历史内容前新增**最高优先级授权覆盖块**：Illegal 持续期间为所有真正新入队消息加 Illegal 锁，含插件、报告、Job、定时与用户/agent 消息。显式将旧 §2 第4条“后续新入队消息不会自动锁定”和实施要求“不添加持续锁定未来消息的门禁”标为失效；未确认为 Illegal 的 uncertain 执行责任仍不能误加 Illegal 锁。历史条文保留审计用途，不能再作为反向实施要求。

授权原话和持久位置：

1. “新机制下，illegal态会illegal锁后来的所有消息，直到取消illegal态”——原会话 JSONL 第 **4849** 行，`2026-10-20T08:35:31.375Z`。
2. “Oneshot现在似乎没有什么独特价值，我想移除这个模式，你帮我确认一下它是否有独有功能，没有的话先移除所有入口，但是先不删代码，也skip所有相关测试”及“5.对，包含所有消息，包括plugin来源的”——第 **4891** 行，`2026-10-20T08:48:12.940Z`，message `msg_01a1587f-308c-7aa3-97ec-f9abbd91e02e`、turn `01a1587d-87ea-78d2-b243-8b667ee489c3`。

原路径：`C:/Users/14709/.codex/sessions/2026/10/18/rollout-2026-10-18T03-19-37-01a14b4e-2d9b-7d41-9fe9-7fb7b80b0735.jsonl`。[只含这两条 user 记录的摘录](../../audit/sticky-wake-integration-20261021/authorization.json) 保存时间、位置、ID 和文本 SHA256；不复制无关会话内容。

## 49 skip 逐项审查与修正

原交付的 49 项**不能全部原样认定为合适的整项 skip**。[逐项表](../../audit/sticky-wake-integration-20261021/skip-audit.md)、[机器清单](../../audit/sticky-wake-integration-20261021/skip-audit.json)、[复现脚本](../../audit/sticky-wake-integration-20261021/audit_skips.py) 逐项记录 e160 文件/行号、body hash、退役路径证据、保留/拆分/恢复结论。

恢复 7 个现行用例：CBC native 首次启动/resume、CBC/Kimi/OpenCode execution modes、Claude metadata、Claude stream/Steer/compact 编码、有效 prompt（含 Codex prompt 文件及 resume）、长 prompt 的历史模式回退、usage enrichment 写盘失败回滚。前五项混有 legacy 断言，拆出专属旧 Oneshot 片段为 4 个 narrow skip（CBC 声明重复断言合并）；长 prompt 回退仍是当前边界，usage rollback 仅因 fixture ID 含 oneshot 被误停，现直接恢复。InputRow 原 skip body 也收窄到 Oneshot，保留现行 stream/native 与新 shortcut 测试；增加 quick-new 的历史 Oneshot 回退且保留其他默认字段/Workspace 用例。

当前**注解库存：43 Python + 3 前端 = 46 skip**，均为退役的私有 consumer/argv/Oneshot 注册与生命周期或旧 UI 选择恢复语义。Job one-shot 调度、普通 usage、stream/native 和停用边界测试不在停用范围。此库存与下表“本次实际运行的 skip 数”不同，不可把未运行的库存加进本次执行总数。

## 当前候选的新验证（不是旧 940）

先运行 prepare_validation_env 创建本树 junction，随后 `-Check` 再确认环境；无依赖安装/升级。所有**最终有效验证** exit 0。[完整命令与 cwd/argv](../../audit/sticky-wake-integration-20261021/commands.json)，[结构化计数](../../audit/sticky-wake-integration-20261021/summary.json)。各命令预估和实测均低于 3 分钟，最长有效 pytest 组约 38 秒；没有运行本地全仓全量，也没有用受保护服务承载长命令。

| 当前整合验证组 | 通过 | skip | 失败 | 证据 |
| --- | ---: | ---: | ---: | --- |
| 核心 Illegal/operation/admission/Force、CBC native、Claude、模式和 prompt | 286 | 26 | 0 | [core.xml](../../audit/sticky-wake-integration-20261021/core.xml) |
| retention、handoff originalPrompt/Job retarget、shortcut lifecycle、native completion 与新入口事务检查 | 164 | 1 | 0 | [compat.xml](../../audit/sticky-wake-integration-20261021/compat.xml) |
| 七入口故障注入及 gate 的最终加强复验（含重复） | 10 | 0 | 0 | [atomic-final.xml](../../audit/sticky-wake-integration-20261021/atomic-final.xml) |
| queue API、手动报告、迁移、启动恢复、定时恢复、附件协议 | 192 | 0 | 0 | [queue-api.xml](../../audit/sticky-wake-integration-20261021/queue-api.xml) |
| 真实独立 HTTP/WS/Chromium、同时强杀、全锁与 ready 成败 | 10 | 0 | 0 | [http-browser.xml](../../audit/sticky-wake-integration-20261021/http-browser.xml) |
| 真实本地 Codex CLI＋合成 provider（native/Pan turns 同步、启动失败、模式切换及持久 ACK） | 4 | 0 | 0 | [native-real.xml](../../audit/sticky-wake-integration-20261021/native-real.xml) |
| 非空 Wake 的 ready 延迟/失败，再固定 Job/浏览器证据（其中两项重复） | 4 | 0 | 0 | [wake-ready-proof.xml](../../audit/sticky-wake-integration-20261021/wake-ready-proof.xml) |
| 前端 14 文件：shortcuts、InputRow、server picker、handoff、Wake/设置、队列、retention | 257 | 3 | 0 | [最终只读复验 JSON](../../audit/sticky-wake-integration-20261021/frontend-readonly.json) |

按 Python testcase identity 去重，前端重复复验不累加：**620 Python/HTTP 通过 + 257 前端通过 = 877 通过；实际执行 29 skip（26 Python + 3 前端），0 失败/错误**。不要与原 940 或库存 46 混用。

正常 `pnpm build` 的 TypeScript/Vite/预压缩检查通过，72 资产；后续缓存隔离复验也构建同一受版本控制的配置，明确断言 `isProduction=true`、`mode=production`，再次校验 72 压缩资产。[build](../../audit/sticky-wake-integration-20261021/build.txt)、[最终只读前端/构建日志](../../audit/sticky-wake-integration-20261021/readonly-frontend.txt)。改动前端 ESLint exit 0、0 error；4 条 SettingsPopover 既有 hook warning 保留。源码 `git diff --check` 通过；原 e160 导入的历史 raw 日志含既有空白警告，保留原样，不把日志规范化当功能修复。

### 新增风险验证

- `test_sticky_wake_integration.py` 的 send、assign、notice、plugin/channel、自动 report、手动 report、Wake 七条路径，在真实 metadata replace 注入失败时，Session 原始 JSON bytes 不变、队列/ledger/事故成员/手动报告 receipt 不泄漏、事件广播不发布半成品；恢复 writer 后同一操作成功，成员与行同存。
- 同一 Wake ID 四个并发请求只建一个条目；重复 ID 不再次解 Manual。Wake 前置的新行只解除自己的 Illegal 锁，旧 Manual、报告策略锁、Pause、lease、Steer pending/force hold 均保留。lease 维持原条目范围：独立 Wake 可优先交接，但编辑中的原条目仍不能交接，不清租约。alive/unknown owner 拒第二 writer，不因 Wake 退休责任。
- 重新跑 Force 在 queue/spawn 串行边界的并发 Illegal 复检；默认 Force 工具选择规则与保护并存。ready 前构造 Idle 不清旧锁，显式 Start 和已入队非空 Wake 的真实启动失败也不清；真正 adapter proof 才恢复。
- 浏览器验证 Behavior 保存、独立 Wake 与普通群发同时存在、未刷新 WS 新 Illegal 图标及队首位置；main 的新增 Shortcut/Picker/Handoff/Retention UI 单测和后端兼容组都重新执行。

### 新关机实验原始证据

本树、本候选实际再跑：Job `job_d7d3d819b06fdde2d4171ae7`，运行目标为 Running Session，持续 86400 秒；按捕获 PID/create-time 的自有进程树在同一 barrier 放行中强杀测试 Pan/provider/Job，再用同一测试数据重启。

失败提醒 `q_db87e9bdf65540d09262fdebfab42164` 的重启后状态为 **queued + illegal 锁**；Wake 前 CLI 未收到。显式 Wake 后 CLI 先收到“继续”，再收到带该 Job ID 的失败通知，原 queue ID ledger 为 `sent_to_cli`。[新前后快照](../../audit/sticky-wake-integration-20261021/job-crash-current.json)、[新 CLI 接收](../../audit/sticky-wake-integration-20261021/job-cli-current.jsonl)。Job 的 notificationState=delivered 仍只表示通知成功入队，不表示模型已处理。

[非空 Wake 成功终态快照](../../audit/sticky-wake-integration-20261021/ready-success-final.json)、[失败终态快照](../../audit/sticky-wake-integration-20261021/ready-failure-final.json) 是测试 teardown 后状态；ready 测试按旧事故 ID 判断恢复，不把 teardown 可能产生的新事故当旧事故结果。[Behavior 截图](../../audit/sticky-wake-integration-20261021/wake-behavior.png)、[唤醒界面](../../audit/sticky-wake-integration-20261021/wake-completed.png)。

## 环境过程例外与清理

1. **严格只读的初始偏离**：直接用默认 Vitest CLI 写入了 canonical `node_modules/.vite/vitest/results.json`，默认 Vite loader 也使用 `.vite-temp`；没有安装、升级或编辑依赖包，但这不满足“junction 全程只读”。未擅自回写共享缓存以伪造还原。随后用 [只读验证脚本](../../audit/sticky-wake-integration-20261021/readonly-frontend.mjs) 复验同 14 文件和生产构建，缓存落在本树 owned-cache，受控执行前后共享结果 cache 的 SHA256/mtime、loader 临时目录 mtime 均相同：[proof](../../audit/sticky-wake-integration-20261021/readonly-cache-proof.json)。后续重验不能追溯消除最初偏离，交 MA 知悉。
2. 验证辅助过程有非产品失败：最初临时证据已被并行 pytest 清理，导致新增 ready 文件尚未写出时的一次调用 0 tests/collection error（[日志](../../audit/sticky-wake-integration-20261021/wake-ready.txt)）；改用本树独占 basetemp 后四项通过并立即归档。只读工具第一次读取已关闭 Vitest 的字段报错，第二次 runner loader 不能供应原配置的 __dirname；均不是用例失败。之后在 owned dist 转译同一配置、保留原 __dirname、关闭再次 config 文件加载并断言 production 后完整流程通过，旧辅助错误日志保留。
3. 本任务未移动 main/practical、push/release、重启现有 Pan 或测试 8767/8768。收尾 refs 均观察为 20473b41。14 个测试 Pan 端口都已释放，[listener 检查](../../audit/sticky-wake-integration-20261021/listeners.json) 为空；新 Job 与记录的 CLI owner PID/create-time 均已终止，[清理记录](../../audit/sticky-wake-integration-20261021/cleanup.json)。
4. 自动审批审查拒绝了递归删除本树 owned-runtime 目录的命令，工具返回 **blocked by policy**。不绕过拒绝，不再尝试替代删除；数据作为 ignored 临时证据保留，测试进程已清理，Git clean 与源码无关。owned-cache、dist 和依赖 junction 同样为 ignored 验证产物。

未验证：远端 CI、全仓全量、真实收费模型、真实 QQ/微信网络、practical 真实数据迁移/服务运行。Pan 管理范围之外外部客户端的不可观测执行保持原设计边界。本轮没有发现需要用户重新决定产品语义的缺口；流程例外、当前未测范围如上，供 MA 最终验收决定。
