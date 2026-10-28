# CP-X1 lifecycle v2：canonical 准备、实际 owned host 与剩余红项

代码候选 `ec2a61e162c0a680aaec3f906397480db2c2e64d`，父候选
`dc393e9557adec86ae54bf186a221b4fbbb38206`。本批按 MA 的产品接线与
`ExecutionStore.reject_prelaunch` 单符号补充授权修改产品；未修改 launcher、平台层、
Worker 实现、原测试、共享 conftest、CI 或其他 TA 路径。

## 实现与安全边界

`api_system_main_lifecycle` 复用原策略解析和 Session 快照，先在原 canonical
`ExecutionStore` 持久保存冻结计划，再关 Worker gate，按快照执行 offline/preserve
的 `shutdown_all`，完成后才交给 detached supervisor。进程内重复、冲突选项和
exit/restart 并发返回 busy 与原 effect；同一 PID/birth 的内存状态丢失后，两个操作
各按确定性身份查询原 store，而不是重放准备或创建第二个 effect。

`ProtectedLifecycleExecutor.execute` 仅在 `created=True` 的本次调用内设置准备 token。
准备成功先原子消费 token 并写 `launchAttempted`，再调用 launch；已有 intent 只查原回执。
准备失败的 `reject_prelaunch` 在同一事务核验原 Attempt fence、token、claim 和 launch
标记，用原 outbox 保存 `unknown`、原错误、`mainEffectStarted:false`、
`preparationMayHaveChanged:true` 和未确认 cleanup。部分 Worker 准备不能视为无副作用，
不自动重试或重开 gate；launch 不明保持原 `launch_unconfirmed`。没有恢复 legacy writer。

测试专属只读 adapter 以 `protected_plan` 的身份，经 `ExecutionHost` / `ExecutionStore.get`
读取原库，核对 Job、Attempt、effect、冻结计划及 digest；缺库不创建，查询不回填选项。
`lifecycle_transition_fixture` 只把真实 canonical 冻结选项映射给旧 observation，禁用 detached
launch；因此这些单元证据不能冒充真实 supervisor 运行。

## 本机验证：Windows，Python 3.14.5

均使用 `D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py --`
和相同解释器执行；下列计数互不相加，所有所列集合 skip 为 0。

| 集合 | 结果 | 证据 |
|---|---|---|
| 新增准备/fence/controller 及既有 P5B 契约，`-m pytest tests/test_lifecycle_canonical_preparation.py tests/test_lifecycle_prelaunch_fence.py tests/test_lifecycle_followthrough.py tests/test_p5b_lifecycle_action.py -q -ra` | 28 pass，exit 0 | `preparation-regression-28.xml/.log` |
| 既有独立执行器，`-m pytest tests/test_job_final_independent_execution.py -q -ra -k "fe05 or missing_or_wrong_supervisor_claim or ambiguous_launch or lost_or_unclaimed_supervisor or reopened_store_rejects_stale_phase or lifecycle_offline_lookup or phase_transaction_prevents_duplicate"` | 36 pass，exit 0 | `existing-fences-final.xml/.log` |
| 原 15 transition 节点，经 `-p tests.support.lifecycle_transition_fixture`，未改断言 | 10 pass、5 fail，exit 1 | `original-15-final.xml/.log`、`node-outcomes.json` |
| 原 `safe-api`，`-p tests.support.lifecycle_host_diagnostic --cpx1-genuine-main` | 1 pass，exit 0，代码 `ec2a61e1` | `host-genuine-safe-final.xml/.log`、`owned-host-genuine/safe-api.json` |
| 另 6 个原 host 节点：`preserve-api`、`q-config`、`q-menu`、`q-menu-2`、`q-menu-3`、`q-triple`，同一 genuine-main 选项 | 6 pass，exit 0，Job `job_d8b437eb0f5ec2691d384419` | `host-genuine-six.xml/.log`、各自 JSON |

15 节点实际命令选择器是：

```text
-p tests.support.lifecycle_transition_fixture
tests/test_main_service_exit.py::test_exit_job_records_choice_and_exact_pre_exit_running_snapshot
tests/test_main_service_exit.py::test_exit_ask_requires_explicit_choice_before_job_or_worker_gate
tests/test_main_service_exit.py::test_exit_policy_is_frozen_in_job_and_overrides_legacy_payload
tests/test_main_service_exit.py::test_exit_schedules_worker_shutdown_and_python_supervisor
tests/test_main_service_exit.py::test_duplicate_exit_is_rejected_without_second_request
tests/test_main_service_restart.py::test_restart_returns_scheduled_before_python_supervisor_finishes
tests/test_main_service_restart.py::test_restart_ask_requires_explicit_choice_before_any_side_effect
tests/test_main_service_restart.py::test_restart_freezes_configured_policy_and_running_snapshot
tests/test_main_service_restart.py::test_persisted_duplicate_restart_is_rejected_without_spawn
```

