# Receipted idle-successor wake repair

本批仅修正 restart 后 idle 后继的同任务恢复契约，候选等待 MA 独立复审。

- 工作树：`D:/project/pan-worktrees/event-trigger-core-20261022`
- 分支：`feature/event-trigger-core-20261022`；开始时 clean。
- 基线：`b1f1100b5fc6df0e46bbd60f43cc6bc3b5ba2490`
- **最终精确受测源码：`861da58744196eaab19e774d488ab8a3e7b64b7e`**
- 后续提交仅保存本目录证据，不改变受测源码。

## 修前证明

在 b1f1100b 上仅新增 `test_normal_restart_idle_resumes_frozen_task_exactly_once`：
fake restart 生成严格后继 generation、新 PID/出生身份、目标账号和同一未完成 task/execution；
回执 completed 且 inventory 为正常 idle。旧代码没有规划继续消息，断言 `0 == 1` 失败。
[before-fix.xml](before-fix.xml) 为1 failed，命令退出1；完整 argv 在 [failure-index.json](failure-index.json)。

首轮修复后的 `first-fix.xml` 为85项中2 failed：上一批两个参数化用例仍要求
receipted idle 必须拒绝，与本次 MA 确定的新契约冲突。保留原测试 ID，改为验证恰好一次成功及
原 task 身份一致；其余负例保留，新增无回执/错误身份反例。未增加 skip/xfail 或削弱其他断言。
`regression.xml` 的142项是开发期辅助结果，不计入最终冻结验证。

## 契约与覆盖矩阵

| 要求 | 实现 / 验证 |
| --- | --- |
| 原目标资格不扩大 | `original_eligible` 只接受冻结时显式 TA/MA、running、存活身份和未完成 taskId/executionSeq；原 idle、无任务、完成、unknown role 均不晋升 |
| idle 后继可继续 | `recovery_eligible` 允许 running/idle，但必须解析本地 ledger 中已确认完成的 restart；严格同 epoch/session/adapter/role/task/execution，generation 更新、出生身份更新，PID/出生/generation/Worker/account 与 fresh inventory 一致 |
| 可信回执绑定 | TA wake 新增 `arguments.restartCommandId`，原始 restart/当前 successor/当前账号均匹配，且同 case、policy epoch、accountConfigRevision；不能单靠 idle 或更高 generation 选目标 |
| 规划和发送双复核 | `progress_restarts` 查验回执后规划；preflight 再查 fresh inventory，并在 inventory await 后重读 ledger 查验回执，避免使用派发起始时旧快照 |
| 无可信回执不能放宽 | 缺引用/缺 receipt、uncertain、错误 case/source/task、旧 epoch/config、旧代和出生身份、错误账号等 before_plan/before_send 参数组全部拒绝 |
| 当前执行变化仍拒绝 | 完成、missing task、换 task/execution、旧/其他进程、变 generation、offline、unknown/stale inventory、换 owner/账号等双阶段参数组全部拒绝 |
| 单次继续与 unknown | 正例发送一次，recover/重复 dispatch 不重复；未知 wake 回执保持 uncertain，不自动重投；manual/close/disabled 仍阻止尚未派发的继续 |
| MA 共用函数不扩大 | 无 restart 的 `wake_eligible` 只准同一个 running 进程；原 idle MA 不能建监控。提醒 Job 只能经其自身已确认 restart_ma 关联同任务 idle 后继，规划和发送复核；TA receipt 不能充当 MA receipt |
| 永久禁用保持 | 原 disabledAccountIds 配置与 API 未变；本轮 policy 回归及真实隔离 HTTP 重验持久化、权限、重启与旧 token 失效 |
| 业务入口保持 | 定向共存验证覆盖 Action high-risk、query 恢复冻结、disable/claim、close/kick、插件 generation 门禁；未新增 registry/轮询/生产 adapter |

精确函数/command 契约见 [quota-policy-v0.3-safety.md](../../docs/design/quota-policy-v0.3-safety.md)。
旧待执行 TA wake 若没有 restartCommandId，不会根据当前 Worker 猜测或补齐回执。
单纯同进程 idle 也不满足无重启证明的 MA 提醒/继续资格。

## 最终验证

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/verify_quota_policy_idle.py
```

所有 pytest 经隔离 wrapper；完整命令、精确源码、环境、耗时和退出码见
[verification.json](verification.json)。

| 检查 | 结果 | 退出码 / 证据 |
| --- | --- | --- |
| 新 idle 正反例及原 quota/safety 相关回归 | 143 passed，0 skipped | 0；[JUnit](final-policy.xml)、[日志](final-policy.log) |
| 定向共存检查 | 5 passed，0 skipped | 0；[JUnit](final-compat.xml)、[日志](final-compat.log) |
| 真实隔离 HTTP，default adapter unavailable | 1 passed，0 skipped | 0；[JUnit](final-http.xml)、[日志](final-http.log) |
| compile / diff check | 通过 | 均0；[compile](compile.log)、[diff](diff-check.log) |

**最终149 passed，0 failed/errors/skipped**。没有合并或重复计入 MA 前序验收计数。
Windows UTF8 mode0、stdout GBK/locale cp936，未设置 PYTHONUTF8/PYTHONIOENCODING；
文本证据明确 UTF-8，日志 decode/换行明确。XML 入库仅规范行末空白，测试/失败属性保留。

HTTP 复用了现有自有服务测试；其旧 evidence 文件运行后已按原 bytes 恢复，本轮结果单独保存。
[HTTP evidence](http-evidence.json)：端口50079、自有初始PID10056/重启PID4200，
`ownedServerStopped=true`、`portReleased=true`；未创建真实 Worker。

## 未完成边界与停止点

- 本批效果只在内存 fake adapter 中；真实账号切换、模型调用、Worker 重启/唤醒次数均0。
  生产 adapter 仍 unavailable，未接入真实 provider 或改变现有服务。
- core 的两次复核与 ledger claim 不等于外部效果跨进程原子；未来 adapter 仍须在实际效果入口
  CAS 校验原 task、receipt、当前 owner/process/account/config，并保留 Pause/Illegal 等业务门禁。
- `disabledAccountIds` 独立持久化不变；用户三号账号仍只在前一批
  `artifacts/quota-policy-safety/disabled-accounts.input.json`，未写产品默认或真实 profile。
- 既有 producer/inventory 生产接线、unknown reconciliation、易失 ingress 等前序未完成边界不变。
- 没有移动其他树/main/practical/集成树，未 push/release，未操作8767/8768或真实数据，未子派发。
  提交 clean 候选后停止，等待 MA 复审；整体人工验收仍待用户。
