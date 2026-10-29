# JOB-RETIRE revision 6：当前保留能力对照

本批受测产品源码为 **`cde7549690d3611b45b0cad3b51c982323e4f213`**，`packages` Git tree 为 `92428e6db662e9663443d5510810e45ca223d6ca`。在原工作树先核实旧分支 `r6/retirement-closure-20261027` / HEAD `ae3476854ef010c1dd021112d5647ebe0c1dbaeb` clean、指定基线存在，再从指定基线创建 `r6/retirement-parity-acceptance-20261029-a`。没有合并旧分支、移动 main/practical 或 push。

本批仅新增专属测试、辅助脚本、新版证据和报告。产品、共享测试、conftest/support、CI、三个 scheduler host 均未修改。D1–D6 和六对象模型不变，没有新增 provider、账本、计算器或迁移策略。

## 当前结论与矩阵入口

- [M01–M15 当前对照](../../evidence/job-retirement-rev6-20261029/m01-m15.md)：每项给实际业务接线、当前执行节点和未验层，不沿用历史完成标签。
- [43 节点逐条对照](../../evidence/job-retirement-rev6-20261029/original-43-review.md)：原函数、matched assertion、精确 successor/support node 与剩余语义。
- [机器矩阵](../../evidence/job-retirement-rev6-20261029/acceptance-matrix.json)：包含原 43 个函数全文、原源 SHA/函数 hash、当前结果、原九项子集、命令/count/exit 和最终测试文件 hash。
- [输入指纹](../../evidence/job-retirement-rev6-20261029/reviewed-inputs.json)：指定 v2 计划、宪法设计、历史 M 矩阵、bounded recovery 验收、CP-X1 per-node leads 和原映射。

43 个节点当前分类为 **29 verified-retained、7 partial、5 product-gap、2 decision-required**。verified-retained 指该行列明的保留语义有当前证据，不等于旧 HTTP/MCP envelope 兼容、全能力验收、整体退役或真实数据迁移。原历史映射、原 43/P23 三红和旧报告均保持原 bytes/Git 内容。

所有执行节点按最后一次实际结果去重为 **388 passed / 10 failed / 0 error / 0 skip**，共 398 个不同 node ID。这是多个聚焦矩阵的集合，**不是一次 398 项全量测试**；该统计也不把每个单测等同真实效果证明。10 条最终红由 8 条产品语义红及 2 条环境红组成。首轮测试编写错误单独保留，不能混成产品缺陷，也未删除断言、skip 或失败日志。

## 原九项重新核对

| 原语义 | 当前结论与精确证据 | 剩余层 |
|---|---|---|
| Resume rescanning | `test_retirement_rev6_resume_native.py::test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles`：真实 select、真实 Worker Wake、两次候选变化、原 client/queue identity、pause 保留；不替换 provider/Worker ports | 临时 paused Sessions，不证明供应商 CLI 消费或真实运行中 Worker 集合普查 |
| Empty scan no-op | 同一 native 测试第三 Run 完成 matchedCount=0/results=[]、队列不变；现有 `test_scheduled_passive_template_matching_empty_fire_not_backlogged` 也重新执行通过，真正匹配 time Event 后完成零选择 | 不再使用 unmatched event type 冒充；真实 pending cohort 不在本批 |
| QQ create | 绑定真实 product_channel，经现有 `QQChannelProvider → server._send_scheduled_qq → /api/qq/send → plugin.api_send → OneBotChannel.send`，支持字段通过；**短 targetId=123、纯空白 text 在 POST 返回 201** | 原拒绝语义仍丢失；不是只修 numeric pattern 就关闭 |
| QQ edit | 真实产品编辑 CAS、选定 bot/target/text 和历史 frozen plan 不变通过；**相同两种非法值在 PUT 返回 200、revision2** | 两条严格红保留，合法编辑不代表完整字段校验通过 |
| QQ targetless run-now/plugin failure | 默认 channel、显式注册但未连接 bot、未知 bot 的真实插件错误，与直接原 route receipt 一致；manual cause 无伪时间 Event，一次 Attempt、重复 dispatch 无变化 | 仅真实离线插件负向效果。没有真实 QQ 账号/已连接 gateway/messageId 成功验收 |
| QQ backlog/retry | 原 `undeliveredFires` 的 resend 能力没有被通用 retry 测试代替。真实插件拒绝后的显式 retry 当前 409，原 Run/Attempt/effect 不变 | **仍为产品缺口**；必须由 MA 决定只对能证明零发送的原拒绝开放新 Attempt，unknown 仍只查原身份 |
| Existing Session before write | v1 message/broadcast 原三红后继当前通过；但新增 **v2 source-aware message/broadcast × POST/PUT 四红**，缺失固定目标仍被提交 | whitelist 只覆盖 v1；不能以 v1 通过关闭今天主用 v2 业务语义 |
| Automatic recovery revocation/budget | 真实默认 detached 进程失败一次后撤权，保留 marker/预算/deadline、一次 Attempt/实际调用；canonical 显式终结为 failed。现有 budget exhaustion、cold reopen/pulse 也以真实进程重新通过 | 原节点用 monitoring=true/stableReset=300；本次已接受实现只支持 failed/timeout、stableReset=0、有原清理证明的 bounded policy。原配置仅部分后继，复杂/unsupported 仍保留禁用 |
| Quota real HTTP unknown | `test_unknown_real_tcp_cold_reopen`：实际自有 TCP/lifespan、canonical admission、默认 unavailable adapter、uncertain Run/Attempt/effect、冷重开同身份，无旧 writer | 不查询真实账号，不证明所有阈值/切换策略或真实集合迁移 |

