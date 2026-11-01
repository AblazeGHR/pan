# CP-X1 lifecycle strict successors

原分支基线 `a9d236c1c16159ca27697cfe090e3b7256c8c417`；本批代码
`10e6fd08fe009aeb8cd6c234e85acd865b9ad884`。未改 MA integration、平台层或原五个测试。
上一批的七项真实 host 证据、source SHA 差异和红证据仍保持原样。

## 真实非阻塞缺口与修复

在 a9，受控 `shutdown_all` 等待 Event 时，exit 和 restart 的路由响应也被阻塞：
`nonblocking-before.xml/.log` 中两项按原语义均红。现 `ProtectedLifecycleExecutor.execute`
先写原 canonical intent 和 preparation token，由 `api_system_main_lifecycle` 的 host 关 gate
后持有同一个 continuation task；task 等 Worker shutdown 完成、原子写
`launchAttempted` 后启动原 supervisor。`scheduled` 只代表持久接纳，不代表效果成功。
task 的结果、异常和取消均被观察；创建任务失败、部分 shutdown、取消以及不明 launch
保留原 Attempt/effect 的 unknown 回执，既不自动重放也不把副作用当作未发生。

`tests/test_lifecycle_strict_successors.py` 用 Event 精确证明响应在 Worker shutdown
仍被阻塞时到达、gate 已关、原 receipt 存在；同操作及跨操作请求返回原身份的 busy。
另两项调用真实 detached supervisor，原受测进程不是 Pan main，真实身份 gate 给出
`identity_unverified` rejected 且不停止该进程；不是模拟成功。持久准入失败、任务创建
失败、任务开始前取消、launch 期间取消、首请求准入期间并发均有专属回归。

## 五个旧 transition 节点与 canonical 后继

原五节点在 observation 夹具下本批重跑仍为 **0 pass / 5 fail**，见
`original-five-after.xml/.log`。夹具禁用 detached launch；不能用该组红结果推断真实
supervisor 未启动。没有删改或宣称原节点通过：

| 原节点 | 原断言与本批后继 | 剩余差异 |
|---|---|---|
| `test_exit_job_records_choice_and_exact_pre_exit_running_snapshot` | `test_freeze_before_gate_and_worker_shutdown_before_supervisor` 验证真实 canonical 原计划、排序后的 running IDs、choice 先于 gate；`test_scheduled_response_does_not_wait_for_worker_shutdown[exit]` 验证 held task/scheduled | 旧测试要求具体 task 名及 legacy registry；新事实源为原 ExecutionStore，旧节点仍红 |
| `test_exit_schedules_worker_shutdown_and_python_supervisor` | Event 验证 shutdown 完成前不 launch；`test_public_scheduled_response_precedes_real_detached_runner_completion[exit]` 运行真实 detached runner 并验证原 identity gate；真实 `safe-api` 验证真实 main 的 shutdown 与 offline receipt | 旧命令 `packages.core.main_lifecycle --supervise` 已退役，原命令断言不作为 canonical runner 的等价证据 |
| `test_duplicate_exit_is_rejected_without_second_request` | `test_scheduled_response_does_not_wait_for_worker_shutdown[exit]` 同时验证原 request/effect、同操作 busy；`test_lost_memory_still_fences_duplicate_or_cross_operation` 验证内存丢失仍不重放 | 旧测试人工设置 `_main_exit_pending`，不是 canonical intent；原节点仍红 |
| `test_restart_returns_scheduled_before_python_supervisor_finishes` | Event 证明返回早于 shutdown/launch、冻结选项先落库、gate 已关；真实 detached restart 在受控非 main 目标上由身份 gate 拒绝 | 旧 `_perform_main_restart` coroutine 的内部名称与原 registry 不再是事实源 |
| `test_persisted_duplicate_restart_is_rejected_without_spawn` | `test_duplicate_during_original_admission_waits_then_returns_original_busy` 和内存丢失测试验证一个 intent、原 effect 与一次 launch | 旧 `_main_restart_pending` 标志本身不是原 canonical intent，原节点仍红 |

这些是逐行为后继及明确缺口，不把旧内部实现断言宣布成已通过。五项仍需 MA 决定
旧接口兼容/退役和 CI 门禁迁移；不能通过恢复 legacy writer 或伪造 task 事实解决。

