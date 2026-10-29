# Job migration readiness：离线候选

基线：`d350efaf87e6ae60a5a60f037a4d736b52a2243b`。这是已审查、整体尚未验收的 r14 候选；quoted-shell 仍红，不能把它写成整体 accepted。

验证源：`c89866f9db23d3833994511353c51cd18fe3cf6c`（其父 `198a416737574d866dae022a4a83e81aa5bf1239` 包含脚本修复及演练；后继只修本批 shell 缺引用负控夹具）。后续交付提交仅含本目录证据。分支 `r6/job-migration-readiness-20261029`，树 `D:/project/pan-worktrees/job-migration-readiness-20261029`。

## 结论与修复范围

可交 MA 审阅的**合成离线工具链**已闭环：完整固定范围 inventory、分类、严格拒绝、显式部分计划、dry-run、reviewed digest apply、幂等、source/context stale 拒绝、rollback、同 ID reapply、原子提交边界崩溃恢复、快照双 hash 与 P9C review packet。不是运行迁移或产品验收。

仅修改 `scripts/job_final_migration/{inventory,mapping,cli,apply}.py`：

- inventory 对旧 recovery.monitoring/stable reset 明确 incompatible-preserved，避免漏掉 monitoring 语义却输出 simple。当前产品 automatic recovery 的 process/failed-timeout/stableResetSeconds=0 限制未改变。
- mapping 保留消息显式 sourceSessionId；标准 ISO 字符串小数秒原精度保留，时区仍显式，DST fold/gap 拒绝。
- CLI 在任何发布前拒绝 context/独立授权输入与 bundle `.bak/.lock` 路径碰撞，防止后续包更新覆盖输入。
- rollback 报告补齐 runtimeFenceVerified=false，和既有离线合同一致。

新增专属测试 `tests/test_job_migration_readiness.py`（19 nodes），支持脚本 `tests/support/job_migration_readiness_rehearsal.py`，操作手册 `scripts/job_final_migration/READINESS_RUNBOOK_20261029.md`。packages、产品/全局 fixture、原测试/断言均无差异；`source-guard.json` 记录精确路径、源 tree/hash 和 negative diff guard。

## 本轮证据

| 工件 | 结果 / exit | 含义 |
|---|---|---|
| red.xml / red.log | 4 failed / 1 | 修复前 monitoring 两节点、context-backup 碰撞、sourceSessionId 丢失 |
| red-successor.xml / log | 3 failed, 11 passed / 1 | 新增纳秒两节点与 rollback 边界字段原红；前四节点已绿 |
| regression.xml / log | 203 passed / 0 | 开发时原五模块组合；非最终 immutable 验证源 |
| final-regression.xml / log @198a4167 | 207 passed, 1 failed / 1 | 新负控夹具用相对 shell 命令，正确被提前 preserve，未达到期望缺引用拒绝；原 red 保留，未削弱断言 |
| verified-regression.xml / log @c89866f9 | **208 passed, 0 skipped / 0，38.76s** | 19 专属 + 189 未改动的原 inventory/apply/review-packet/contracts tests；含原 write/fsync/replace 故障及 before/after child exit77 测试 |
| verified-rehearsal.log + summary.json @c89866f9 | **exit 0** | 独立公开 CLI 完整演练及 crash recovery、快照复核 |

所有测试由指定 `D:/project/Pan-main/.venv/Scripts/python.exe` 经 `scripts/run_tests_isolated.py --` 同解释器运行，本机 Windows/Python3.14.5。完整最终 argv 在 source-guard.json；CLI 每步 argv、exit 在 cli-commands.json：15 次受控子命令，8 个 exit0、5 个**预期拒绝** exit1、2 个**预期崩溃** exit77。不把预期失败吞成零退出。

20 条合成源：8 simple（六类均覆盖，外加广播和现代 Scheduler）、9 complex、3 unknown；8 条生成 17 个 disabled/draft canonical objects，12 条 preserve 且 targetIds=[]，sourceTotal=8+12、pendingClassification=0。逐条原 ID/位置/hash/理由见 dispositions.json。所有 sourceBackups 的 base64 bytes 和源逐文件相等，所有对象通过当前 canonical schema 与引用验证。

