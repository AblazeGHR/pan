# R5/R6 可执行拆除计划（2026-10-26）

## 1. 结论、范围与证据口径

工作树 `D:/project/pan-worktrees/job-r5r6-plan-20261025`，分支 `r5r6/retirement-plan-20261025`，核验基线 `43d0977f0c783f0bd69da5c4611d6bdfe8236a67`；开始时工作区干净。本次只新增本文与配套 manifest，不修改产品代码、测试或配置，不运行服务、测试、构建、真实数据迁移，不 push 或推进其它分支。文中的操作均为后续实施要求。

**当前不能直接删旧宿主。** “R1–R3 + WP08 apply”是输入基线描述，不代替阶段出口验收：旧 HTTP/MCP/React 写入口仍在，canonical admission 仍有旧 registry backend，WP08 仅离线发布 disabled 定义。R5 可在**经验证的 R4**之后启动，R6 还要经过 R5、迁移恢复演练与用户验收。不得把本计划或离线 apply 成功当成 owner 已清零。

必读版本核对：本基线 [终局设计](JOB_FINAL_MODEL_20261025.md:535) §12 为工作包/测试追踪，R5/R6 实际位于 §6（353/354）；文件止于 §12，无用户所称“§13 退场表”。本计划按现有 §1 的 E/S 表（全文）、§6 阶段表、§12 WP09/10 和 [RETIREMENT_INVENTORY 全文](RETIREMENT_INVENTORY_20261025.md)全部 E01–E15、S01–S14、U01–U16 建映射；不杜撰缺失章节。若 MA 另有更新版 §13，集成前必须复核差异。

[file/symbol/route/test manifest](R5_R6_EXECUTION_MANIFEST_20261026.json)提供基线上的逐文件、类/方法、HTTP handler、MCP 工具、测试 node 与静态 import 候选。以下 `file:line` 均为本基线重新扫描的证据；不是沿用 inventory 的旧行号。manifest 的 59 条 HTTP decorator 是静态 handler 清单，不是生产路由数：`final_api.install` 会重挂 facade 并保留部分原地址。20 条 MCP 符号含 19 个旧工具和 `job_config_drafts`，不能报告为 20 个待删旧工具。测试 node 数是 AST 定义数，不含参数化展开，未作 pytest collection。

动作仅用四种：`delete` 删除运行时代码/导出；`read-only` 仅查询非执行历史或受限离线证据；`migrate` 先转移职责及断言再删旧实现；`keep` 保留新模型或通用能力。同文件可有多种动作，表内职责优先于 manifest 的文件级默认动作。`delete` 从不表示删除真实数据。

## 2. 开工门禁与可执行顺序

| 门禁 | 责任交付、必须检查的值 | 不满足时的动作 |
|---|---|---|
| G0 基线/范围 | 每包记录 branch/HEAD/status、祖先与 diff；禁止并发修改共享路径 | 重基线后重扫本文证据；不覆写其它包 |
| G1 入口止写 | HTTP 所有旧创建/更新/重试/run-now/pause/resume/template/owner-switch 入口与 MCP/CLI/UI 调用方逐项切断；动态 route/tools/catalog 与 authenticated UI 证明 | 作为 R3/R4 补缺；不能按“R3 已完成”跳过 |
| G2 owner/资源 | 所有 profile/cohort 的 oldOwner、activeLegacy、legacyExecutableRecord、unknownOwnership 均为 0；原 epoch/lease fenced；进程 PID+birth/整树 proof、在途 lifecycle、通知 outbox/pins 有结算证据 | 保留相关 read/cancel/settlement；不擅自 cancel、kill 或重发 unknown |
| G3 迁移/历史 | sourceTotal=simpleMigrated+complexPreserved+pendingClassification，最终 pendingClassification=0；逐 ID 原路径/hash、目标 IDs、授权、历史索引/查询及告知记录；简单项真实导入、验证、恢复，而非只生成包 | 不删除 registry、time source/bridge、历史读取；复杂项不生成目标、不执行、不 GC |
| G4 通用能力接管 | canonical query/cancel/edit/retry/pause/pin/retention/handoff、跨停机 receipt、平台 cleanup、事件历史与通知原键均验收；同锁/CAS/取消 fence 契约 | 先迁能力，再删宿主；UI 按能力处理，不能假成功 |
| G5 R5 出口 | 旧 URL/工具/UI/调用链为零，旧宿主文件移除；已迁原 ID 可查询 provenance，复杂项只读告知；无新旧双 owner | 不进入 R6/发布结项 |
| G6 R6 出口 | 用户验收；离线迁移/rollback 包独立封存且可验证；生产分发无 loader/owner-switch/旧 flag/迁移 CLI；三平台与部署 readback 分层签收 | 保留离线恢复能力；不自动清理备份/复杂原数据 |

WP08 的 `scripts/job_final_migration/apply.py:183/184/201/203/212` 明确 `runtimeActivated=false`、`runtimeFenceVerified=false`、`runtimeEnforced=false`。因此 G2/G3 需要后续受控 runtime import/fence/activation 工作及证据，不能把 `--mark-read-only` 意图当权限已锁或 lease 已 fence。当前离线包没有自动进入生产 store（`scripts/job_final_migration/README.md:3`）。

