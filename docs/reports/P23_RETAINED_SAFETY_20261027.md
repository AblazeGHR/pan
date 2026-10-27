# P23 retained-safety 后继验证与最小修复

日期：2026-10-27。工作树：`D:/project/pan-worktrees/job-p23-retained-safety-20261027`。
分支：`r6/p23-retained-safety-20261027`。
固定基线：`bdd4c778d55daff58529635b2adc464c03a1393c`；它只是尚未整体 MA 验收的
P21 候选接口基线，**不表示已合入**。任务给定集成树仍为 `bac726cf`，本 TA 未推进它。
源码/测试/证据候选提交：`38083c99d77cb800bb789ff8b952fa26d6a30b27`。
本报告及证据文本规范化另作普通提交；最终 clean 交付 SHA 以本 Session 最终答复为准。

**结论：executor 保留安全能力已补最小实现并经 Windows 真树验证；原 late-child 节点的
canonical 逻辑 retire 后继已通过；两条完整 HTTP 后继仍阻塞于共享 Run cancel 接线，不能宣布
3 条 retained-safety-gap 全关闭。** 原测试/断言和 R6-I 原红证据未删、未 skip、未弱化。

## 逐 node 映射

| 原 node | 精确后继（路径相对 tests/） | 本轮结果与边界 |
|---|---|---|
| `test_job_delete_safety.py::test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent` | `test_job_retained_safety_canonical.py::test_terminal_child_writeback_does_not_recreate_retired_parent_successor` | 后继通过。真实独立进程产生 terminal receipt 后暂缓投递；原 Job retire，再投递真实 terminal，原 ID/定义保持 retired 且字节字段不变、无替代 parent、新 Run 准入 409、业务效果和清理各一次。原节点因 retired `_reconcile_scheduled_processes` 仍红。canonical 保留历史 tombstone，不声称物理删除 parent 记录 |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[False]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[False]`；executor 对应 `test_job_retained_safety_owned.py::test_real_executor_cleanup_http[False]` | executor 通过；完整后继红在 canonical cancel 未向原 executor 请求 cleanup。测试显式诊断 fallback 调用真实独立 cleanup HTTP 完成清理，**不把 fallback 算公共入口验收** |
| `test_job_delete_http_owned.py::test_real_owned_tree_delete_http[True]` | `test_job_retained_safety_canonical.py::test_real_owned_tree_delete_http_successor[True]`；executor 对应 `test_job_retained_safety_owned.py::test_real_executor_cleanup_http[True]` | executor 首次 unknown 拒绝确认、冷进程查证和明确复查通过；完整后继同样红在共享 cancel 未接线。公共 unknown 拒绝物理删除→查证→确认的完整协议仍需 MA 接管验证 |

可直接核查的机器结果见 [validation.json](../../evidence/p23-retained-safety/validation.json)。

## 改动与接口

只改 4 个授权产品文件：`packages/jobs/independent_executor.py`、
`independent_execution_runner.py`、`independent_service.py`、`independent_client.py`。
新增 3 个 `test_job_retained_safety_*.py`，未改两个原测试文件。

- 新增内部 `POST /executions/{originalEffectId}/cleanup`，body 只允许完整原 `attempt`。
  API 不接受 caller 自报 proof/PID/birth；沿用本地 Host/Origin/content-type 防护。原 effect 不存在
  404，旧 Attempt/lease/epoch/generation 或无原 owned handle 409，`processOwner` 不支持显式 501。
  `IndependentExecutor.cleanup_execution(*, attempt, effect_key)` 提供共享层可调用的 async port，
  并复核 Job/effect/Attempt 全 fence。原 pre-effect cancel 语义未变。
- execution document 新增 `cleanupRequest={revision,status,proof?}` 和 `cleanupHistory`，不新增
  SQLite 表、不改 canonical Job/Run/Step/Attempt/schema；status 为 requested/unknown/confirmed。
  HTTP 200 只是返回状态，不把 requested 或 unknown 当成功。请求和确认分开持久化。
- 整树终止只由持有原 ProcessOwner/Job handle 的 canonical supervisor 执行。先核验原 PID/birth，
  Windows 使用原始 FILETIME，再沿现有 owned-tree 算法终止、等待根进程，持久保存身份/根退出/
  owned 成员/remaining 证明。已证明的 tree 结果在后续 terminal 阶段复用，不再次 kill。
- 首次 probe unknown 时不执行 kill；真实终止已发生而回执丢失时保持 unknown 并保留原 handle，
  不因根进程后来退出或普通恢复而自动重复清理。只有下一次明确请求才增加 revision、复查。
  已结束但仍由保留句柄识别的同一根进程允许 native dead+ours=true 查证；unknown/reused 拒绝。
  supervisor 丢失时不回落到 root-only recovery 冒充树证明。杀掉业务进程的效果为 rejected，非 succeeded。

**canonical DELETE 保持逻辑 retire。** 没有将其修改成隐式物理 purge，也没有要求它仅因
运行中就必须返回物理删除的成功/失败语义。先显式清理原执行资源、再 retire 的后继已真实走到；
公共 cancel 发起这一清理的共享入口仍缺。早期 `canonical-first` 实验错误要求逻辑 retire 返回 409，
该探索断言已纠正，原日志/XML 仍留存，不算验收依据。

## 真树证据

测试使用自建随机 loopback 端口、临时根和自己的 supervisor/parent/child 进程。测试启动器运行
生产 `independent_execution_runner.run`，其 spawn/wait/verify/kill_tree 均走真实 Windows backend。
只注入两种不确定性：identity probe denied、真实 tree kill 后确认回执丢失。没有 mock 活树、终止、
成功 cleanup proof 或 terminal receipt。canonical 测试的 ExecutorPort 等待真实独立 terminal 并可延迟
投递；这证明真实迟到回调，不代表默认 execution_pending 的恢复接线已完成。

- [普通清理](../../evidence/p23-retained-safety/backend-unknown-false.json)：三类原进程身份可查证，
  native owned tree 清空，effect=1、kill=1，原 fence 重查和重复准入不重发。
- [unknown 后复查](../../evidence/p23-retained-safety/backend-unknown-true.json)：首次 denied 不确认也
  不 kill，冷解释器 recovery exit 0，去掉故障后 GET 仍 unknown，显式 revision 2 才确认；effect=1、kill=1。
- [实际已终止但确认丢失](../../evidence/p23-retained-safety/backend-partial-ack.json)：根/子已真退出，
  supervisor 保留句柄；没有自动第二次 kill，明确复查后才确认。两次显式清理授权、业务 effect 仍为 1。
- [迟到 terminal](../../evidence/p23-retained-safety/canonical-late-terminal.json)：原 parent retired 不变，
  原 Run completed，无替代对象，无重复效果；新准入明确拒绝。
- 两个 [HTTP False](../../evidence/p23-retained-safety/canonical-delete-unknown-false.json) /
  [HTTP True](../../evidence/p23-retained-safety/canonical-delete-unknown-true.json) 工件均明确记录
  `canonicalCleanupRequested=false`；`physicallyCleanBeforeRetire=true` 来自真实内部清理，不能掩盖入口缺失。

PID 复用负控使用**真实活 PID + 故意不相等的 FILETIME birth**，native verify.ours=false，
recover 返回 refused_identity，原进程仍活。没有强迫操作系统实际循环复用 PID；不扩大此证据的含义。
所有真树工件最终记录 ownedProcessesRemaining=[]、supervisor 已 reap、随机端口已释放。

## 执行与统计

全部 pytest 命令均通过 `python scripts/run_tests_isolated.py -- python -m pytest ...`。
每次完整 argv/cwd/exit/逐 node 结果见 validation；各 label 对应同目录 `.xml/.log`。

| label | tests | passed | failed | skipped | exit |
|---|---:|---:|---:|---:|---:|
| original-red | 3 | 0 | 3 | 0 | 1 |
| backend-first | 2 | 2 | 0 | 0 | 0 |
| canonical-first（探索，含已纠正的 retire 假设） | 3 | 1 | 2 | 0 | 1 |
| canonical-final | 3 | 1 | 2 | 0 | 1 |
| regression-first（不存在文件名，未执行测试） | 0 | 0 | 0 | 0 | 4 |
| regression-final | 142 | 141 | 1 | 0 | 1 |
| matrix-final | 188 | 165 | 4 | 19 | 1 |
| delta-final | 11 | 11 | 0 | 0 | 0 |
| cleanup-final | 11 | 11 | 0 | 0 | 0 |
| acceptance-final（最终源码扩大矩阵） | 189 | 167 | 3 | 19 | 1 |

最终扩大命令：

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_job_retained_safety_protocol.py tests/test_job_retained_safety_owned.py tests/test_job_retained_safety_canonical.py tests/test_job_final_independent_execution.py tests/test_independent_job_service.py tests/test_job_delete_safety.py tests/test_job_cleanup_failures.py tests/test_job_delete_main_combination.py tests/test_platform_process_contract.py -q --tb=short --junitxml=evidence/p23-retained-safety/acceptance-final.xml
```