因此九项中 **4 项 bounded retained 语义已证、4 项 product-gap、1 项 recovery partial**。没有把旧九项一律保留 stale gap，也没有把当前局部通过一律升级完整关闭。

## 容易混淆的旧能力

### 默认／未知模板与 assign

原默认 assign 节点确实验证了缺省 action 的业务发送；本批新增 `test_retirement_rev6_schedule.py` 用实际产品 `product_assign_v2`、被动 Template、新建来源、Trigger、原 worker.assign 和持久 queue/task receipt，证明显式 scheduled assign 能力。**缺省 action/旧简单字段入口的适配仍缺**，通用 legacy-field 拒绝测试不能算它的严格后继。

原 unknown-fallback 节点前半要求 HTTP 拒绝未知 action，后半通过内部 `_update` 强塞未知 action，期望执行时 fallback assign。该内部安全兜底与 D6“unknown 原状保留且不可执行”需要 MA 明确决定；不能恢复 generic dispatch 或自动执行未知数据。保留合法 default assign 与执行损坏/未知记录是两个不同问题。

### scheduled shell 与 argv

新增 `test_scheduled_explicit_shell_argv_preserves_command_cwd_exit_and_no_early_creation` 通过 canonical 被动 time Template，实际 `cmd /d /s /c` argv 执行自有命令，验证 cwd 文件输出、exit7、同一 Run/Attempt/effect、原 owned cleanup；创建/实例化前后均未提前执行。说明 **shell 执行表达能力没有因 argv-only 完全消失**。

但旧 `action.shell.args.command/cwd` 的字段适配和默认简单 Template 尚不存在；手工写显式 shell argv 不是旧参数兼容。离线 migration mapping 把 shell 映射到 `process.shell` 所需 pin，并不能证明当前生产有该 publication。该 slice 停在适配/Template 决策，未改产品。

### Windows GUI、file IPC、早期创建和取消

本批新增两个 new/alloc 三代 GUI/console 测试，经 **canonical 公共进程 Action 和默认 detached host** 执行原 `desktop_tree_probe.py`，保留同一私有 desktop、无 input-desktop 窗口、GUI 创建 FILETIME、stdio 与释放断言。双向文件 IPC/exit7 也从旧测试迁到同一公共执行路径；没有复活旧 runner。

`windows-public` 为 9 pass：上述 3 个新节点，加 P23 公共 cancel/late receipt 三节点、真实失败/取消输出两节点、原 token 的真实 peer-file/named IPC 一节点。`creation-source` 中两个 native creation-edge 节点进一步验证真实 suspended 创建归属先于 resume、controller 在创建边缘死亡时无 child instruction/survivor。故 GUI、file IPC、cleanup 不是可以直接删去的“旧内部观察”；本批已补当前路径的真实后继。

旧 runner 层级、重复 flat kind/status、隐藏子 Job 位置等可作为内部观察变化，但 PID/birth、desktop 隔离、原身份清理与取消是用户能力／安全边界。原 named-venv earliest-import 观察没有在本批构造新环境重跑；不以现有 GUI/proof 或 Windows skip 代替它。Linux/macOS、本机以外桌面和真实 lifecycle 服务也未新增验收。

### 入口与 UI