执行顺序（每步形成候选与独立验收）：

1. **R4 前补缺**：先冻结旧新效果入口，迁 canonical clients；保持旧 ID 查询/取消/原通知 settlement。建立全 profile owner/cohort 清单；实现受控离线包导入和实际 fence，验证授权、字段/时间语义、稳定 ID 与重复执行边界。
2. **R4 交接**：迁 registry backend/原子锁、time source pending/ack、历史索引、独立 Attempt 协议、lifecycle receipt 与安全 primitives；排空或确认原 owner。G1–G4 都通过才签 R4。不能用默认空 profile 的零计数代表全部实例。
3. **R4 后可立即实施的 R5-A**：删已无调用的旧工具、旧 routes/remount、legacy UI/transport/types；删 cron shim、scheduler engine 桩与旧 wiring。条件是调用方迁移和动态 route 快照已先合并，安全/历史能力保留。没有独立的“只看空函数即可全删”例外。
4. **R5-B 宿主收尾**：删 bridge/compat/unified/old kind factories、旧 registry/notice/pass/runner；按本文文件组逐项反向依赖归零。R5 成功前必须确认发布包和正常启动不依赖这些文件。
5. **R6**：用户验收后移出迁移工具、旧 task importer、owner-switch、双轨 flags 和 legacy fixture loader；保留受限离线恢复包、审计样本与原复杂数据。回滚演练用归档包及同一 ID，不能回滚成自动开启旧 owner。

**需要额外 owner 证据的项**：S01/02/03/04/07/08/10/11/12/13/14、E10/E15；存在 unknown/活进程/未 ack 就阻断相关宿主删除。E01/E06/E07–09 的 read/cancel 仅在 G2/G3/G4 成立后删。复杂原数据物理非零允许，但可执行注册/lease 必须为零。

## 3. inventory 全量决策：E01–E15

| ID | 文件/类/端点证据（当前基线） | 动作与执行出口 |
|---|---|---|
| E01 | `packages/web/server.py:10941/10973/10991/11001/11083/11128` 的 start/list/get/cancel/update/retry | `delete` 六 handlers 与 decorators；当前仍写。先停 start/update/retry；list/get/cancel 过渡保留至 G2–G4。不能把 cancel 当可无条件删除的旧写 |
| E02 | `packages/web/server.py:11025` session-message start，11069/11076 仍 start_message/start_broadcast | `delete` POST；调用者先迁显式 Job+Creator+Trigger |
| E03 | `packages/web/server.py:10909/10923` `_legacy_mcp_authority/_legacy_action_job`；`Runtime.create_legacy` 213 | `delete` 伪身份/builder；通用可信主体/授权先接管，历史审计主体 `read-only` 不升级为 user |
| E04 | `packages/jobs/api.py:131/495/711/773/814/827/848/1121/1159/1174`；`final_api.py:384/403/411/418` | `migrate` 管理能力后整文件 `delete`，删除 `/api/legacy/jobs` 与原路径余量；canonical `/api/jobs` keep，不得按前缀批删 |
| E05 | `packages/jobs/api.py:747/753/765`；`packages/jobs/templates.py:39/55/60/106` | `migrate` 内置/自定义时间模板为完整被动 Template，保存 provenance；旧 API/store `delete`，原数据 read-only |
| E06 | `packages/scheduler/api.py:298/309/362/374/386/447/470/485/500/525/539/556` | `delete` 全 router；374 owner-switch 先禁止新切换，未完成切换按原 operation 恢复。list/get/runs 先迁审计；其余写先撤 |
| E07 | `packages/mcp/server.py:2213/2389/2409/2432/2447` | `delete` agent_background_start/get/list/cancel/retry 五工具；先迁客户端；读/取消出口与 HTTP 同门禁 |
| E08 | `packages/mcp/server.py:2258/2304/2327/2349/2377` | `delete` agent_message_job_create/get/list/update/cancel 五工具；禁止永久同名 wrapper |
| E09 | `packages/mcp/server.py:2516/2593/2622/2644/2691/2705/2722/2736/2753` | `delete` scheduler_create/list/get/update/delete/pause/resume/run_now/runs 九工具；清常量、helper 和 catalog 描述 |
| E10 | `packages/web/server.py:4019/4025/4353/4359` restart/exit status/POST；4089/4413 create_service_job | `migrate` 受保护固定系统 Action+canonical supervisor/receipt 后 `delete` 四路由和旧 Job writer；保留跨服务停止的原 operation 查询 |
| E11 | `packages/web/src/views/JobsView.tsx:65/470/856/1046/1419`；`components/jobs/LegacyJobDetails.tsx:7`；`JobDetailDrawer.tsx:351` | `delete` legacy tab/form/details/branch；`migrate` JobsView 其余管理能力到 canonical UI。不得整删 JobsView 造成 pin/retention/批量查询丢失 |
| E12 | `packages/web/src/services/api.ts:2444` 起旧 Scheduler transports；`stores/scheduleStore.ts:9` | `migrate` 来源管理到 Creator 客户端；删除 10 个旧 transport、旧类型/store（反向引用为零才整文件删） |
| E13 | `packages/web/src/services/api.ts:2627/2637/2644/2652/2663/2744/2753/2799/2805/2812` | `migrate` list/detail/history/control/pin/delete；旧 create/patch/run/cancel/retry/fetchLegacy transport `delete`，canonicalJobs service keep |
| E14 | `packages/core/cli.py:98/101/106`；`console_exit.py:67/72`；`host_console.py:146`；`scripts/restart.bat:4/stop.bat:8`；`api.ts:2052` 附近 lifecycle transport | `migrate` CLI lifecycle/console/scripts/Settings 到固定系统操作；删旧 URL 和 JSON 轮询，保留进程身份/端口/readiness/no-auto-retry 校验。不能删整个 CLI/host console |
| E15 | `packages/jobs/independent_service.py:72/123/240/251/261/266/275/359`；`pan_bridge.py:41/61` | `migrate` canonical executor；旧 JobStore.create/retry 与 `/jobs*`/`/events*` `delete`；既有 read/cancel/ack/recover 先保留至 owner/outbox 清零；不删整个独立 service |