最终新增 P23 集合：**14 nodes = 12 passed / 2 failed / 0 skipped**。
`acceptance-final` 加未改原 HTTP 两红，按 node 去重：**191 = 167 passed / 5 failed / 19 skipped**。
五条红＝原 3 红＋共享 cancel 接线后继 2 红；不是 5 个新产品回归。
19 skip＝现有 POSIX ProcessOwner 契约 18 条及 Windows-constructor-on-POSIX 门禁 1 条；理由在 JUnit。

`matrix-final` 另一次旧 `test_cancel_with_wrong_runner_identity_does_not_touch_job` 出现
`WinError 10053`，保留原始失败。`delta-final` 同 node 及最终 acceptance 同 node 均通过；没有压制错误、
放松断言或把该次失败冒称已实测 baseline-equivalent。`regression-first` 误写不存在的
`tests/test_independent_job_platform.py`，exit 4，也完整保留。

Windows `import packages.web.server` 经隔离 wrapper exit 0；未做 React 修改/构建。
证据 raw bytes 在 [raw-output.zip](../../evidence/p23-retained-safety/raw-output.zip)；审阅文本仅规范化
行尾空白/换行，前后 SHA256 在 artifact-whitespace.json，原 raw SHA 在 validation。
提交走普通 hook；最终范围 `git diff --check` 通过。