## 三个旧 controller 节点的风险分解

新 `test_lifecycle_controller_publication.py` 在真实 FinalService Job/Run 和公共 Run cancel
上，以真实 `launch_detached` bootstrap exit receipt 后、调用真正 runner `run/claim` 前的
测试握手阻塞；之后仍调用原生产 runner。base 和**实际命名 venv**各覆盖 claim 前与
业务三代子树运行后的 controller crash，共 **4 pass**。记录原 controller/runner PID 与
birth、bootstrap 终态、原 Attempt/effect、进程树、公共 cancel、原 owner cleanup proof、
supervisor 退出和效果仅一次。claim 前断言尚无业务子进程；原顺序是 claim 后才 spawn，
因此不制造“child 已运行但 supervisor 未发布”的旧窗口。claim 后崩溃则证明活着的原
owner 完成 Job cancel，无 survivors；不是合成 receipt，也不自动重复 launch。

| 原节点 | 已证明 | 仍 open |
|---|---|---|
| `test_controller_process_crash_keeps_owned_runner_then_restart_cancels[legacy]` | 四组中的 after-running 保留原身份、公共 canonical Job cancel 和三代清理 | 原隐藏专属 desktop 创建与 `desktop_gone` 尚未成立；legacy writer 不重启 |
| `test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication[base]` | base 真实 bootstrap 完成、claim 尚空且业务 child 尚不存在时杀 controller；原 runner 随后 claim 并可取消 | 旧 child-before-PID 时点在当前 claim-before-spawn 链不存在；原 desktop 消亡仍 open |
| 同节点 `[venv]` | 实际新建 `named-cpx1-venv`，运行时 prefix/executable 确认；同一 claim 前及运行后事实 | 同上；venv 启动链的原 desktop 消亡仍 open |

真实树的 desktop 均为 `Default`。`desktop-original-red.xml/.log` 通过
`-p tests.support.lifecycle_strict_options --cpx1-require-hidden-desktop`，对真实报告的
desktop 名执行原 `desktop_gone`，结果 **1 fail**。CP-X2 独占 Windows ProcessOwner
修复；组合后可对四个参数运行同一 option，不能让 Default 或虚构 desktop 通过。
旧 desktop 断言不在普通四项绿结果里。此前一次仅传环境变量的试验由于隔离 wrapper
移除 PAN 环境变量，实际没有启用断言，保留为 `desktop-option-not-applied.*`，不得当验收。

## 检查与边界

Windows，`D:/project/Pan-main/.venv/Scripts/python.exe` Python 3.14.5，均通过
`scripts/run_tests_isolated.py --`：

```text
-m pytest tests/test_lifecycle_strict_successors.py tests/test_lifecycle_controller_publication.py tests/test_lifecycle_canonical_preparation.py tests/test_lifecycle_prelaunch_fence.py tests/test_lifecycle_followthrough.py tests/test_p5b_lifecycle_action.py -q -ra
```

`full-local-final.xml/.log`：**41 pass，0 skip，exit 0**。两个原真实 host
`safe-api`/`preserve-api` 在最后一个 callback 异常处理补丁前为 **2 pass**；最终代码
`10e6fd08` 上重新跑真实 `safe-api` 为 **1 pass**，`real-host-final.xml/.log`。
这些集合有重叠，不应累加为不重复总数。原五项红、desktop 红及修前非阻塞红均保留。
测试专属输出改入 `evidence/cp-x1-strict-successors-20261029/`，未覆盖 a9 祖先证据。

MA 的 a9 integration 验证 `job_97a0142922bf40cbf185a450`：所报 73 项中 69 pass、
4 fail；七个原 host 目标与 62 个独立 executor 通过。四个 signal 参数因 genuine-main
夹具没有 `/__test/host-signal/{signal}` 而 404，是夹具/所选层不匹配，不能称产品 signal
失败或声称该层已测；原 XML 在
`D:/project/Pan/.workflow/job-unification-review-20261028/lifecycle-v2-final-ma-owned.xml`。

没有新 skip/xfail、push、远端 CI、main/practical、真实数据/profile、既有服务或
8767/8768 操作。Linux/macOS、最终组合、CP-X2 desktop 修复及远端 CI 均未验证。