以上按 handler 符号定位；精确 method/path/decorator 行见 manifest HTTP 清单。`server.py` 行号可能因分支演进漂移，实施者先用 manifest symbol 定位重扫。

## 4. inventory 全量决策：S01–S14 与额外文件

| ID | 文件/类/方法证据 | 动作、前置及最终文件命运 |
|---|---|---|
| S01 | `packages/core/background_jobs.py:66/92/218/258/268/281/338/800/1139/1176/1208/1263/3139` | `migrate` registry get/write/locks 给 canonical storage 后整文件 `delete`；旧 start/update/retry先撤。`action_runtime.py:9`、`final_runtime/coordinator.py:8/38`、`automation/chain_engine.py:65` 仍依赖，不能先删 store |
| S02 | 同文件 `1275/2219/2231/2615/2740/2755/2810/2936` 两时间 pass、dispatcher、redelivery | `delete` 旧执行链；`_entry_next_fire:2425` 是被新 time_events 使用的 pure calculator，先 `migrate`，不能随 dispatcher 同删 |
| S03 | 同文件 `1467/1477/2106/2139/2480/2517/2531` hooks/历史/hidden child 对账 | hooks/pass `delete`；runs JSONL 历史 `read-only`，索引/原 dispatch key `migrate`；子进程结算后再删唯一 reconciler |
| S04 | `packages/scheduler/store.py:63/73/118/170/199/231/239/256/283/346/378/489/624/633/678/705/711/742/775/835` | `migrate` data_root、pure calculators 与历史查询；CRUD/projection `delete`；742/775/835 importer/merge 到离线包 `read-only` 至 R6，生产 R6 整文件 `delete`。启动隐式导入先移除，不等到 R6 再关 |
| S05 | `packages/scheduler/engine.py:105/122/138/143/148/159/171/263` | startup `_ensure_registered`/`start_loop` 与旧 dispatch `delete`；status/config/preview 能力迁 Creator；stop_loop/drain 桩随调用方撤除；最终整文件 `delete` |
| S06 | `packages/jobs/legacy_compat.py:24/42/81/94/113`；`packages/scheduler/legacy_bridge.py:21/106/133/181/207` | `delete` 两整文件；`project_cursor` 最后一次推进/receipt 及老切换 operation 完成后撤，R6 不保留 bridge 壳 |
| S07 | `packages/jobs/system_actions.py:16/34/68/74/90/112/152/166/176/214/225/236/252/274` | old KINDS/definition/validate/install/arguments/prepare/finish/sync/invoke/settle/recovery_ready/notify/wait_process/execute `delete`；`execute_typed:19` `keep`（或调用者直接 TypedExecutor 后删除空壳）。`packages/actions/store.py:121` immutable/system validation 必须先迁，不能失去固定系统授权 |
| S08 | `packages/jobs/action_runtime.py:49/80/192/213/698/735/742/839/922/942`；`packages/jobs/unified.py:8/59/64` | `migrate` 活 Runtime 生命周期到 FinalService/FinalRuntime，旧 config_job/create_legacy、inline schedule、投影 `delete`；不可按“event_targets”字串删 canonical ledger 的准入方法。config_job 已不调 `_job_from_payload`，但 52 仍依赖 store calculator，旧 inventory 此处须校正 |
| S09 | `packages/automation/engine.py:68/118/174`；`store.py:22/34/196/210/231/242/246`；`chain_engine.py:8/12/51/62` | R1 的 Engine.run_attempt/drain、Ledger.claim/finish/prepare_action/sync_attempt、run_chain 已退场；`keep` Event matching/State/admission/Trigger CRUD，历史 Run `read-only`。`chain_engine.py` 现为 JobAdmission，不能按旧表整删；迁 target 隐式 managed Job 创建与 registry 锁后保持 admission port |
| S10 | `background_jobs.py:3177/3193/3242/3301`；`packages/core/main_lifecycle.py:27/85/183/199/233/266` | `delete` 旧 lifecycle writer/supervisor 分支；`keep` ProtectedLifecycleExecutor/run_canonical_supervisor、进程/端口身份和 ready/offline 检查。旧 run_supervisor/run_exit_supervisor 排空后删除，不能整删 main_lifecycle |
| S11 | `background_jobs.py:3504/3547/3567/3625/3707/3719/3728`；`pan_bridge.py:61` | `migrate` 通知原 event/effect/terminal key、durable enqueue→ack 与恢复证据；旧 producer、loop、bridge `delete`；canonical outbox `keep`，pending/uncertain 不丢或重投 |
| S12 | `independent_service.py:72/123/251/275`；`independent_runner.py:252/339`；`core/background_runner.py:14` | `delete` 第二 Job 生命周期/旧 runner；`migrate` ServiceLock 与 Windows structs 依赖后才整删旧 runner；`independent_executor.py:44/295/321`、`independent_execution_runner.py:13/87` `keep`，仍负责同 Attempt/effect 恢复 |
| S13 | `background_jobs.py:546/1507/1654/1831/1953`；`jobs/api.py:211/217/827`；`pins.py:5/19/37/45` | `migrate` handoff/安全删除/retention/pin 到 canonical metadata 与操作；旧 schema 分支 `delete`。显式证明 pinned/active/unknown/cleanup_pending 都不能被 GC；Settings 读写不能无声丢失 |
| S14 | `jobs/process_cleanup.py:16/80/111`；`owned_process.py:11`；`cleanup.py:19`；`background_jobs.py:3061/3123/3340/3379/3422` | `keep` birth/树/guard/cleanup proof 算法；`migrate` 平台接口与 canonical effect identity，再 `delete` 旧 runner storage glue。Windows Job Object 与 POSIX 采用相同行为契约，不降低断言 |

