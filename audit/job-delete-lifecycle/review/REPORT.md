# Job 删除生命周期：MA 审查返修候选

本报告取代父候选 2686c8d4 的交付判断；保留父候选的原报告与 105 项证据，不把旧测试计入本次新增验证。

- 工作树：`D:/project/pan-worktrees/event-trigger-core-20261022`
- 分支：`feature/event-trigger-core-20261022`
- 返修基线：`2686c8d4c369058c96e77c89f7e4202e315100a1`，开始时 clean。
- 精确受测源码：`ede83d7034912b71591c9a1c502a43549ba9f445`。
- 后续证据提交不修改产品源码或测试。最终候选 SHA 见 Git HEAD / TA 交付消息。

## 修前复现

`baseline_plugin.py` 只从 Git 读取上述拒绝候选的原 `_terminate_job_processes` 函数体，在合成进程上执行，不运行旧服务或触及真实进程。

```powershell
python scripts/run_tests_isolated.py -- python -m pytest -p audit.job-delete-lifecycle.review.baseline_plugin tests/test_job_delete_safety.py -k "unobservable and (children_oserror or birth)" -o addopts= -q --junitxml=audit/job-delete-lifecycle/review/red-junit.xml
```

exit **1**，**2 failed / 11 deselected**：children 枚举 OSError 和成员出生时间不可读均错误返回 `confirmed=True`。红测 JUnit 已保留。另保留原 1 秒容差取消后暴露的真实 HTTP 浮点舍入口径失败、kernel Job 外 console helper 验证失败记录；未降低身份或退出断言。

## 产品修复与审查缺口闭环

| 审查缺口 | 当前实现和证据 |
| --- | --- |
| 枚举/身份不可读误作空树 | `process_cleanup.py` 在任何效果前捕获全部 root/后代，AccessDenied、OSError、消失成员、创建时间不可读全部向上传播，删除记录留 `cleanup_pending/cleanupError`。 |
| 按顺序 kill 子进程后才枚举 runner | 全部 root 和可观察成员先用 `ProcessIdentityHandle` 保留句柄。通过 raw FILETIME 确认 runner 后 DuplicateHandle 取得它已经持有的 Job Object；复用 `JobObjectGuard` 的无 breakaway、kill-on-close、成员/active 双查询以及整 Job 终止，不按 PID 快照冒充完整所有权。 |
| 新生任务后代/已退出 root | task 必须经保留句柄证明属于复制的同一 Job。后来创建的任务后代继承 Job 成员资格，由 `TerminateJobObject` 覆盖。缺 root/缺所有权/guard 查询失败不声明成功。guard 外观察到的 helper 不按名字跳过、不裸杀；保留身份并等到实际退出，否则保留记录。 |
| 1 秒出生身份容差 | 采用已有 `ProcessIdentity.matches` 同口径：raw FILETIME 优先、整数精确匹配；旧浮点出生时间只有零容差相等才通过。HTTP 暴露的 raw→Unix 浮点舍入差异通过优先 raw 身份解决，没有放宽容差。 |
| kill 拒绝/未知退出 | Job 终止错误、remaining、runner kill receipt 未确认、任一保留句柄 UNKNOWN/未退出均失败闭锁。只有同句柄 DEAD 和整 Job 空树证据齐备才 unlink。 |
| cleanup_pending 永久拒绝 | 不再无条件拒绝再次请求。同一 DELETE 是显式重新核对，串行执行。先持久 `cleanupMembers`、整 Job 确认后持久 `cleanupTreeEnded`；若 runner 回执不确定，保持记录。后续显式 DELETE 使用该 durable proof 核对成员，不重投任务/子树效果，可按原 raw 身份继续清理 runner。无请求时无新 worker/watcher/恢复重放。 |
| concurrent cancel 绕过清理 | cancel 在入口、持久接受点与效果后提交点均复检 cleanup_pending，不能把未确认删除覆盖为 cancelled 后绕过证明；三阶段交错均有负控。unknown/uncertain（含历史 UNKNOWN）进程记录也不能走普通 unlink。 |
| runner 写回/启动竞态 | controller spawn 与 task spawn 各有同 Job 专用 admission 锁，DELETE 在标记前与其协调。task 的实际创建与身份/Job handle 发布在同一 admission 内；清理中拒绝新 spawn。`runner_update` 保留清理状态，不覆盖成 running/terminal，缺记录拒绝，不重建 JSON。 |
| scheduled parent/child 独立锁 | 恢复保守原语义：活动父 dispatch 或非终态 child（包括 cleanup_pending）存在时拒删父配置。子任务创建仍经父锁核对，缺父或清理中的父禁止创建。终态 child 迟到回写/reconcile 不重建已删父。没有在本轮扩成级联删除调度器。 |
| 全局锁阻塞 | OS 枚举/等待只占同 Job 专用 mutex，不占 registry 全局 `_lock`；实际 task spawn 也不占全局锁等待。HTTP 删除用 `asyncio.to_thread`。屏障验证无关 Job 能持久化。 |

