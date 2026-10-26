# WP08 离线迁移 apply

此包在 lite inventory/plan 上扩展，只处理 `simple-script-migratable`。**root 始终只读**；
`--target` 是 root 外的私有 JSON 迁移包，包含 WP01 `schemaVersion=2` canonical objects、稳定 ID
分配表、来源/hash、逐条迁移证据、源文件原始 bytes 备份及只读/owner 切换意图。
它不是运行中的 store，也不会自动被服务加载。所有 Job/Trigger/Creator 都是 disabled，Template 是 draft。
原启用/暂停意图、cursor、历史结果和授权材料单独保留，不伪造 canonical Run，也不把旧 payload 放进新执行定义。

`--apply` 成功原子发布整包后，返回报告的 `plan.executable=true`；默认 dry-run、失败和 rollback 均不置 true。
此字段只表示离线 apply 已提交，**不是运行准入/业务已恢复**。报告始终标明
`runtimeActivated=false,runtimeFenceVerified=false`。不 import server，不启动进程/服务，不改运行时、旧 store/pass 或源文件。

## CLI

以下变量必须指向自己拥有的临时 fixture，不能套用到真实 profile：

```powershell
$fixtureRoot = 'C:/Temp/wp08-fixture/data'
$migrationTarget = 'C:/Temp/wp08-fixture/canonical.json'
$migrationContext = 'C:/Temp/wp08-fixture/context.json'
$migrationReport = 'C:/Temp/wp08-fixture/dry-run.json'

# 保持 lite 接口：只读盘点。--root 和 --out 必填。
python -m scripts.job_final_migration --root $fixtureRoot --out -

# 不加 --apply：校验上下文、六类型映射、schema/引用；不创建 target 或它的父目录。
python -m scripts.job_final_migration --root $fixtureRoot --target $migrationTarget --context $migrationContext --out $migrationReport
$reviewedPlan = Get-Content -Raw $migrationReport | ConvertFrom-Json

# 审阅报告后，用该次 planDigest 提交；源/上下文改变均报 plan_stale。
python -m scripts.job_final_migration --root $fixtureRoot --target $migrationTarget --context $migrationContext --expected-digest $reviewedPlan.planDigest --apply --mark-read-only --out -

# 原输入重跑：返回同一 IDs/operationId，不重复写 target。
python -m scripts.job_final_migration --root $fixtureRoot --target $migrationTarget --context $migrationContext --expected-digest $reviewedPlan.planDigest --apply --out -

# 回滚离线发布；预期摘要取当前包，保留 ID、来源和备份，不重启旧 owner。
$currentBundle = Get-Content -Raw $migrationTarget | ConvertFrom-Json
python -m scripts.job_final_migration --root $fixtureRoot --target $migrationTarget --rollback --expected-digest $currentBundle.bundleDigest --out -
```

也支持 `python scripts/job_final_migration/cli.py ...`；报告可用 `--format md`，rollback 固定输出 JSON。
`--root`、报告、target 均显式指定。报告/target/backup/lock 不能重合，输出拒绝 root 内、符号链接或 junction。
报告硬链接拒绝；报告和迁移包均通过临时文件原子替换写入。`process.json` / `data/process.json`
仍在任何源读取前触发真实 profile 保护；必须 `--allow-real-profile` 才可读，但该 flag 不提供迁移授权、fencing 或运行激活证明。
本工作包没有接触真实 profile。

## 迁移上下文 v1

lite 的 simple 只证明白名单形状，不证明真实授权、provider 可用或旧 owner 停止。
因此需要显式 `--context`，不从 `source=agent/system` 或 `creatorSessionId` 升格推定执行权限。
上下文是受控离线输入/操作员声明；脚本校验结构、已有 authorization 主体一致性和引用，但不谎称已独立验证声明。
缺字段、未知版本、不同摘要、Action 未发布/已禁用/版本或 digest 不符都 fail closed。

顶层字段：

| 字段 | 内容 |
|---|---|
| `schemaVersion` | 整数 `1`；不是 canonical schema 版本 |
| `profileId` | 目标 profile 的明确标识，不从目录名猜 |
| `actions` | 映射键到 `{object,ref,completions}`；object 是完整 WP01 published+enabled Action；ref 的 digest 必须等于其规范化 definition 的 WP01 digest |
| `records` | 以 inventory 的 `migrationId` 为键的逐条上下文；complex 不需要也不会迁移 |