额外的文件级拆分（以上各行都纳入删除闭包）：

- `packages/jobs/final_runtime/coordinator.py:38` 的 JsonRegistryStore `migrate`；`FinalRuntime:58`/ports `keep`。`final_service.py:37` AggregateStore 可作为 canonical adapter，但必须证明锁、并发、原 ID/provenance、crash/outbox 接管，不是改个 import。coordinator 即使 canonical service 注入了 store，顶层 `background_jobs` import 仍须移除。
- `packages/scheduler/time_events.py:30/67/166/174/206/219/230/238` 的 Sources/Runtime 属过渡来源，不是无害历史 reader：put/reserve 依赖 store 与 `_entry_next_fire`，有 pending publication/ack/link。`migrate` 到 `creators.py` 的 CreatorStore/CanonicalEventStore/CreatorRuntime 和 composition；证明旧 pending envelope 同 identity 结算、游标不重发、source_links 引用完整，再 `delete` 整文件。`set_next_fire` owner-switch 专用在 R6 生产包不得存在。
- `packages/automation/service.py:222` event_page、135 accept、179 deliver、232 ingest_core、242 maintenance、251 dispatch `keep`；313–361 的旧 Runtime、data_root、QuotaRuntime glue、TimeEvents、LegacySchedulerBridge `migrate`。Quota 受保护操作不是旧 Job 一词命中即可删，需沿 `quota_policy`/`quota_host` 调用闭包接管。
- `packages/jobs/independent_service.py:33` ServiceLock `keep`；Handler/serve 健康与 canonical executions `keep`，JobStore `migrate` 成仅托管 execution_host 的容器，旧 jobs/outbox recovery 删除。`independent_client.py:33–39` health/submit/get/cancel execution/events/ack `keep`；41–47 旧 start/get/list/cancel/retry/events/ack `delete`。
- `packages/jobs/independent_runner.py:53/66` `_STARTUPINFOW/_PROCESS_INFORMATION` 先 `migrate` 到 Windows 平台 backend；`desktop.py:50`、`restricted_token.py:183` import 改完且 birth/tree/权限测试通过后删文件。`process_wait`、`desktop`、`restricted_token`、`owned_process`、`cleanup` 通用执行安全能力 `keep`。
- `scripts/job_final_migration/{__init__,__main__,cli,inventory,mapping,apply,render}.py` 与 README：R4/R5 `read-only` 意指受限离线用途（apply 可写自己 target，绝不写 source），R6 `migrate` 独立非生产分发，生产包 `delete`。原包、digest、备份、稳定 ID、rollback 工具/测试归档 `keep`，不设 30/90 天自动删除。

## 5. UNLISTED 全量处理