实际当前产品 Action 发布方法仅由内存 collector 执行，未实例化运行时服务/DB；process logs v1、message/broadcast/assign v2、channel 的 definition/ref/digest 列于 summary.json。它证明同源码的新空环境发布合同，不证明真实 profile 权限或 provider 可用。没有内置 process.shell release：有效 legacy shell 缺显式 binding 时仍拒绝，无动作 fallback。

幂等验证 targetWrites=0、bytes+mtime 不变、operation/allocations 不变。context 编辑与 history source bytes 编辑均 plan_stale，target hash 不变。fixture 操作员为 stale 用例暂时改动**合成** history 后恢复；迁移工具自身只读 source。rollback 保留 entries、allocations、sourceBackups，canonical=[]；reapply 同 IDs。

真实 owned 子进程分别在 os.replace 前/后 exit77：前者无已提交 target，后者完整新包可读；两者 lock 均继续拒绝。仅在自有 child 终态确认后，演练显式清除确切 lock 与记录 hash 的孤立 `.tmp`，用原 root/context/digest 恢复，IDs 不变。crash-recovery.json 记录全部事实；remainingTemporaryArtifacts=[]，未扫描/终止其他 PID。

source-before.json==source-after.json；全 root 额外 `scheduler/cursors-unported.json` 也原样保存并稳定复制，但它不在 lite inventory/包备份 allowlist 中。summary.unscannedSourceFiles 明确列出，不能据此声称全 profile 库/游标/owner 已迁移。P9C packet 仍 draft/review-only，不能提供权限；未调用 p9d_rehearsal 或任何 runtime import/activation。

早期演练失败也保留：drill-01.log（夹具子 CLI 编码；后继显式 -X utf8），drill-02.log（快照 cached inventory 的 allowRealProfile 元数据不符合既有 P9C 合同；后继以合成副本要求的相同参数建立 cache）。没有改产品/复核器、hash 或旧断言来绕过。drill-03.log 与 final-rehearsal.log 是开发/父源成功，不替代最终 c898 证据。

## 重放与验收下一步

从候选根：

```powershell
& D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe tests/support/job_migration_readiness_rehearsal.py --output .validation/job-migration-readiness/ma-review-01
```

输出目录必须不存在。最终已执行私有演练目录：`D:/project/pan-worktrees/job-migration-readiness-20261029/.validation/job-migration-readiness/verified-c89866f9`。离线 bundle 路径为其 `publication-not-yet-created/bundle.json`；当前包/每份源/崩溃孤立文件 hashes 已列于机器证据。私有合成包和 context 不提交 Git；提交的是脱敏合成记录处置、统计、命令、hash。

MA 下一步可审阅四个脚本的 22 行新增/1 行替换及专属夹具，再执行上述命令独立核验；随后与 JobTA quoted-shell 修复分别整合。此包未触发 CI；没有阻塞等待 JobTA，也没有把其 serializer 原红变绿。

必须保留的 r14 原红：`tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup`，基线证据 `evidence/job-retirement-rev14-20261029/product.xml`（85 pass/1 fail）、`shell-development.log`、`docs/reports/JOB_RETIRE_REV14_PASSIVE_TEMPLATE_20261029.md`；本批没有重跑、修改或接受它。

未验：真实 profile 数据/授权/grants、当前 provider release 可用性、connected cohort 全量盘点、所有未来 occurrence shadow、pending/unknown 效果与通知 ack 实际结算、真实 owner fence/排空/激活/旧 executable 清零、真实迁移回滚、POSIX 实机/远端 CI、部署/人工验收。离线包成功不取消这些独立门禁。main=`9b4506c94ac67a39093961b218b2f01d387d1049`、practical=`86e421555f366133b25a4069e8575c0a1310ea60`、冻结平台=`a6cc7f19f253d236c94ca141558888a44daa7077` 未推进；没有 push、服务、真实账户/data/profile、8767/8768 操作。

收尾只读核对：原 `xplat-worker-capability-20261027` clean；原 `xplat-minimal-release-20261029` 的 `evidence/cp-x3-minimal-20261029/{pipe-worker,preexisting-profile}.json` 有 dirty。本批没有写入/还原这两份文件，也不将其纳入新候选；此处只记录观察，不推断改动归属。原 Job/平台树均未 reset、切换分支或合入本批。
