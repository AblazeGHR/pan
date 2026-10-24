# 最新 main 的 Job 删除生命周期组合候选

- 整合树：`D:/project/pan-worktrees/job-delete-main-integration-20261024`
- 分支：`integration/job-delete-main-20261024`
- 精确起点/current main：`f03e7c652c45e207486d2ee33643b95ae915985b`。
- 精确受测源码：`a137ea1fc8c4ab0c3d4d7571fc6d22ad512e7b41`；证据提交不改产品或测试。
- 起点是候选祖先，具备仅移动 main 的 fast-forward 拓扑；本任务没有移动 main/practical。
- 原 core 树仍为 `1557ae41685ba535f3da947c608434efb9153b64` 且 clean，原证据未写入/覆盖。

## 精确引入列表

仅 cherry-pick 以下本任务五个提交，未 merge 旧 core 分支。全部自动合并，没有文本冲突；之后作语义组合审查。

| 原提交 | main 整合提交 | 内容 |
| --- | --- | --- |
| 2686c8d4 | 9b2ed940 | 首次删除门禁/初始回归和证据 |
| cbd207b1 | 01763cd4 | retained handle + JobObject 完整所有权、失败闭锁、pending 复核 |
| 7ebc269f | 0a45849f | concurrent cancel 不能越过删除证明 |
| ede83d70 | 581811b1 | unknown/uncertain 不直接 unlink |
| 1557ae41 | edb2c035 | 旧树修前/修后证据，作为历史保留 |

新组合源码提交 `a137ea1f` 仅修复下述终态观测问题并增加组合测试。相对 main 的产品改动精确限制在：

- `packages/core/background_jobs.py`
- `packages/core/background_runner.py`
- `packages/core/terminal/guard.py`
- `packages/jobs/api.py`
- `packages/jobs/process_cleanup.py`

没有从旧 core 导入 Action/Template/授权/Locker/Worker/session_error_log 的其他实现。原 main 的授权路径、runtime.finalize/ActionStore snapshot 清理、Run/Attempt 模型保留。

## 组合语义发现与最小修复

新增 `test_job_delete_main_combination.py` 在第一次运行时 **1 failed / 1 passed，exit 1**（`red.xml`）：cleanup_pending 下 runner 迟到回写 completed 虽没有改变 status/可执行 snapshot，但仍生成了 terminalEventId。新组合修复将其 status/exitCode/error/processTreeEnded 记为独立 `cleanupRunnerOutcome` 观测，不发布 terminalEventId/notificationState；不把观测升格为终态。

| 主线共存边界 | 实测结论 |
| --- | --- |
| System Action 实际 admission snapshot | cleanup_pending 失败和 runner 迟到终态回写后，attemptSnapshot、Run/Attempt 身份及 running 审计保持原样，snapshotCleanup 仍 active；无 lastExecution/terminalEventId 伪终态。 |
| 显式再核对成功删除 | 只在进程证明确认后 unlink；迟到 runner_update 报 not found，不重建 JSON。 |
| 授权 Action Job 删除 | 使用主线 UserAuth Bearer / action_api / Runtime.delete。无 token 401；active execution 在 cleanup_pending 下仍 409，ActionStore 可执行 snapshot digest 不变；旧 legacy DELETE 不能绕过 protected gate。 |
| 合法终态与 revision | 通过主线真实 finalizer 完成 uncertain 清理后，快照被清理；过期 revision DELETE 409；匹配 revision 的用户 DELETE 成功。没有改授权函数或 Runtime.delete。 |
| Agent 授权/撤销/MCP | 运行主线 permission/runtime/MCP 回归，包括可信 IPC 授权与真实 stdio 对象操作；没有凭 body 身份或 legacy API 放宽 Action Job 删除。 |
| 旧 scheduler / child | 原活动 dispatch/child 拒删、pending child 拒删、终态 child 迟到回写不重建父记录、创建后删除 admission 边界继续通过。 |
| 不可观测和 orphan 负控 | 原 children/create_time/kill/缺 PID/UNKNOWN/迟到成员/并发 cancel 和重复 DELETE 负控均在新主线通过。无可信 Job 所有权仍 fail closed。 |

## 冻结源码验证命令和结果

```powershell
python audit/job-delete-main-integration/run_combination.py
```

`command.json` 保存精确 Python 路径和参数；脚本仅经 `scripts/run_tests_isolated.py` 运行如下定向文件：

```text
tests/test_job_delete_main_combination.py
tests/test_job_delete_safety.py
tests/test_job_delete_http_owned.py
tests/test_background_jobs.py
tests/test_jobs_api.py
tests/test_job_system_actions.py
tests/test_job_action_templates.py
tests/test_authorization_permissions.py
tests/test_authorization_runtime.py
tests/test_authorization_mcp.py
tests/test_terminal_guard.py
tests/test_job_runner_venv.py
```

结果 **236 passed / 0 skipped，exit 0，40.42 秒**；5 个既有配置/依赖警告。不是全仓测试。JUnit `final-junit.xml`、UTF-8 日志 `final.log`。

真实 Windows 自有 runner/JobObject/任务/子进程 + 产品 legacy router 的随机 loopback HTTP 两条路径：正常确认删除、不可观测时保留后显式再核对。第二条还有新解释器调用生产 reconcile_running 验证冷恢复不重投。各自 receipt 在 `http-owned.json` / `http-owned-recheck.json`，均 `ownedProcessesRemaining=[]` / `portReleased=true`。授权 Action Job 使用生产 ASGI 路由/权限组件验证，未把 legacy 随机端口测试冒充完整授权主服务部署。

五个产品文件 `py_compile` exit 0（`compile.json`），`git diff --check` exit 0。`integration.json` 保存起点祖先证明、产品 allowlist、原树状态及 main/practical refs。

## 剩余边界与交接

- 无可信 JobObject 绑定的历史运行记录、旧非隐藏 unguarded 路径或根已消失且没有完整退出证明的记录仍保留 cleanup_pending；不增加强删、自动重投或推断所有权。
- 没有真实用户 Job/profile、provider/QQ/OAuth/private exporter、保护端口 8767/8768 或现场服务操作；无 push/release。
- 本轮未改 React，不跑 frontend build；没有执行主服务部署重启、用户现场迁移或全仓 CI。
- main/practical 在交付时分别仍是 f03e7c65 / 53dff07b；本候选可供 MA 独立验收并决定是否快进 main，不代表已获验收或已合入。