| ID | 本基线证据 | 动作 |
|---|---|---|
| U01 | `packages/automation/authorization.py` object_path 与 canonical_path；`server.py:10909` | `migrate` 授权后 `delete` 旧 URL allowlist；不得放宽 canonical grants |
| U02 | `background_jobs.py:3075` cancel | `migrate` 原 ID 安全取消后删旧实现，活资源存在时 keep 控制 |
| U03/U04 | 同文件 `855/878` start_action_process/start_scheduled_process | `delete` hidden child builder；先处理父子进程/receipt |
| U05 | 同文件 `3309` claim_hidden_runner | `delete` 旧认领；canonical Attempt fence keep |
| U06 | `system_actions.py:68/74/225/236/252` | 按 S07；安全 validation 先迁 |
| U07 | `jobs/api.py:283/366/439` | `delete` 旧 payload validators；canonical schema validation keep |
| U08 | `scheduler/store.py:118/170/199/239` | `migrate` pure calculator 后删除副本；DST/秒/分数/anchor/误点策略不可漂移 |
| U09 | `scheduler/store.py:705/711` | append `delete`；list `read-only`→审计导入后删 runtime reader |
| U10 | `scheduler/cron.py:1` | `delete` shim，`jobs/cron.py` keep |
| U11 | `jobs/templates.py:30` 附近 BUILTIN_TEMPLATES | `migrate` 完整被动 Template，无 enable/隐式执行 |
| U12 | `jobs/pins.py:5/19/37` | `migrate` canonical pin metadata；同 S13 |
| U13 | `server.py` `_snapshot_main_lifecycle_options`、`_launch_main_restart_supervisor`、`_watch_main_restart`（manifest 精确行） | `migrate` 选项/身份/receipt，旧 Job 分支 delete；不整删 canonical supervisor |
| U14 | `mcp/server.py:3331` job_config_drafts，3351 仍 `/api/jobs/action-jobs` | 当前是旧 Action transport 客户端，`migrate` 到 `mcp/final_tools.py` 的 canonical 工具再撤导出；不能误当早期 19 个旧工具一并清掉而丢配置功能 |
| U15 | `jobs/action_api.py:1`、`final_api.py:403` | `migrate` Action/Template schema 与运行控制后整旧 router delete；final_api 与 `/api/jobs/actions` keep |
| U16 | `packages/mcp/manifest.json:29/38/39` 附近工具提示 | `migrate` 系统提示/目录与 release note；静态加动态目录验证无旧工具宣传 |

## 6. 入口、调用方与保留的查询/控制面

当前静态可证的写入点（外部实际调用量未观察）：

| 层 | 剩余写入链与调用者 | 拆除动作 |
|---|---|---|
| HTTP 旧后台 | server start/message/update/retry/cancel → background_jobs 或 create_legacy；MCP 19 工具与 api.ts/LegacyJobDetails/JobsView 仍调用 | 先撤新效果写；查询/取消/settlement 保留到 G2–G4；最终路由删除 |
| HTTP facade | jobs/api create/patch/delete/pin/run-now/templates/settings → 旧 store；final_api.install 重挂 `/api/legacy/jobs` 并保留原不冲突具体路径 | 删除两个地址面的同 handler，不能只删新加 `/legacy` 前缀；pin/retention/delete 先迁 |
| HTTP Scheduler | tasks create/update/delete/pause/resume/run-now/owner-switch → store/engine/bridge；MCP scheduler_*、scheduleStore 仍调用 | 先断新效果和 owner-switch；查询历史迁审计；整 router 删除 |
| HTTP 生命周期 | restart/exit POST → create_service_job → detached supervisor；Settings/CLI/host_console/bat 调用 | 固定系统 executor 和跨停机 operation receipt 先接管 |
| MCP | 19 旧导出仍在 @mcp.tool，另 job_config_drafts 指向 action_api；`server.py:234/235` 注册 canonical final_tools 为并存面 | 先升级 clients/catalog，再撤旧导出；tools/list 与 tools/call 都验证，不仅 grep |
| CLI/console | `cli.py:101/106` 动态旧 URL + 旧 JSON；console_exit/host_console/bat 均旧生命周期 POST | lifecycle 函数迁 operation，final_cli/final_client keep；oldPid/createdAt/root/port/requestId 与 ready 检查保留 |
| React | router.tsx:10/49 仍 JobsView，:15/33 新 CanonicalJobsView 并存；JobsView.tsx:856/866/894/984 等创建/更新/run/cancel，scheduleStore 与 Settings 仍旧 transport | 新 UI 管理能力补齐后迁 route、WS stores/harness/tests；删除 LegacyJobDetails/NewJobForm 旧形状；非执行不兼容告知仍可看 |
| 独立 service | Handler:359 同时提供 `/jobs` 与 `/executions` 两协议；pan_bridge/background recovery/IndependentJobClient 调旧协议 | 旧协议停止写入，旧 read/cancel/events/ack 排空后删；canonical executions 全保留 |

**过渡期保留**（有明确删除门禁，不变成永久 wrapper）：旧 GET background list/detail、scheduler list/get/runs、legacy facade list/detail/runs、lifecycle status，及旧 cancel/independent events→ack/recover；read-only 不授权 retry/run-now/update。取消已开始效果需真实 owner/fence 与清理 proof，不能因“禁写”一并拒绝后导致孤儿进程。

**R5 删除后保留**：canonical `GET /api/jobs`、`GET /api/jobs/{id}`、`GET /api/jobs/{id}/runs`、`GET /api/job-runs/{id}`、`GET /api/operations[/{id}]`（按原 requestKey 查证）；控制通过 `POST /api/jobs/{id}/operations` 与 `POST /api/job-runs/{id}/operations` 的明确操作/CAS/幂等（`final_api.py:238/302/327/333/363/376`）。无独立旧 `/cancel` 别名。已迁原 ID 用 provenance 索引映射到 canonical，需新增或补全统一**非执行**历史查询契约；本基线没有证据证明该索引已完整，列为 G3 阻塞，不能编造现有审计端点。