`actions` 的键：MIG-BG 用 `process.exec`；MIG-MSG 用 `session.send` / `session.send-many`；
MIG-SCHED 用 `session.assign` / `session.send` / `process.shell` / `qq.send`；
MIG-ACTION-JOB/TRIGGER/TEMPLATE 用原 `actionId`。
`completions` 按源 `ordinary` / `monitoring` 映射到显式 WP01 `{contract,parameters?}`，必须在固定 Action 声明中存在。
带旧 signal 参数但缺确定映射时拒绝，不能静默丢参数。Action literal arguments 按 pinned inputSchema 校验。

每条 `records[migrationId]` 必须提供：

```json
{
  "originalDigest": "sha256:<inventory originalDigest>",
  "principal": {"type": "user", "id": "fixture_user"},
  "owner": {"type": "user", "id": "fixture_user"},
  "authorizationEvidence": "受控授权证据引用；不能填凭空推定的权限",
  "oldOwnerStopped": true,
  "oldOwnerEpoch": 1,
  "fenceEvidence": "已停止旧 owner 的证据引用；临时 fixture 明确从未存在 owner",
  "importedAt": "2026-10-25T00:00:00+00:00",
  "policies": {
    "overlap": {"mode": "forbid", "maxActive": 1, "maxPending": 0},
    "recovery": {"automatic": false, "maxAttempts": 3, "backoffSeconds": ["5", "15", "60"], "stableResetSeconds": "300"},
    "notification": {"on": [], "recipients": []},
    "retention": {"policyRef": "fixture_policy", "pinned": false}
  }
}
```

这段只展示结构，策略值必须来自来源政策。Action Job/Template 的 recovery、overlap、通知接收人及状态
会与源逐项核对；旧通知 `unknown→uncertain,cleaning→settling`，按源顺序去重，原词保留在源备份。
Trigger 的 retry budget 和并发上限必须一致。旧未指明的 maxPending、通知通道、retention 不用新建默认猜补。
已有 authorization 的主体不能借 context 改成别的 user/agent。

有 schedule 的条目还需要 `scheduler`：
`providerRef`（WP01 time provider ref）、`configSchemaRef`（固定 Ref）、`requiredCapabilities`、
`graceSeconds`、`pausePolicy`；interval 还需 `advanceMode="grid"`，cron 还需 `dstPolicy`。
有直接 Trigger 时还需 `creatorSelector`，明确既有 provider/source 的选择范围。
完整、可执行的合成上下文工厂见 `tests/test_job_final_migration_apply.py::context_for`，只服务于临时 fixture，不能用于生成真实授权声明。

## 映射与保留

| 来源 | 目标与证据 |
|---|---|
| MIG-BG | process Action Step + 手动 Job；exit/log/资源收据仅 historicalEvidenceOnly，无自动 Run |
| MIG-MSG | session send/send-many Job + once Creator + Trigger；delay 用已落盘 nextRunAt，广播按顺序去重 |
| MIG-SCHED | assign/send/shell/QQ Step + Job/Creator/Trigger；once/固定 anchor interval/native cron；未消耗 maxRuns 映射 Creator driveLimit |
| MIG-ACTION-JOB | 静态单步/链，固定 Action ref、原 configRevision 与策略；定时则附 Creator/Trigger；runLimit 归 run-admission |
| MIG-TRIGGER | 非执行 Trigger + 受管 Job；event bindings 移到 admission arguments，Job Step 从 input 取值；原 ID/revision 在 provenance 保留 |
| MIG-TEMPLATE | 完整被动 Template，可包含时间 composition；原 revision/provenance 保留，无实例运行 |

target IDs 来自 `migrationId` 的确定角色分配，并与 operation 一起持久保存，不按业务内容合并不同来源。
原 ID/task/entry、source path/file hash、record digest、schema fingerprint、cursor、fieldMap 和审计字段保留在 entries。
来源的本地时间只按显式 zone 解释；模糊/不存在的本地时间拒绝，不猜 DST fold。

complex 原位置/hash 不变，仅写非执行 preserved 清单，不产生 targetIds，不进入 migrated。
unknown/坏 JSON/重复 key/扫描错误会拒绝整次 apply，避免只迁已读到的一半。
apply 前后核对扫描输入集合和原始 bytes，新增/修改 ledger 或来源文件会触发 plan_stale。