源码确认 `packages/mcp/server.py::RETIRED_MCP_TOOLS` / `REMOVED_SCHEDULER_MCP_TOOLS` 移除了旧 create/retry 工具，保留只读/cancel 工具。原宪法文档要求旧 MCP 工具名/参数无感适配，因此这是 **待 MA 决定的兼容边界**，不能用 native HTTP 绿宣称旧 MCP 契约完整。

当前 `CanonicalJobsView` / structured TimeTemplateFields 有 canonical 管理及 instantiate 入口；本批验证其后端新建来源与共享来源引用，但没有进行浏览器验收或前端改动。旧 NewJobForm 源文件存在不证明其仍是可达生产入口。M14 的简单默认 assign/shell field wrapper 及最终用户流程保持未验，不能用 source inventory 关闭。

## D6 自有合成 rehearsal

`d6-source` 18 pass，调用 `scripts/job_final_migration` 真实 inventory/dry-run/apply/partial API/CLI，输入仅由 `_make_root(tmp_path)` 等专属合成 fixture 生成。覆盖六类型、源文件逐字节/hash 与 backups、默认 dry-run 不建 target、输出 disabled/draft、重复 apply 同 ID、complex/unknown/corrupt 原 bytes 保留、targetIds=[]/executable=false。`partial_runtime_import` 的拒绝 executor 只是支持性防线，不作为真实迁移效果证明。

原 pending 部分另以三断点、unknown 原效果不重放、advance 后缺原 commit 仍 uncertain 的现有精确节点重新执行。它们只证明 **临时合成原身份的显式 adoption**；D6 白名单和原 owner fact 不因此扩大。offline bundle 的 `plan.executable=true` 是离线发布完成，不是 runtime activation，报告仍为 runtimeActivated=false/runtimeFenceVerified=false。没有读取任何真实 51/60 集合、账号或 profile。

## 实际命令、结果与失败保留

每组完整 argv、cwd、exit、elapsed、精确 node/outcome 存在同目录 `<label>-command.json`，JUnit 为 `<label>.xml`，原输出 `<label>.log`。统一通过指定解释器调用 `scripts/run_tests_isolated.py`；每个所选组预计并实际均小于 3 分钟，没有运行全量长任务。

| label | pass / fail / error / skip | exit | 用途 |
|---|---:|---:|---|
| session-resume | 92 / 0 / 0 / 0 | 0 | 当前 Session/source/Resume、literal、name scope |
| qq-recovery | 7 / 1 / 0 / 0 | 1 | 保留首轮恢复测试误期望 retry_wait；源码明确撤权终结 failed，原预算/不重投断言继续保留 |
| qq-final | 11 / 0 / 0 / 0 | 0 | 真实 QQ 离线插件、编辑及已纠正的真实 recovery 节点 |
| qq-original-field-matrix | 12 / 4 / 0 / 0 | 1 | 原非法字段完整方向；短 ID/空白正文四红 |
| source-aware-validation | 0 / 4 / 0 / 0 | 1 | 当前 v2 固定 Session 写前存在性四红 |
| windows-public | 9 / 0 / 0 / 0 | 0 | canonical GUI/IPC/取消/输出/token |
| creation-source | 6 / 0 / 0 / 0 | 0 | native creation edge、handoff、安全 lifecycle/invalid schedule |
| d6-source | 18 / 0 / 0 / 0 | 0 | 合成离线迁移与原 pending 身份 |
| management-time | 81 / 2 / 0 / 0 | 1 | CRUD/pin/source；两条默认 host-zone Template 环境红 |
| retained-read | 162 / 0 / 0 / 0 | 0 | 历史 read/pin 不变、原参数化节点及独立 hold |
| independent-scheduler | 31 / 0 / 0 / 0 | 0 | 实际脱离 host 的进程、时间计算/DST/source migration |
| resume-native | 1 / 0 / 0 / 0 | 0 | 不替换 Worker/provider 的真实 query/Wake/原 receipt |
| schedule-identity-final | 3 / 0 / 0 / 0 | 0 | 无 provider double 的 scheduled assign/message、manual cursor、Event/rule ID、广播 |
| scheduled-shell | 1 / 0 / 0 / 0 | 0 | 实际显式 shell argv 的 time Event 执行 |

另外保留 `schedule-recovery-final`（1 pass/2 fail，误用发布前 revision）、`schedule-final`（2 pass）、`event-broadcast`（1 fail，误传 helper key）、`event-broadcast-final`（1 pass）、`schedule-native-final`（3 pass）的中间实验，均有真实 exit。没有用删除失败样本生成“全绿”。