独立 service 最终保留 `GET /health`、`POST /executions`、`GET /executions/{effect_id}`、`POST /executions/{effect_id}/cancel`（原 attempt fence）、`GET /execution-events`、`POST /execution-events/{id}/ack`（`independent_service.py:369–396`、`independent_client.py:33–39`）。这些不是 E15 待删的旧 `/jobs*`。`/api/final/*` 是同 handler 的 canonical deprecated alias，`keep` 到单独客户端迁移决定；它不接受旧 kind，不应借 R5 误删。其它 `/api/actions` Action registry 需单独契约核对，禁止按 legacy 文本全删。

## 7. 测试逐项处置与新增退役断言

manifest 列出 79 个后端测试文件、874 个 AST test 定义及 12 个显式命中旧依赖的前端测试文件；每个后端 node 有 `action/stage/line`。这是一份静态影响候选清单，不声称覆盖动态 fixtures/参数化生成。实施包必须再做全仓反向引用和 collection；新增发现附加到 manifest，不可因本表漏列就删断言。

| 动作 | 已存在测试/节点证据 | 实施要求 |
|---|---|---|
| `delete` 旧正向协议断言 | `tests/test_legacy_mcp_compat.py:31/61/90` 三条“可信旧入口仍返回旧形状”用例 | 用 FD02 旧 tool 不可发现/调用、旧 URL 无效果、canonical 授权和原 ID 查询取代；115 unknown 不重放、145 reconciler ownership 用例 migrate，不整删文件 |
| `delete` 旧路径/body 正向断言 | `tests/test_scheduler_mcp.py:124/144/151/178/187/202` 六 nodes | 旧工具下线后删除；48 identity、82 denied target、209 quoting、220 backend error 等迁 canonical transports，安全语义不丢 |
| `delete` 旧事实源/兼容桩断言 | `tests/test_scheduler_store.py:72` create_writes_job_record、279 claim_leader_stub | 原子性/默认值/非法 schema/时间语义迁 Creator/contract；不为旧 JSON writer 保留运行时 shim |
| `migrate` 旧调度/桥接 | `test_scheduler_store.py:320/337/346/353/415`、`test_scheduler_legacy_bridge.py:46/69/85/102`；engine/integration/dispatch/api | 迁移/idempotency/坏输入留离线包；single owner、unknown action、manual run 不推进 cursor 迁 FS/FM/FR。没有新断言与双 owner 证明前不得删除原回归 |
| `migrate` 混合 API/UI | test_jobs_api、test_jobs_completed_retention、test_job_unified_*、test_background_job_api；JobsView.bulk-search/schedule-shapes、api.legacyJobs、jobStore/DetailDrawer/NewJobForm/RetentionSettings | delete 只限旧正向入口/DOM；pin、搜索/批量、错误、历史、授权、每目标 receipt、cleanup_pending 仍保留并改 transport/backend；旧 facade 404 替换成功调用预期，不弱化 canonical 行为 |
| `keep` / 改 adapter | `test_job_delete_safety.py:78/101/116/125/141/163`；test_job_delete_http_owned、test_job_cleanup_failures、test_job_delete_main_combination、test_handoff_job_retarget | birth/PID reuse、整树退出、并发删除/late spawn、unknown 不确认删除、handoff targets 必须保留；只迁 fixture backend |
| `keep` 新模型 | test_job_final_contracts/runtime/admission/creators/executors/independent_execution/http_service/transports/clients；`test_job_final_admission.py:221` retired hosts absent | 全部断言保留；依赖旧 Runtime 的 setup 改 canonical adapter，不能删 test 来掩盖缺能力 |
| `migrate` transport 路径快照 | `test_job_path_flip.py:51/73/139`、`tests/fixtures/job_path_flip_routes.json:1`；`test_job_final_clients.py:52` legacy 部分 | canonical alias/Cookie Origin/Bearer/stdio/授权回归 keep；旧 facade/92 旧工具存在的断言改成精确移除差集；不盲目重录快照 |
| `read-only`→迁独立包 | `test_job_final_migration.py`；`test_job_final_migration_apply.py:72/101/131/195/246/309/318/359/395/417/429` | 六 MIG 类型、complex 不变、stale/atomic/kill-resume/rollback/原 IDs/profile guard 保留在离线包 CI；生产测试不得通过 import 迁移包来自动加载旧 schema |
| `keep` 时间/平台 | test_scheduler_cron；test_scheduler_time_events；test_main_lifecycle_jobs/test_pan_cli/test_lifecycle_shortcuts/test_independent_job_* | cron/calendar 算法 keep，time_events fixture migrate Creator；独立恢复/跨停机查询/权限/本地 Origin/整树 proof 不删 |

R6 仅删除会在**生产启动/运行**生成或加载 legacy 的 fixture/helper；把历史合成样本搬到离线审计/负控 fixture，不能删除未知 schema/恶意输入或原授权负控。manifest 默认 migrate 是“逐 node 保留断言并改 backend”，不是授权整文件删除。

