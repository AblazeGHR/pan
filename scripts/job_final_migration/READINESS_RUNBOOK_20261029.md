# 合成离线迁移 readiness 演练

基线为 `d350efaf87e6ae60a5a60f037a4d736b52a2243b`：已审查、整体尚未验收的 r14 候选，Windows quoted-shell 原红仍开放。本批不是 r14 产品验收，不改变平台冻结候选。最终验证源及命令结果见仓库 `evidence/job-migration-readiness-20261029/REPORT.md`。

## 操作边界

只使用本批工厂生成的合成记录，source root 只读；target 位于 source 外。默认 dry-run 不创建 target 或其父目录。显式 `--apply` 原子发布 disabled Job/Trigger/Creator、draft Template 和原始 bytes 备份。`plan.executable=true` 只表示离线包提交，`runtimeActivated=false`、`runtimeFenceVerified=false`；没有运行导入、激活、旧 owner 退役、队列投递、进程/服务或真实 profile 迁移证明。

不得把本文命令替换成真实 data/profile。真实数据读取、可信授权、当前 grants、provider 可用性、connected cohort 排空、跨进程 fence 和 runtime import/activation 都需要独立授权与验收。不要调用本包已有的 `p9d_rehearsal`：它会创建运行时 store 并执行隔离导入，不属于这次离线链。

## 一条可重复命令