两条 Template 环境失败由 [environment-probe.json](../../evidence/job-retirement-rev6-20261029/environment-probe.json) 独立确认：指定 venv `ModuleNotFoundError: tzlocal`。显式 UTC 的图创建/引用通过；本批未安装依赖，不改 provider gate 或断言绕过。

## 产品缺口和最小下一步（本批只报告，不编辑）

请 MA 分 slice 确认下列最小范围。所有原错误/失败证据均可直接复跑；不能用本候选批准新协议或全面重写。

| slice | 原调用／当前冲突 | 最小待协调符号 |
|---|---|---|
| QQ literal 校验 | 旧 `normalize_scheduled_qq_action` 用 `[1-9][0-9]{4,19}`；当前 product_channel schema 只用数字正则，正文只 minLength1 | `FinalService._install_product_actions` 的 channel schema 或固定产品 `_references` 业务校验，复用 `packages/jobs/actions.py::normalize_scheduled_qq_action`；publication 已不可变，不应覆盖已有 release，先由 MA 决定最小兼容新 release/写前复用方式 |
| v2 Session 固定目标 | 原 worker queue 仍正确；业务写前 helper 仅白名单 v1，source-aware v2 漏检 | `FinalService._check_literal_product_sessions` 的已核对产品映射，必要时覆盖 `product_session_message_v2` / `product_session_broadcast_v2`；不按任意参数名猜目标，不改 Worker |
| QQ known-failure retry | 原 `_redeliver_undelivered` targetless resend；现 `control_run/_retry_boundary` 要求 provider 没提供的 cleanup proof | `FinalService._retry_boundary`、`TypedExecutor._channel` 的原拒绝/no-send receipt 分类；只讨论 trusted zero-send 拒绝，未知继续原身份查询，不能创建第二 ledger/provider 协议 |
| default assign / shell field Template | 原 scheduler 默认 action/command 字段；现显式 `product_assign_v2` / process argv 已能执行 | `CanonicalJobsView`/TimeTemplateFields 的产品预组合选择与必要纯参数映射、Action publication（仅确需 shell adapter 时）；先确认是否复用现有显式 Action 即可，不恢复 generic dispatcher |
| unknown fallback / clear-target deferred record | 原未知内部数据仍 assign，clear target 可进入 undeliverable | 需 MA/用户对 D6 non-executable unknown、disabled draft/manual repair 的解释作决定；无获批产品修复范围 |
| old MCP transparent compatibility | 旧 MCP create/retry 被直接去注册，而宪法旧名契约要求适配到 canonical | `packages/mcp/server.py` 对这些旧工具的薄参数/结果适配与现有 canonical ports；不运行旧 owner，不新增执行生命周期 |
| original monitoring/stable-reset recovery | bounded accepted implementation 不支持 monitoring/stableReset=300 等旧配置 | 保持 unsupported/complex，若属于必须保留的能力，由 MA 先决定原业务函数/现有 recovery 端口最小接法；本批不扩大自动恢复策略 |

非阻断优化放在迁移后 backlog：错误展示一致性、诊断工件命名、索引/性能、额外 outbox/hold/provider 协议均不作为本批新门槛。本批没有实现这些优化。

## 资源与未验层

自有 TCP fixtures 均检查线程结束、socket 关闭及端口不可连接；真实 detached 进程 fixtures 检查原 supervisor exit、owned tree cleanup。Windows GUI desktop 与实际取消有已执行断言。测试原 helper 写到固定 evidence 路径的输出先完整归档到本版 `generated/`，再恢复 baseline Git 内容；[generated-artifacts.json](../../evidence/job-retirement-rev6-20261029/generated-artifacts.json) 记录前后 hash。没有丢弃红证据。

未验证真实数据激活/迁移、全部旧 owner/lease 排空、真实 51/60 集合、QQ 账号成功发送、模型消费、最终浏览器流程、旧 MCP 完整兼容、真实 main lifecycle restart/exit、全量测试历史红闭包、三平台本轮实机、远端 CI 或部署。CI40850416995 仅按指定 MA 验收文档作为历史当前 SHA 远端快层事实引用，本批没有重新查询或触发 CI。

此候选完成的是有界 parity 对照与可重复红/绿证据，仍需 MA 验收整合，不宣布整体迁移、退役或删除完成。

`raw-artifacts.zip` 另外保存本批证据及专属测试源文件的原始 bytes，避免 Git 文本换行归一化影响原 hash；包 SHA256 在 `raw-archive.json`。`artifact-hashes.json` 的 raw-file hash 对应包内 bytes，独立于 checkout 的换行设置。