拟新增 `tests/test_job_final_retirement.py` 与 `scripts/check_job_final_retirement.py`（本轮不创建）：

- **FD01 静态与 import**：按 E/S/U 绑定 denylist，AST 校验删除模块/符号、动态 import/`-m`/bat/manifest/types 的闭包；新 FinalRuntime/FinalService 的 imports 不再经过 background_jobs/store/legacy_bridge。正常 startup 不读写旧 scheduler/tasks、runs 或 independent jobs 表。不能对整个 tests/docs 放宽排除；历史文本白名单有条目、理由、owner。
- **FD02 对外退役**：生产 route 遍历 + OpenAPI + first-match TCP 实测；manifest 中旧 routes 两地址面全不在，旧 POST/PATCH/DELETE/cancel/retry/run-now 都不产生新记录/进程/lease；404/405/明确 retired 均需核查无副作用，最终目录无旧入口。MCP tools/list/catalog/core/native 及 tools/call 均验证，CLI 动态拼接路径、GUI DOM/网络无旧写。canonical `/api/jobs/actions` 不被 ID route 吞掉，`/api/final` 同 endpoint 仍有效。
- **FD03 发布包**：从实际发布工件解包扫描，不只扫 checkout；无旧模块/owner-switch/loader/dual flags/迁移 CLI；canonical 独立 executions/service/runner、Windows structs backend、跨停机 supervisor 可 import/启动，生产包不依赖离线迁移包。
- **FD04 非执行保留与安全**：复杂源字节/路径/hash不变，无 Job/Run/Attempt/lease生成，startup/upgrade/GC 不自动读取执行/删除；已迁原 ID 可查完整历史/原因、canonical cancel 原 Attempt 生效；旧 unknown/uncertain 无重发；pending notice 原 key durable enqueue 后才 ack；pending cleanup 仍阻止物理删除。
- 补充资源恢复场景：Pan 停机期间独立 effect 完成→重启按同 effect/Run 恢复、duplicate/late epoch receipt 拒绝、无第二 Job；lifecycle 接受后服务离线仍可查原 operation、不重试固定系统效果；rollback 前后 fencing/已开始新 effect 不允许逆向复活旧 owner。

未来验证命令：先经 `python scripts/run_tests_isolated.py -- python -m pytest <受影响nodes> -q`，再完整 `python scripts/run_tests_isolated.py -- python -m pytest tests/ -q`；产品前端修改后 `packages/web` 执行 `pnpm build`，相关 Vitest 和隔离 authenticated browser/HTTP/stdio。三平台独立安装/import `packages.web.server`/collection/契约单元；ubuntu 慢层、Windows ConPTY/Job Object 明确 skip/实测。每次记录 exit/counts/skips/JUnit/SHA，不修改断言让迁移“变绿”。本轮这些命令均未执行。

## 8. 并行拆分与集成顺序

以下是未来派发建议，本轮没有创建/派发子任务。每组独立 worktree/branch、唯一 owner；同组内部顺序执行。

| 包 | 独占文件组 | 交付与依赖 |
|---|---|---|
| P0 接管契约/owner 证据 | 离线 migration importer/切换证据设计、manifest 与隔离测试；不改 P1–P5 产品文件 | 先定义 storage/锁/历史索引/receipt 接口，完成 G2/G3 证据；不把离线 attestation 当 runtime authority |
| P1 runtime/registry | jobs/action_runtime、final_runtime/coordinator/ledger、core/background_jobs/background_runner、jobs/legacy_compat/unified/pins/pan_bridge；其专属测试 | 先迁 store/锁/pin/retention/handoff/outbox，最后删旧宿主；交接口给 P2/P3/P4；不得先删除其它组仍 import 的模块 |
| P2 Scheduler | scheduler 除 api.py 的全部过渡文件/creators/composition 与 calculator；其专属测试 | 迁 source/pending/link/cursor，删 store/engine/time_events/bridge/cron shim；store 迁移实现移出生产由 R6 收尾 |
| P3 Automation | automation engine/store/service/chain_engine/job_links/quota_policy/quota_host；其专属测试 | 保留 State/Event、改 canonical admission/来源接线；依 P1/P2 接口；不修改 authorization/auth（归 P5） |
| P4 executors/platform | jobs/system_actions/independent_*、desktop/restricted_token/owned_process/process_cleanup/process_wait/cleanup、core/main_lifecycle、actions/store/typed_executor、platform backends | 保留 canonical service/runner、安全算法与 protected lifecycle；先提供 structs/锁接口，P1 才能删旧定义 |
| P5 HTTP/MCP/CLI | web/server.py、jobs/api.py/action_api.py/final_api.py、scheduler/api.py、mcp/server.py/final_tools/manifest、automation authorization/auth、core cli/console_exit/host_console、restart/stop.bat | 唯一共享入口 owner，删 dual-address remount；提供最终 route/tools/CLI schema 快照；P1–P4 只给接口补丁需求 |
| P6 React | web/src views/components/services/types/stores/router/hooks/harness 与前端测试/e2e | api.ts/canonicalJobs.ts 仅该组修改；依 P5 协议，先验收管理功能/历史告知再删 legacy UI |
| P7 组合/R6 | 发行打包配置、retirement scanner/denylist、CI、矩阵/用户文档、受限离线迁移包清单 | 在其它包已整合后串行做全仓 import/route/产物闭包；R6迁移包分发调整不得与 P0 同时修改 scripts/job_final_migration |