## MA 接管与未验层

共享入口补丁要求已精确写入
[MA_SHARED_ENTRY_REQUIREMENTS.md](../../evidence/p23-retained-safety/MA_SHARED_ENTRY_REQUIREMENTS.md)：
`FinalService.control_run(cancel)` 在事务提交后调用原 effect 的 cleanup port；`_drive_run` 不能仅凭
cancelling 宣告已取消；默认 pending adapter 的 uncertain Attempt 必须原 identity 查询/受 fence 的
reconcile，不能新建/重发；pending/unknown 保持可查，明确复查才重试清理；完整 proof 与 supervisor
退出后才完成资源清理；最后再宣告 process Action cancel capability。API 授权/CAS/requestKey 不放松，
DELETE retire 与物理资源清理继续分开。公共 unknown 拒绝/查证/确认门禁仍由 MA 补齐。

本 TA 没有直接改 shared final_service/final_api/ledger/coordinator/background_jobs/conftest/support/React；
三个 scheduler host 全部未改未删，P24 pins/history/bridge 范围未动。15 个关键受保护文件的
Git blob 与固定基线一致，R6-I 证据目录亦无差异。原 HTTP 红测试本身写入的两个旧 audit 工件仅在
本轮复现中产生，已将本轮输出复制至独占 evidence 后恢复原字节；未交付外范围改动。

[独立 capability 矩阵](../../evidence/p23-retained-safety/CAPABILITY_MATRIX.md) 记录已有 processOwner
能力的本次扩展。Windows 实测；Linux/macOS 执行、三平台远端 CI、完整仓库 suite、默认 pending
恢复、完整公共 physical-delete 流程、浏览器、真实 provider、真实数据迁移、部署与人工验收均未证明。
未另派 TA，未变更模型。未 merge/push、未推进 main/practical、未启停既有服务、未操作 8767/8768，
未读取或写入 51/60 集合。仅清理本测试创建且有归属证明的临时进程/端口。