在此候选仓库根执行，每次换一个**不存在**的自有输出目录（脚本拒绝覆盖）。不安装依赖、不访问端口。

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe tests/support/job_migration_readiness_rehearsal.py --output .validation/job-migration-readiness/operator-drill-01
```

工厂复用原测试的六类型 source/context，添加合成异常与现代一次 Scheduler/广播。内存 collector 调用当前 `FinalService._install_product_actions` 收集发布定义：不实例化 FinalService、不建立 DB/OwnerFence、不调用 start/import/activation；发布方法及 schema 均来自同一源码。它证明新空环境对应的 schema/Action pin，不证明真实 profile 当前 release 或 provider 可用。

生成物全部在指定输出目录：`01-inventory.stdout.json`、`03-dry-run.stdout.json`、`04-apply.stdout.json`、`dispositions.json`、`summary.json`、`source-before.json/source-after.json`、每步 `.command.json/.stderr.txt/.stdout.json`、`crash-recovery.json`、`review-packet/` 及 `publication-not-yet-created/bundle.json`。原始 source/context/bundle 只保留在忽略的 `.validation` 内；提交证据只取合成统计、逐条处置、hash 和命令。

## 清点范围与处置

| 当前固定输入 | 工具行为与本轮边界 |
|---|---|
| `background_jobs/jobs/*.json` | BG、单条消息/广播、scheduled-task、Action Job；main-lifecycle 不在白名单；复杂/活动/未知身份原状保留 |
| `scheduler/tasks/*.json` | legacy task 与现代 scheduled-task 都可盘点；仅明确 once/固定 anchor interval/原生 cron 的白名单形状进入映射；已消费计数、复杂恢复、pending effect 拒绝 |
| `actions/registry.json` | 原 Action ID/generation 引用及完整被动 Job Template；source revision、原授权与引用证据保留；不把旧 system_job 执行定义当当前 Action release |
| `automation/registry.json` | Trigger 定义及历史 runs/pending 关联；活动/unknown/uncertain 不重做；旧 event 参数移至 admission input |
| `background_jobs/schedule_templates.json` | schedule-only Template 缺完整 Job/授权时不补默认，不等于完整被动 Template |
| 两处 `runs.jsonl` | 原始 bytes 进入备份；关联运行/收据影响准入；保留历史，不制造 canonical Run |
| 配置里的 next/last fire、run count、pending/cursor | 保留逐记录 cursor/provenance 与备份；未证明当前运行时接续 |
| 其他文件、SQLite sources/outbox、外部 owner/服务、独立 cursor 文件 | 不属于 lite inventory 固定扫描范围；演练故意放入 `scheduler/cursors-unported.json`，它在全 source hash guard 与快照中保持不变，但**不在迁移包备份/盘点内**。不能宣称全 profile 完整盘点、零 legacy 或迁移完成 |

合成主链共 20 个可识别/未知实例：8 条离线迁移、9 条 complex、3 条 unknown，共 12 条保留。unknown/坏 JSON/重复 key 使默认整次 dry-run/apply 拒绝；只有显式 `--preserve-incompatible` 与 context 的 partialPolicy、selectedMigrationIds 一起固定后，才允许这 8 条离线提交。两条坏 JSON scan error 在 partial 模式有可验证 raw bytes，其他不可证明扫描错误继续阻断。

保留条目涵盖 active/uncertain 资源、pending notice、无法证明来源授权、未知字段、monitoring/stable reset、target clear/fallback 未知选项及损坏/未来 schema。每条保留原 ID/路径/hash/reasons、`targetIds=[]`，不得把 active 解释成真实进程仍运行，也不得自动取消、重发或清理它。lite simple 只表示形状候选，不意味着权限已核验；缺 authority/fence attestation 的独立负控继续拒绝。

## 操作员步骤与检查点

1. 先审阅 inventory 的分类、字段映射、source hash、cursor、授权/资源/收据理由；sourceTotal 必须守恒。审阅不能从旧 `source=agent/system` 推定 user 权限。
2. 严格模式对未知源拒绝。确要部分离线包时，独立审阅 partialPolicy 与 selectedMigrationIds，未选/不兼容身份绝不分配目标对象。
3. dry-run 返回 context+inventory 绑定的 `planDigest`；确认 target 与父目录不存在。显式 apply 必须原样提交这个 digest，不自动接受新计划。Context/授权输入不能和 target、report、`.bak`、`.lock` 重合。
4. 同一 root/context/plan/target 重放必须 targetWrites=0、target bytes/mtime 不变、operationId/allocations 不变。source/history/context 改变必须 `plan_stale` 且 target hash 不变。先重新清点审阅，不绕过 CAS。
5. rollback 使用当前 `bundleDigest`，原子发布 rolled-back-offline：canonical=[]，原始备份、IDs、entries 保留。`.bak` 保存上一个完整包。它不恢复旧执行能力，不是在线 downgrade。再次 apply 复用原 IDs。
6. 可重复演练在 os.replace 前/后让**自有子进程** exit 77，验证旧/新完整包可见，锁仍阻断。仅有该 child 的终态回执后，操作员清除这个确切 target 的自有 lock 和已记 hash 的孤立临时文件，再用原计划恢复；工具绝不自动判锁死、换身份或读取孤立临时包。普通 write/fsync/replace 失败还由原测试验证。
7. stable_copy 对合成 source 文件做双 hash，P9C review packet 再核对 manifest/hash/inventory。它始终 review-only，不能把 draft/context 改成 approved 来取得 apply 权限。P9C 的 allowRealProfile=true 是既有快照 inventory 元数据合同，这里对应完全合成副本，不访问真实根。
8. 最后 source-before==source-after，全 root 文件集合/hash 不变；remainingTemporaryArtifacts=[]。只保留自有输出供审阅，不删除或移动原树/其他进程。后续人工清理限本次明确拥有的输出目录，先核对绝对路径。

## 当前契约及未验证层

当前 process release `product_process_logs_v1`、session-message/broadcast/assign v2 与条件发布的 channel schema 由源码收集，严格校验输入、Action ref revision/digest、completion、对象 schema、引用与 name 限制。消息显式 sourceSessionId 不能丢。未发布/禁用 Action、缺引用、错误参数和超长名称阻断，不能回落另一动作。内置没有 `process.shell` release；旧 shell 只能使用独立明确发布、schema 一致的 context binding，否则 fail closed，本批不生成虚假 release。quoted-shell 序列化原红不在此修复范围。

旧 recovery.monitoring/stable reset 生命周期不能只因 canonical schema 容纳 duration 就视为可迁：当前 FinalService._references 的 automatic recovery 仅支持 process failed/timeout、finite policy、stableResetSeconds=0；旧 monitoring 标志没有无损映射，本批保留原实例。非自动 recovery 的参数作为显式策略/审计保留，不代表监控生效。target clear/fallback 不添加到现有产品 schema。

时间只采用显式 offset/IANA zone，不按 host 猜；DST fold/gap 拒绝。标准 ISO 字符串的原始小数秒精度保留；本轮直接测试纳秒及 naive+明确 zone。interval anchor、cron/暂停/上限/原 cursor 继续由原 apply 测试验证。完整 100 发生点 shadow 对照、所有历史时间格式、真实 provider 可达性、真实通知 ack/未知效果结算、旧 owner 清零、真实 crash/powerloss/network filesystem、真实 profile rollback、三平台 CI、部署与人工验收均未证明。
