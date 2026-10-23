# Quota policy safety repair candidate

本批为 TA 返修候选，等待 MA 独立复审，不等同于上线或真人验收。

- 原树：`D:/project/pan-worktrees/event-trigger-core-20261022`
- 分支：`feature/event-trigger-core-20261022`
- 精确基线：`115e7db2c298ed29b5c0904001689138cbe25eb3`，开始时 clean。
- 首轮源码：`edafe0a9c23d9880629f75d53d3ff23b6168e59f`。
- **最终精确受测源码：`d4eabefeb1ee776201c46b392f8bee94ca250921`**。
- 之后的提交仅保存本目录证据；产品、契约、测试均在最终受测源码内。

## 修复与验收矩阵

| 缺口 / 边界 | 本批行为 | 确定性证据 |
| --- | --- | --- |
| 永久禁用与 capacity 排除混用 | 持久 `disabledAccountIds` / `accountConfigRevision`，独立于 `excluded`；默认空，不含用户账号；恢复、重置及模式切换不解除 | `test_disabled_persistent_separate_from_capacity_exclusion_and_recovery` |
| 有余额或最近 reset 的禁用账号被选中 | rank、无候选 reset 选择、switch preflight、原子 claim 都排除 disabled；禁用当前账号不能发继续重试，可选择其他允许账号 | `test_disabled_with_quota_never_selected`、`test_disabled_nearest_reset_omitted_and_all_disabled_no_reset`、`test_disabled_current_capacity_skips_retry_and_switches_off_account` |
| 配置并发、非法配置和写盘失败 | 严格字段/类型/数量/唯一性校验，CAS revision；配置变化增加 epoch、取消未 claim 计划并使旧 Job 失效；写失败不发布内存中的更改 | disabled 校验参数组、并发更新、持久化失败测试 |
| 改禁用配置恰逢 switch preflight | 屏障停在 inventory await，修改配置后释放；无效果，迟到检查不覆盖已取消状态 | `test_disable_during_switch_preflight_cannot_dispatch_or_override_cancel` |
| 缺 owner / process / alive / fresh 证据 | inventory v2：runtimeEpoch、fresh observedAt、PID+birthToken、alive、显式 role、task/execution；缺失或过期为 unknown | `test_observation_requires_verified_runtime_process_identity` 参数组 |
| restart 变成启动 offline | 重新查验原冻结 epoch/Session/Worker/generation/进程出生身份；offline、无匹配进程或换代只缩小集合，不调用 restart | `test_restart_never_starts_offline_or_retargets_new_identity` 参数组 |
| restart 后继代猜测 | 重启确认必须含 successor；要求同 epoch/Session、更新 generation、新出生身份、目标账号及 fresh inventory 一致；不匹配 uncertain、不重试 | `test_unverified_restart_successor_uncertain_and_never_replayed` 参数组 |
| 旧 running 已完成仍唤醒 | 冻结时显式 TA+running+未完成任务；规划、发送前分别复核；完成/idle/offline/新任务/新 execution/新进程/新 generation/变更角色或账号均不唤醒 | `test_old_running_completed_before_wake_never_woken`、`test_wake_rechecks_current_task_process_role_and_successor` 双阶段参数组 |
| missing task 或名称推断角色 | taskId+executionSeq+unfinished 状态必须有据；MA/TA 只接受显式 role，不用名称补齐；missing 记录 blocked/诊断 | `test_missing_frozen_unfinished_task_or_explicit_role_never_guessed` 参数组 |
| MA 提醒 Job 改投新任务 | Job 保存 `wakeTarget`；不采用阈值时的新任务；仅可沿已持久确认的 restart_ma successor 绑定同一未完成任务，再复核当前进程 | monitor 新任务负控、verified successor 正控、missing MA task 三项测试 |
| 旧账本无身份 | 旧 ready 命令 blocked；旧 dispatching uncertain；缺 v2 身份的提醒 Job disabled/blocked，不重新猜测任务 | `test_legacy_commands_jobs_blocked_without_identity_upgrade` |
| manual / close / unknown | 在身份检查 await 处切换 manual/close 后无效果；unknown inventory 或 receipt 不重放；保留原关闭收敛保护 | manual/close 参数组、identity unknown 测试及旧 quota 回归 |
| HTTP 管理权限 | 用户 Bearer + expectedRevision；401/400/409 明确；没有 adapter/凭据/回执注入入口 | 最终真实隔离 HTTP，包含自有进程重启后的持久性和旧 token 失效 |
| 原契约共存 | 原 Group/high-risk、JobTemplate/取消、query 冻结、插件 generation 和 queued 恢复、Engine 并发检查保持 | `final-compat.xml` 16 项，未机械重跑前序70/154或全仓 |

## API 与当前用户禁用三号账号的配置输入

配置路径是**所选 profile 下的** `automation/quota-policy.json`，新增字段不是全局账号默认值。
管理接口与 host adapter v2 契约见 [quota-policy-v0.3-safety.md](../../docs/design/quota-policy-v0.3-safety.md)。

```text
GET /api/automation/quota-policy
PUT /api/automation/quota-policy/disabled-accounts
Authorization: Bearer <user-token>
Body: {disabledAccountIds: string[], expectedRevision: integer}
```