原 15 中 ask 两项与四种策略各两项通过。5 个剩余红项为 exit 的
`test_exit_job_records_choice_and_exact_pre_exit_running_snapshot`、
`test_exit_schedules_worker_shutdown_and_python_supervisor`、
`test_duplicate_exit_is_rejected_without_second_request`，restart 的
`test_restart_returns_scheduled_before_python_supervisor_finishes`、
`test_persisted_duplicate_restart_is_rejected_without_spawn`。它们仍要求旧
`pan-main-exit`/`_perform_main_restart` task、legacy registry 或旧 `_main_*_pending`
标志；这些原断言没有删除，也不能靠把 canonical 回执投影成旧 writer 来假装通过。
其中 launch 被测试夹具禁用，旧 detached-command 断言的红结果应按这个条件解释；新
canonical 的 gate、shutdown、单次 launch 顺序与冲突选项由新增测试单独验证。

真实 host 夹具把**仓库原样 `main.py`**复制到专属临时根，在该根启动，并通过
test-only `sitecustomize` 隔离 config、Session、日志与 deterministic CLI；没有改身份
检查。每项证据记录 PID/birth、listener owner、请求/effect、真实内部 shutdown 响应、
`should_exit`、原 receipt、退出码和清理。7 项原测试均保留 25 秒和原 offline/Illegal、
重启及清理断言；成功 receipt 不等于真实用户数据或部署验收。
六项 Job 在 `f1c63ee8d7a6cf4a50b878c66f0bbfcc55ba9cc6` 产品源码上运行；之后
`ec2a61e1` 仅新增已有 intent 的内存丢失 fence，并重跑真实 `safe-api`、对应新增回归。
六项未在该后续 SHA 上重新运行，不把它们冒充精确最终 SHA 的结果。

## 原始红证据和仍未闭合部分

修前七项原 host 在隔离入口不是 `main.py` 且 cwd 不属于 root 时均超时；原 effect
`identity_unverified`、`should_exit=false`，内部 shutdown 未观察到，最终测试清理释放端口。
该结果是 fixture 身份阻塞，不能归因于真实 main 的停机行为。原失败日志、JUnit 和每项
JSON 保留于 `host-safe-api.*`、`host-six-before.log`、`host-six.xml` 和 `owned-host/`。
真实 main 初次因 cwd 不在自有根仍红，`host-genuine-safe-first.*` 和
`owned-host-genuine/safe-api-first.json` 保留；随后修正 cwd，未绕过身份检查。

两个新 canonical 进程树测试分别在 submit 返回前、后杀死经过 PID/birth 验证的真实
controller，保留原 Attempt/effect 和活着的 supervisor/子孙，显式请求原 owner 清理，
验证树无 survivors、supervisor 退出及唯一 outbox；结果本身为 `rejected`，不称取消成功。
它们**不是**原 3 个节点的严格等价后继：旧 Job cancel/desktop 断言，以及 base/venv 下
controller 在 supervisor PID 发布之前崩溃的时点仍未覆盖。具体映射见 `node-outcomes.json`。
另 8 个旧 legacy runner/tree 红节点不属于这三个后继，也保持原红，不宣称迁移完成。

对上批 `CP_X1_LIFECYCLE_FOLLOWTHROUGH_20261029.md` 的修正：JUnit 的分组是 exit
5 pass/8 fail、restart 7/7、legal HTTP 13/7、Job prevention 11/11；总计仍为 36/33，
原报告各组的 6/7、10/12 是误计。原 69 节点 Job 启动后工作树曾前进，不能把该次
结果当作不可变 `4be0f6c7` 的精确源码证明；原日志和 XML 保留，未重写成绿。
本轮修前 transition 隔离 probe 禁用 launch，输出是 admitted/null，不是服务成功证据；
最初 `followthrough.*` 三项实验包含合成 shutdown 端点，不是实际生命周期验收。

JUnit 主机名已统一脱敏为 `windows-local`，失败正文保留；其余证据路径均在
`evidence/cp-x1-lifecycle-v2-20261029/`。无新增 skip/xfail、断言降低、push、远端 CI、
main/practical 更新、真实账号/profile/data、部署或既有服务/8767/8768 操作。
Linux/macOS、最终组合 SHA 的远端 CI 和真实用户环境均未验证；MA 负责后续整合与授权运行。