推荐落地顺序：P0 接口冻结→P1/P2/P4 能力迁移可并行→P3/P5 接线→P6→P1/P2/P4 旧宿主最终删除→P7 组合。阶段内候选 PR 可并行，但“删除被依赖文件”必须等待所有消费者迁移合并。跨组混合回归如 test_job_path_flip/test_job_final_clients 由 P5 持有；各自专属测试见 manifest，MA 派发时把共享 test fixture/通用 conftest 归 P7，不允许重复认领。目标分支推进、push/远端 CI/部署/真实数据操作不包含在本次授权内。

## 9. 风险与逐项缓解

| 风险 | 具体误删链 | 缓解与阻断证据 |
|---|---|---|
| 活资源查询/取消丢失 | 删 E01/E15/registry 导致 runner/supervisor 不可找；cancel 仅按 Job ID 无 birth proof | G2 全 cohort/PID+birth/整树/epoch 证据；先验 canonical 原 ID→effect 查询/取消；unknown 与 cleanup_pending 阻断删除，不用 kill/retry 冒充恢复 |
| 历史 Event/Run 无法读取或被重新执行 | 整删 automation Ledger、runs JSONL/time-events sqlite；把旧 historical rows 当 pending admission | event_page/历史 pins/通知关联保留；原 ID/provenance/history 索引字段守恒、分页/重复导入测试；recordType fence；复杂数据 read-only 不自动加载、不生成 effect |
| 独立 service 恢复中断 | 整删 independent_service/runner/client、过早删旧 outbox ack | 区分 jobs 与 executions 两协议；保留 ServiceLock/execution_host/typed receipts/canonical runner，迁 Windows structs；跨 host 停机/late receipt/ack crash 同 identity 测试 |
| 数据迁移回滚重开双 owner | apply 成功当 owner 切换；rollback 旧二进制自动读取原 records | 显式 runtime import+fencing authority+epoch、受控激活与部署 readback；按原 operation 查询；新效果已开始不得 rollback-to-old，转受控恢复；离线 rollback 仅撤 disabled 发布；备份/ID/复杂数据受限保留无期限 GC |
| 时间语义/次数丢失 | 删 store/calculator/TimeEvents 导致 anchor/DST/maxRuns/pending cursor 变化 | FS01–05 对照秒/分数/zone/DST/100 occurrence、skip/misfire/pause、publication 三断点；原 envelope/occurrence/drive key 结算，不用内容 hash 合并来源 |
| 通知重复/丢失 | 删除 recovery/legacyId 关联后新 outbox 再发，或先 ack 后 durable enqueue | 原 event/effect/terminal key 映射；pending=0 或已转原键证明，enqueue/ack 各 crash 断点测试；unknown 不自动 resend |
| 通用管理/权限被一并删掉 | pin/retention/handoff 随 legacy facade 删除；system validate 移除使 high_risk 放开 | G4 功能映射逐项验收；原 grants/revision/可信 IPC 不升级，效果前撤权；固定 lifecycle 与任意 high_risk 分开负控；能力矩阵同步 |
| 用删测试掩盖未接管 | 删除旧文件连带 PID/tree/授权负控与全部迁移 fixtures | 每个 migrate node 有后继 node/JUnit/相同断言；只删明确旧正向协议；保留 red evidence，不能 skip/吞错误/unknown=success |

## 10. 交付/验收清单

每个实施包提交 `baselineSha/sourceSha/pathsAdded/Changed/Deleted/E-S-U/removedRoutes/tools/symbols/replacementNodes/commands/exitCodes/counts/skips/artifactDigests/unverifiedLayers`。阶段签收另列 simpleMigrated、complexPreserved、pendingClassification、legacyExecutableRecord、oldOwner、unknownOwnership、pendingNotice、cleanupPending、owner fence proof 与 reviewer。复杂保留的业务明确“未迁移，需手动新建”，不称全部迁移成功。

R5 验收需源码闭包、单测、构建、真实 HTTP/stdio/浏览器、所有 profile owner/历史映射分别交证据；R6 再交发布包、三平台 CI、获授权真实副本 migration/crash/rollback、部署版本/route/tools/owner readback 和人工验收。任何层缺证据均单独标未验证。

本轮证据限于 Git 状态、源码/设计静态读取、AST 清单与文档引用校验；产品测试、构建、HTTP/MCP/浏览器实测、远端 CI、真实数据/owner 迁移、部署与人工验收均未做。最终提交只含本文及 manifest。

文档自检：manifest JSON 可解析；51 个文件、982 个类/函数/方法定义、59 条 HTTP decorator、20 个 MCP 符号、79 个后端测试文件/874 个 AST test 定义与 12 个前端测试文件均有基线定位；全部符号及 test node 行号/名称、正文完整路径的行号范围、E/S/U 连续覆盖校验通过。该静态检查不验证产品行为或真实 owner 状态。