用户指定的三号账号 `c3e2a777-2ca3-40d7-871b-e0d42daeaa50` 已作为明确部署输入保存于
[disabled-accounts.input.json](disabled-accounts.input.json)。该文件只含可配置禁用名单，
**没有写入真实 profile，也没有把该 ID 放入 packages/tests 或产品默认值**。

下一阶段接线/部署前，由授权用户登录目标实例，GET 取得 revision，将 input 的
disabledAccountIds 与该整数 expectedRevision 组成上述 PUT，再 GET 核对名单与
accountConfigRevision；这不会自动开启 automatic。输入是完整替换名单，若目标实例
已经有其他永久禁用账号，应先合并保留它们再提交。不可直接覆盖正在运行的 ledger。
新名单变化会取消旧计划与监控；解除禁用也只能显式用户写，不重放已去重的旧事件。

## 最终源码验证

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_quota_policy_safety.py
```

完整 argv、退出码、源码 SHA、耗时与环境见 [verification.json](verification.json)。
所有 pytest 均通过 `scripts/run_tests_isolated.py -- ...`，使用只读既有依赖、自有临时 profile/端口。

| 检查 | 最终结果 | 退出码 / 证据 |
| --- | --- | --- |
| quota 安全/原策略/parser/两项 Worker 日志回归 | 96 passed，0 skipped | 0；[JUnit](final-policy.xml)、[日志](final-policy.log) |
| 定向组合兼容 | 16 passed，0 skipped | 0；[JUnit](final-compat.xml)、[日志](final-compat.log) |
| 真实隔离 HTTP | 1 passed，0 skipped | 0；[JUnit](final-http.xml)、[日志](final-http.log) |
| 编译 / diff check | 通过 | 均0；[compile](compile.log)、[diff](diff-check.log) |

**合计113 passed，0 failed/errors/skipped**，只计最终 d4eabefe 的结果。
Windows：UTF8 mode0、stdout GBK、locale cp936；没有 PYTHONUTF8 / PYTHONIOENCODING 覆盖。
证据文本显式 UTF-8，捕获子进程 bytes 显式 decode，日志换行规范化后入库。

## 修前失败与辅助证据

- `before-fix.xml`：在115e7db2上仅新增两个回归，2 failed，分别证明禁用账号仍被选中、完成任务仍被唤醒；退出1。
- `ma-task-before-fix.xml`：在edafe0a9上仅新增 MA Job 任务更换回归，1 failed，证明会改投新 MA 任务；退出1。
- [failure-index.json](failure-index.json) 保留两个精确命令、源码 SHA 和证据说明。
- `pre-ma-task-fix/` 保存首轮冻结 edafe0a9 的108项及 manifest，不叠加计数。
- `first-fix.xml`、`regression.xml`、`identity-regression.xml`、`http-preflight.xml`、`ma-task-after-fix.xml` 是未冻结开发期辅助记录，不替代最终 SHA 证据。

## 迁移、生产接线与剩余边界

1. v1 ledger 只在内存读取时补空 disabled 与0 revision；不读写真实 profile，不清历史排除或去重。
   无身份旧计划 blocked，dispatching uncertain；不会靠当前 Session 状态升级成新任务。
2. strict host runtimeEpoch 现在是策略消费的必要关联证据；provider payload 不能声明它。
   现有未接线的生产 producer 缺 owner epoch 时仍只作诊断，不自动恢复。下一阶段须由单一
   Pan owner/Worker/task 生命周期投射 epoch、进程出生信息及真实未完成任务，不能新造 registry。
3. 查询 adapter 应复用 cc-switch 现有桥接；当前 provider quota 不等于已验证的 managed
   account 五小时/周额度。若桥接欠缺这些字段，扩原契约，unknown 时停下，不读其 DB 或另做 OAuth。
4. effects adapter 必须在**真实业务效果 admission** 校验传入 accountConfigRevision、禁用名单、
   owner epoch、进程和任务 CAS，并保留 Pause/Illegal/model/queue 门禁；核心的 preflight+ledger claim
   不声称与外部写入跨进程原子。没有长全局 gate 跨外部 I/O。
5. 生产 adapter 仍 unavailable；真实换号、CBC 模型切换、Worker 重启/唤醒及完整 MA 交接重入循环
   没有接通或执行。query Job 与 ledger 仍是分别原子文件，沿用确定性 Job ID/epoch 恢复策略。
   RuntimeIssue observer 提交前易失窗口、人工 uncertain reconciliation 等上一批边界仍保留。
6. MA/task 变化后，不允许重复创建同一 Job ID 偷换 wakeTarget；要由用户确认新 policy epoch。
   阈值已达到与唤醒已送达是不同结果，缺目标时诊断为 blocked，不声称已恢复任务。

## 清理与停止点

最终 HTTP [证据](http-evidence.json)：自有端口63396，初始PID16316、重启后的自有PID37660；
`ownedServerStopped=true`、`portReleased=true`。HTTP 未创建 Worker；重启只针对本用例拥有的服务。
策略效果只在内存 fake adapter 上执行，**真实 provider/model/账号切换/Worker重启/唤醒次数均0**。
未访问8767/8768，未操作真实 data/Session/既有服务，未改 React/外部插件、依赖或其他工作树，未子派发。
未移动 main/practical/集成树，未 push/release。本批 clean 候选提交后停止等待 MA 复审；人工验收待用户。