## 原子提交、备份、rollback 与只读意图

所有对象、ID 分配、源文件 bytes 备份（base64）、hash、来源证据和 owner 意图在一个 JSON 包中提交。
先在同目录写临时文件并 fsync，再用 `os.replace` 原子发布，避免逐对象留下半张图。
同 target 用独占 `.lock` 串行化。已有包更新前，先原子写 `<target>.bak` 保存前一完整包。
源备份保留在包中，`bundleDigest` 覆盖整个包；未知 operation 版本/损坏摘要拒绝恢复，不能新建 ID 重试绕过。

`--mark-read-only` 仅在离线迁移完成时持久化 `oldRecordReadOnly=true`，ownerEpoch=旧 epoch+1，
新 owner 状态为 canonical-disabled。普通重跑保持标记。此处是供后续受控集成读取的意图，
**没有修改源记录权限，也未建立跨进程 fencing authority**；`runtimeEnforced=false`。
在用户要求不改运行时/不启停服务的边界内，不把此意图冒充设计 §5.2 的真实 owner 切换。

rollback 原子改成 `rolled-back-offline`，撤销当前包的 canonical 发布和只读意图，保留 allocations、entries、
原始备份及前一包 `.bak`。不会恢复旧执行能力、删除源数据或进行在线降级。再次 apply 复用原 IDs。
进程在 replace 前退出时可见旧包，replace 后退出时可见新包；中断留下的 lock 不自动判死。
操作员先核实原进程确已退出，再清除该 target 的 lock 后，用原 root/context/target/planDigest 重跑。
孤立临时文件不是已提交记录，不被自动读取。没有复用真实资料、自动 GC 或删除源 store 的入口。

### P9B partial preserve

显式 `--preserve-incompatible` 可在审阅过的 context 中执行部分离线 apply。context 必须包含：

```json
{
  "partialPolicy": {"version": 1, "mode": "preserve-incompatible"},
  "selectedMigrationIds": ["<simple candidate migrationId>"]
}
```

`selectedMigrationIds` 会绑定到 `planDigest`，只能包含当前 inventory 的 simple candidates。selected
记录才会生成 disabled/draft canonical objects；complex、unknown、未选 simple 及未分类 source 文件进入
`preserved`，带原路径、hash、原因和 `targetIds=[]/historicalOnly=true/executable=false`，不执行、不重投。
默认 apply 不变，仍遇 unknown/corrupt 或 scan error 整体拒绝。部分 apply 仍要求每个 selected record 的
当前授权、Action/provider 引用、owner/fence 证据完整，不能用 partial flag 绕过授权。

这是本地文件系统上的原子发布协议，未宣称网络文件系统、真实断电落盘或恶意目录替换下的跨主机一致性。
source snapshot、context 和迁移包可能含命令/文本/授权材料，必须按私有迁移材料保管，不能提交到仓库；
仓库证据只保存合成 fixture 测试、统计、命令与 SHA。

## 支持与验证矩阵

| 功能 | Windows | Linux / macOS |
|---|---|---|
| 离线 inventory/映射/单文件 apply/rollback | 本工作包临时 fixture 实测 | 使用同一 pathlib/os.replace 合同；本轮未实测 |
| 原生进程/服务迁移执行 | 不提供；只生成 disabled 定义 | 不提供；不按运行脚本所在平台推定 target provider 可用 |
| 真实 profile / owner fence / 激活 | 本轮未验证 | 本轮未验证 |

运行 focused tests：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_final_migration_apply.py tests/test_job_final_migration.py tests/test_job_final_contracts.py -q -o addopts= --junitxml=artifacts/wp08-apply-tests.xml
```

覆盖六类型、scheduler action/rule 变体、broadcast/chain、重复 apply、root 快照、unknown/corrupt/complex、
部分 write/fsync/replace 失败、源变更、引用/摘要冲突、只读/rollback、提交边界真实子进程退出，以及独立进程无 server import。
远端三平台 CI、真实 provider/授权/目标、100 个发生点/DST/pause/misfire 语义对照、真实复制数据和生产迁移、
运行时 fencing/lease/activation、真实断电、部署与人工验收均未验证。