实现文件：`packages/core/background_jobs.py`、`background_runner.py`、`terminal/guard.py`、`packages/jobs/process_cleanup.py`、`packages/jobs/api.py`。没有新增 Job registry、调度器、外部服务、认证或 Locker 逻辑。

## 冻结源码验证

```powershell
python audit/job-delete-lifecycle/review/run_review.py
```

脚本记录精确解释器、完整命令、source SHA 和退出码于 `command.json`，输出 `final.log` 和 `final-junit.xml`。实际命令经 `scripts/run_tests_isolated.py` 运行：

```text
python -m pytest tests/test_job_delete_safety.py tests/test_background_jobs.py tests/test_jobs_api.py tests/test_job_delete_http_owned.py tests/test_terminal_guard.py tests/test_job_runner_venv.py -o addopts= -q --junitxml=audit/job-delete-lifecycle/review/final-junit.xml
```

结果：exit **0**，**146 passed / 0 skipped**，26.82 秒，5 个既有依赖/pytest 配置警告。范围为 29 项确定性 safety、2 项真实 HTTP/自有进程、105 项原相关 Job/API、6 项原 guard 和 4 项原 venv runner 回归，没有全仓测试。

- 确定性负控：children AccessDenied/枚举失败、成员 create_time 失败、kill 拒绝、starting 缺 PID、guard 新生成员残留、过期 root、retained UNKNOWN/DEAD 不冒充完整树、零容差/raw 优先。
- 并发/持久化：重复/并发 DELETE、runner 迟到 terminal 回写、清理后缺记录拒绝回写、新 spawn 拒绝、未完成清理显式再次核对、持久树证明下只处理原 runner、scheduled parent/child 迟到回写、无关 Job 不阻塞。
- 实际 HTTP 运行真实 `packages.jobs.api.router`，自有临时 JSON registry、自有 Windows background_runner/DesktopCommand/子进程，随机 loopback 端口。第二用例先注入不可观测，确认 Job/自有进程保留；新 Python 解释器调用实际 `reconcile_running` 冷读取确认 cleanup_pending 不重放，再移除故障显式 DELETE 成功。
- `http-owned.json`、`http-owned-recheck.json` 均为 `ownedProcessesRemaining=[] / portReleased=true`。测试 finally 只使用自有 receipt 捕获的 PID+出生身份清理。没有绑定/探测 8767/8768。
- `python -m py_compile` 覆盖上述五个产品文件，exit **0**，详见 `compile.json`；`git diff --check` exit **0**。

## 恢复操作与保留边界

1. GET `/api/jobs/{jobId}` 可见 `cleanup_pending`、`cleanupError`、`cleanupAttemptAt`、原 PID/出生身份和已持久证明；API DELETE 失败返回 `job_busy`，不发 `job.deleted`。
2. 可观测性恢复后，显式再次 DELETE 同一 ID；没有自动重试未知终止、没有重新执行任务。
3. 已持久完整树退出证明的中断清理可由新服务进程读取并显式继续。PID 重用、未知身份、缺失/失败的持久证明不能靠新的全局账号或当前 PID 倒填。
4. 没有 `processJobHandle` 的历史运行记录、`hideWindow=false` 的旧非 Job 所有权路径、非 Windows unguarded 路径不能证明不存在逃逸后代，继续保留，不能强删。本轮没有引入强制删除/手动“相信我”接口。此类记录需人工保留证据核对；如果历史根已消失且无完整树证明，本轮不能安全自动解决，明确作为安全边界保留。
5. 确认终态记录的旧删除语义保持；independent-job-service 的既有 unsupported gate 保持；scheduled 父配置的活动子任务拒删保持。
6. 重启验证是新解释器的生产恢复函数+隔离 API，不声称真实现场主服务重启或用户 Worker 行为通过。

main/practical、其他工作树、Auth/Locker/性能/CI 线未修改；无 push/release、真实账号/provider/QQ/OAuth/private exporter 操作。候选待 MA 独立验收，不自行宣称通过。
